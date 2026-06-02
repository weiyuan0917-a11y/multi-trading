package main

import (
	"bufio"
	"crypto/rand"
	"encoding/hex"
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
	apiPort           = "8010"
	webPort           = "3010"
	launcherMutexName = "Global\\MultiTradingCustomerLauncher_SingleInstance_v2"
)

func main() {
	mutexHandle, mutexAcquired := acquireLauncherMutex()
	_ = mutexHandle
	if !mutexAcquired {
		messageBox("MultiTrading", "MultiTrading is already running. Please use the existing app window.")
		return
	}

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
	launcherLog := filepath.Join(logDir, "launcher_lifecycle.log")

	var missing []string
	for _, p := range []string{backend, node, server} {
		if _, err := os.Stat(p); err != nil {
			missing = append(missing, p)
		}
	}
	if len(missing) > 0 {
		fail("The installation directory is incomplete. Missing files:\n\n" + strings.Join(missing, "\n"))
	}

	shutdownToken := randomToken()
	profileSuffix := shutdownToken
	if len(profileSuffix) > 16 {
		profileSuffix = profileSuffix[:16]
	}
	browserProfileDir := filepath.Join(runtimeDir, "browser-profile", profileSuffix)
	defer os.RemoveAll(browserProfileDir)
	writeShutdownToken(runtimeDir, shutdownToken, launcherLog)
	env := customerEnv(root)
	env = setEnv(env, "MULTITRADING_LAUNCHER_SHUTDOWN_TOKEN", shutdownToken)
	var backendCmd *exec.Cmd
	var frontendCmd *exec.Cmd
	if !httpOK("http://127.0.0.1:"+apiPort+"/health", 2*time.Second) {
		if portOpen(apiPort) {
			fail("Backend port " + apiPort + " is already in use, but the health check failed. Close the process using this port and try again.")
		}
		cmd, err := startLogged(backend, []string{"--host=127.0.0.1", "--port=" + apiPort}, root, env, backendLog)
		if err != nil {
			fail("Failed to start backend: " + err.Error())
		}
		backendCmd = cmd
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
		cmd, err := startLogged(node, []string{server}, frontendDir, feEnv, frontendLog)
		if err != nil {
			fail("Failed to start frontend: " + err.Error())
		}
		frontendCmd = cmd
	}
	if !waitHTTP("http://127.0.0.1:"+webPort, 120*time.Second) {
		fail("Frontend did not start in time. Check log:\n" + frontendLog)
	}

	targetURL := "http://127.0.0.1:" + webPort + "/auth?forceLogin=1"
	browserCmd, via, err := openWebUI(targetURL, browserProfileDir)
	if err != nil {
		messageBox("MultiTrading", "MultiTrading is running, but the launcher could not open the app window automatically.\n\nPlease open:\n"+targetURL)
		return
	}
	appendLifecycleLog(launcherLog, "opened UI via "+via)
	if browserCmd == nil {
		appendLifecycleLog(launcherLog, "UI was opened without a monitorable app window; backend will stay running.")
		return
	}
	waitForUIThenShutdown(browserCmd, shutdownToken, backendCmd, frontendCmd, launcherLog)
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

func startLogged(exe string, args []string, cwd string, env []string, logPath string) (*exec.Cmd, error) {
	_ = os.MkdirAll(filepath.Dir(logPath), 0755)
	f, err := os.OpenFile(logPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0644)
	if err != nil {
		return nil, err
	}
	_, _ = fmt.Fprintf(f, "\n%s\n[%s] cwd=%s\ncmd=%s %s\n", strings.Repeat("=", 72), time.Now().Format("2006-01-02 15:04:05"), cwd, exe, strings.Join(args, " "))
	cmd := exec.Command(exe, args...)
	cmd.Dir = cwd
	cmd.Env = env
	cmd.Stdout = f
	cmd.Stderr = f
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: syscall.CREATE_NEW_PROCESS_GROUP}
	if err := cmd.Start(); err != nil {
		_ = f.Close()
		return nil, err
	}
	_ = f.Close()
	return cmd, nil
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

func openWebUI(url string, profileDir string) (*exec.Cmd, string, error) {
	if strings.TrimSpace(os.Getenv("MULTITRADING_BROWSER_APP_MODE")) != "0" {
		var lastErr error
		for _, browser := range browserAppModeCandidates() {
			_ = os.MkdirAll(profileDir, 0755)
			cmd := exec.Command(
				browser,
				"--app="+url,
				"--user-data-dir="+profileDir,
				"--no-first-run",
				"--disable-default-apps",
				"--disable-background-mode",
				"--disable-features=CalculateNativeWinOcclusion",
			)
			cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
			if err := cmd.Start(); err == nil {
				return cmd, "app mode: " + filepath.Base(browser), nil
			} else {
				lastErr = err
			}
		}
		if lastErr != nil {
			_ = lastErr
		}
	}
	if err := exec.Command("rundll32", "url.dll,FileProtocolHandler", url).Start(); err != nil {
		return nil, "rundll32", err
	}
	return nil, "rundll32", nil
}

func acquireLauncherMutex() (uintptr, bool) {
	if os.Getenv("MULTITRADING_DISABLE_LAUNCHER_MUTEX") == "1" {
		return 0, true
	}
	kernel32 := syscall.NewLazyDLL("kernel32.dll")
	createMutex := kernel32.NewProc("CreateMutexW")
	namePtr, _ := syscall.UTF16PtrFromString(launcherMutexName)
	handle, _, lastErr := createMutex.Call(0, 1, uintptr(unsafe.Pointer(namePtr)))
	if handle == 0 {
		return 0, true
	}
	if lastErr == syscall.ERROR_ALREADY_EXISTS {
		kernel32.NewProc("CloseHandle").Call(handle)
		return 0, false
	}
	return handle, true
}

func randomToken() string {
	buf := make([]byte, 32)
	if _, err := rand.Read(buf); err == nil {
		return hex.EncodeToString(buf)
	}
	return fmt.Sprintf("%d", time.Now().UnixNano())
}

func writeShutdownToken(runtimeDir string, token string, logPath string) {
	path := filepath.Join(runtimeDir, "launcher_shutdown.token")
	if err := os.WriteFile(path, []byte(token+"\n"), 0600); err != nil {
		appendLifecycleLog(logPath, "failed to write launcher shutdown token: "+err.Error())
	}
}

func waitForUIThenShutdown(browserCmd *exec.Cmd, shutdownToken string, backendCmd *exec.Cmd, frontendCmd *exec.Cmd, logPath string) {
	_ = browserCmd.Wait()
	appendLifecycleLog(logPath, "app window exited; requesting backend shutdown")
	if requestLauncherShutdown(shutdownToken) {
		appendLifecycleLog(logPath, "backend accepted launcher shutdown request")
		if waitPortClosed(apiPort, 15*time.Second) {
			return
		}
		appendLifecycleLog(logPath, "backend port still open after graceful shutdown timeout")
	} else {
		appendLifecycleLog(logPath, "launcher shutdown request failed; using process fallback")
	}
	if frontendCmd != nil && frontendCmd.Process != nil {
		killProcessTree(frontendCmd.Process.Pid)
	}
	if backendCmd != nil && backendCmd.Process != nil {
		killProcessTree(backendCmd.Process.Pid)
	}
}

func requestLauncherShutdown(token string) bool {
	body := strings.NewReader(`{"stop_backend":true,"stop_frontend":true,"stop_feishu_bot":true,"stop_auto_trader":true,"stop_qqq_0dte_live":true,"stop_qqq_1dte_live":true,"stop_stock_options_swing":true}`)
	req, err := http.NewRequest("POST", "http://127.0.0.1:"+apiPort+"/internal/launcher/shutdown", body)
	if err != nil {
		return false
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-MT-Launcher-Token", token)
	client := http.Client{Timeout: 8 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return false
	}
	_, _ = io.Copy(io.Discard, resp.Body)
	_ = resp.Body.Close()
	return resp.StatusCode >= 200 && resp.StatusCode < 300
}

func waitPortClosed(port string, timeout time.Duration) bool {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if !portOpen(port) {
			return true
		}
		time.Sleep(500 * time.Millisecond)
	}
	return !portOpen(port)
}

func killProcessTree(pid int) {
	if pid <= 0 {
		return
	}
	cmd := exec.Command("taskkill", "/PID", fmt.Sprintf("%d", pid), "/T", "/F")
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	_ = cmd.Run()
}

func appendLifecycleLog(path string, message string) {
	_ = os.MkdirAll(filepath.Dir(path), 0755)
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0644)
	if err != nil {
		return
	}
	defer f.Close()
	_, _ = fmt.Fprintf(f, "[%s] %s\n", time.Now().Format("2006-01-02 15:04:05"), message)
}

func browserAppModeCandidates() []string {
	seen := map[string]bool{}
	out := []string{}
	add := func(path string) {
		path = strings.TrimSpace(path)
		if path == "" {
			return
		}
		clean := filepath.Clean(path)
		key := strings.ToLower(clean)
		if seen[key] {
			return
		}
		if _, err := os.Stat(clean); err != nil {
			return
		}
		seen[key] = true
		out = append(out, clean)
	}
	add(os.Getenv("MULTITRADING_BROWSER_EXE"))
	for _, envKey := range []string{"ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"} {
		base := strings.TrimSpace(os.Getenv(envKey))
		if base == "" {
			continue
		}
		add(filepath.Join(base, "Microsoft", "Edge", "Application", "msedge.exe"))
		add(filepath.Join(base, "Google", "Chrome", "Application", "chrome.exe"))
	}
	for _, name := range []string{"msedge.exe", "chrome.exe"} {
		if path, err := exec.LookPath(name); err == nil {
			add(path)
		}
	}
	return out
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
