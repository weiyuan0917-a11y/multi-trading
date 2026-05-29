package main

import (
	"bufio"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"time"
	"unsafe"
)

const (
	apiPort = "8010"
	webPort = "3010"
)

func main() {
	exePath, err := os.Executable()
	if err != nil {
		fail("Cannot locate launcher path: " + err.Error())
	}
	root := filepath.Dir(exePath)

	backend := filepath.Join(root, "Backend.exe")
	node := filepath.Join(root, "runtime", "node", "node.exe")
	frontendDir := filepath.Join(root, "frontend")
	server := filepath.Join(frontendDir, "server.js")
	runtimeDir := customerRuntimeDir()
	logDir := filepath.Join(runtimeDir, "logs")
	backendLog := filepath.Join(logDir, "launcher_backend.log")
	frontendLog := filepath.Join(logDir, "launcher_frontend.log")

	var missing []string
	for _, p := range []string{backend, node, server} {
		if _, err := os.Stat(p); err != nil {
			missing = append(missing, p)
		}
	}
	if len(missing) > 0 {
		fail("The installation directory is incomplete. Missing files:\n\n" + strings.Join(missing, "\n"))
	}

	env := customerEnv(root)
	if !httpOK("http://127.0.0.1:"+apiPort+"/health", 2*time.Second) {
		if portOpen(apiPort) {
			fail("Backend port " + apiPort + " is already in use, but the health check failed. Close the process using this port and try again.")
		}
		if err := startLogged(backend, []string{"--host=127.0.0.1", "--port=" + apiPort}, root, env, backendLog); err != nil {
			fail("Failed to start backend: " + err.Error())
		}
	}
	if !waitHTTP("http://127.0.0.1:"+apiPort+"/health", 90*time.Second) {
		fail("Backend did not start in time. Check log:\n" + backendLog)
	}

	if !httpOK("http://127.0.0.1:"+webPort, 2*time.Second) {
		if portOpen(webPort) {
			fail("Frontend port " + webPort + " is already in use, but the page is not available. Close the process using this port and try again.")
		}
		feEnv := append([]string{}, env...)
		feEnv = append(feEnv, "PORT="+webPort, "HOSTNAME=127.0.0.1", "NODE_ENV=production")
		if err := startLogged(node, []string{server}, frontendDir, feEnv, frontendLog); err != nil {
			fail("Failed to start frontend: " + err.Error())
		}
	}
	if !waitHTTP("http://127.0.0.1:"+webPort, 120*time.Second) {
		fail("Frontend did not start in time. Check log:\n" + frontendLog)
	}

	_ = exec.Command("rundll32", "url.dll,FileProtocolHandler", "http://127.0.0.1:"+webPort+"/auth?forceLogin=1").Start()
}

func customerEnv(root string) []string {
	env := os.Environ()
	env = appendDotEnv(env, filepath.Join(root, ".env"))
	env = setEnv(env, "MT_BUILD_TARGET", "customer")
	env = setEnv(env, "NEXT_PUBLIC_MT_BUILD_TARGET", "customer")
	env = setEnv(env, "MULTITRADING_ROOT", root)
	env = setEnv(env, "MULTITRADING_RUNTIME_DIR", customerRuntimeDir())
	env = setEnv(env, "MULTITRADING_LOG_DIR", filepath.Join(customerRuntimeDir(), "logs"))
	env = setEnv(env, "LONGPORT_API_PORT", apiPort)
	env = setEnv(env, "LONGPORT_WEB_PORT", webPort)
	env = setEnv(env, "LOCAL_AGENT_ALLOW_USER_OWNERS", "true")
	env = setEnv(env, "NEXT_TELEMETRY_DISABLED", "1")
	return env
}

func customerRuntimeDir() string {
	base := os.Getenv("LOCALAPPDATA")
	if strings.TrimSpace(base) == "" {
		if home, err := os.UserHomeDir(); err == nil {
			base = home
		} else {
			base = os.TempDir()
		}
	}
	dir := filepath.Join(base, "MultiTrading")
	_ = os.MkdirAll(filepath.Join(dir, "logs"), 0755)
	return dir
}

func appendDotEnv(env []string, path string) []string {
	file, err := os.Open(path)
	if err != nil {
		return env
	}
	defer file.Close()

	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		key, value, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		key = strings.TrimSpace(key)
		if key == "" || strings.ContainsAny(key, " \t\r\n") {
			continue
		}
		value = strings.TrimSpace(value)
		if len(value) >= 2 {
			first := value[0]
			last := value[len(value)-1]
			if (first == '"' && last == '"') || (first == '\'' && last == '\'') {
				value = value[1 : len(value)-1]
			}
		}
		env = setEnv(env, key, value)
	}
	return env
}

func setEnv(env []string, key, value string) []string {
	prefix := key + "="
	for i, item := range env {
		if strings.HasPrefix(strings.ToUpper(item), strings.ToUpper(prefix)) {
			env[i] = prefix + value
			return env
		}
	}
	return append(env, prefix+value)
}

func startLogged(exe string, args []string, cwd string, env []string, logPath string) error {
	_ = os.MkdirAll(filepath.Dir(logPath), 0755)
	f, err := os.OpenFile(logPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0644)
	if err != nil {
		return err
	}
	_, _ = fmt.Fprintf(f, "\n%s\n[%s] cwd=%s\ncmd=%s %s\n", strings.Repeat("=", 72), time.Now().Format("2006-01-02 15:04:05"), cwd, exe, strings.Join(args, " "))
	cmd := exec.Command(exe, args...)
	cmd.Dir = cwd
	cmd.Env = env
	cmd.Stdout = f
	cmd.Stderr = f
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: syscall.CREATE_NEW_PROCESS_GROUP}
	return cmd.Start()
}

func httpOK(url string, timeout time.Duration) bool {
	client := http.Client{Timeout: timeout}
	resp, err := client.Get(url)
	if err != nil {
		return false
	}
	_, _ = io.Copy(io.Discard, resp.Body)
	_ = resp.Body.Close()
	return resp.StatusCode >= 200 && resp.StatusCode < 500
}

func waitHTTP(url string, timeout time.Duration) bool {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if httpOK(url, 3*time.Second) {
			return true
		}
		time.Sleep(time.Second)
	}
	return httpOK(url, 3*time.Second)
}

func portOpen(port string) bool {
	conn, err := net.DialTimeout("tcp", "127.0.0.1:"+port, 450*time.Millisecond)
	if err != nil {
		return false
	}
	_ = conn.Close()
	return true
}

func fail(msg string) {
	_, _ = fmt.Fprintln(os.Stderr, msg)
	messageBox("MultiTrading startup failed", msg)
	os.Exit(1)
}

func messageBox(title string, message string) {
	user32 := syscall.NewLazyDLL("user32.dll")
	proc := user32.NewProc("MessageBoxW")
	titlePtr, _ := syscall.UTF16PtrFromString(title)
	messagePtr, _ := syscall.UTF16PtrFromString(message)
	proc.Call(0, uintptr(unsafe.Pointer(messagePtr)), uintptr(unsafe.Pointer(titlePtr)), 0x10)
}
