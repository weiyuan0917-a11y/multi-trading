package main

import (
	"archive/zip"
	"bytes"
	_ "embed"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"unsafe"
)

//go:embed payload.zip
var payload []byte

const appName = "MultiTrading"

func main() {
	installDir, err := defaultInstallDir()
	if err != nil {
		fail("Cannot resolve install directory: " + err.Error())
	}
	if err := os.MkdirAll(installDir, 0755); err != nil {
		fail("Cannot create install directory: " + err.Error())
	}
	if err := extractPayload(installDir); err != nil {
		fail("Install failed: " + err.Error())
	}

	launcher := filepath.Join(installDir, "MultiTradingLauncher.exe")
	if _, err := os.Stat(launcher); err != nil {
		fail("Launcher was not installed: " + launcher)
	}

	cmd := exec.Command(launcher)
	cmd.Dir = installDir
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	if err := cmd.Start(); err != nil {
		fail("Installed, but failed to launch MultiTrading: " + err.Error())
	}
}

func defaultInstallDir() (string, error) {
	base := os.Getenv("LOCALAPPDATA")
	if base == "" {
		var err error
		base, err = os.UserHomeDir()
		if err != nil {
			return "", err
		}
	}
	return filepath.Join(base, "Programs", appName), nil
}

func extractPayload(dest string) error {
	reader, err := zip.NewReader(bytes.NewReader(payload), int64(len(payload)))
	if err != nil {
		return err
	}
	cleanDest, err := filepath.Abs(dest)
	if err != nil {
		return err
	}
	cleanDest = filepath.Clean(cleanDest)
	for _, file := range reader.File {
		target := filepath.Join(cleanDest, file.Name)
		cleanTarget := filepath.Clean(target)
		if cleanTarget != cleanDest && !strings.HasPrefix(cleanTarget, cleanDest+string(os.PathSeparator)) {
			return fmt.Errorf("invalid archive path: %s", file.Name)
		}
		if file.FileInfo().IsDir() {
			if err := os.MkdirAll(cleanTarget, file.Mode()); err != nil {
				return err
			}
			continue
		}
		if err := os.MkdirAll(filepath.Dir(cleanTarget), 0755); err != nil {
			return err
		}
		src, err := file.Open()
		if err != nil {
			return err
		}
		dst, err := os.OpenFile(cleanTarget, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, file.Mode())
		if err != nil {
			_ = src.Close()
			return err
		}
		_, copyErr := io.Copy(dst, src)
		closeErr := dst.Close()
		_ = src.Close()
		if copyErr != nil {
			return copyErr
		}
		if closeErr != nil {
			return closeErr
		}
	}
	return nil
}

func fail(message string) {
	logPath := filepath.Join(os.TempDir(), "MultiTradingSetup.log")
	_ = os.WriteFile(logPath, []byte(message+"\n"), 0644)
	messageBox("MultiTrading Setup", message)
	os.Exit(1)
}

func messageBox(title string, message string) {
	user32 := syscall.NewLazyDLL("user32.dll")
	proc := user32.NewProc("MessageBoxW")
	titlePtr, _ := syscall.UTF16PtrFromString(title)
	messagePtr, _ := syscall.UTF16PtrFromString(message)
	proc.Call(0, uintptr(unsafe.Pointer(messagePtr)), uintptr(unsafe.Pointer(titlePtr)), 0x10)
}
