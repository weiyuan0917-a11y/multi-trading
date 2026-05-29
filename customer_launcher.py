from __future__ import annotations

import ctypes
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path


API_PORT = int(os.getenv("LONGPORT_API_PORT", "8010") or "8010")
WEB_PORT = int(os.getenv("LONGPORT_WEB_PORT", "3010") or "3010")
MUTEX_NAME = "Global\\MultiTradingCustomerLauncher_SingleInstance_v1"
_mutex_handle = None


def _root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


ROOT = _root()
BACKEND_EXE = ROOT / "Backend.exe"
NODE_EXE = ROOT / "runtime" / "node" / "node.exe"
FRONTEND_DIR = ROOT / "frontend"
FRONTEND_SERVER = FRONTEND_DIR / "server.js"
BACKEND_LOG = ROOT / "launcher_backend.log"
FRONTEND_LOG = ROOT / "launcher_frontend.log"
CRASH_LOG = ROOT / "launcher_crash.log"


def _message(text: str, title: str = "MultiTrading") -> None:
    if os.name != "nt":
        print(text)
        return
    try:
        ctypes.windll.user32.MessageBoxW(None, str(text), str(title), 0x40)
    except Exception:
        print(text)


def _acquire_lock() -> bool:
    global _mutex_handle
    if os.name != "nt":
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, True, MUTEX_NAME)
        if handle and int(kernel32.GetLastError()) == 183:
            kernel32.CloseHandle(handle)
            return False
        _mutex_handle = handle
    except Exception:
        pass
    return True


def _is_port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((host, int(port))) == 0


def _http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= int(resp.status) < 500
    except Exception:
        return False


def _status_code(url: str, timeout: float = 2.0) -> int | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return int(resp.status)
    except Exception:
        return None


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("MT_BUILD_TARGET", "customer")
    env.setdefault("NEXT_PUBLIC_MT_BUILD_TARGET", "customer")
    env.setdefault("MULTITRADING_ROOT", str(ROOT))
    env.setdefault("LONGPORT_API_PORT", str(API_PORT))
    env.setdefault("LONGPORT_WEB_PORT", str(WEB_PORT))
    env.setdefault("LOCAL_AGENT_ALLOW_USER_OWNERS", "true")
    env.setdefault("NEXT_TELEMETRY_DISABLED", "1")
    return env


def _start_logged(cmd: list[str], cwd: Path, env: dict[str, str], log_path: Path) -> subprocess.Popen[bytes]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8", errors="replace") as f:
        f.write("\n" + "=" * 72 + "\n")
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] cwd={cwd}\ncmd={cmd!r}\n")
    log = open(log_path, "a", encoding="utf-8", errors="replace")
    flags = 0
    startupinfo = None
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=flags,
        startupinfo=startupinfo,
    )


def _wait(url: str, seconds: int) -> bool:
    for _ in range(max(1, int(seconds))):
        if _http_ok(url, timeout=3.0):
            return True
        time.sleep(1)
    return _http_ok(url, timeout=3.0)


def _check_files() -> str:
    missing: list[str] = []
    for path in (BACKEND_EXE, NODE_EXE, FRONTEND_SERVER):
        if not path.exists():
            missing.append(str(path))
    return "\n".join(missing)


def main() -> int:
    if not _acquire_lock():
        _message("MultiTrading 已在启动中或已运行。请稍候查看浏览器页面。")
        return 0

    missing = _check_files()
    if missing:
        _message(f"安装目录不完整，缺少以下文件：\n\n{missing}", "MultiTrading 启动失败")
        return 1

    env = _env()
    api_url = f"http://127.0.0.1:{API_PORT}/health"
    web_url = f"http://127.0.0.1:{WEB_PORT}"

    try:
        if not _http_ok(api_url, timeout=2.0):
            if _is_port_open(API_PORT):
                _message(f"后端端口 {API_PORT} 已被占用，但健康检查未通过。请先关闭占用该端口的程序。", "MultiTrading 启动失败")
                return 1
            _start_logged([str(BACKEND_EXE), f"--host=127.0.0.1", f"--port={API_PORT}"], ROOT, env, BACKEND_LOG)

        if not _wait(api_url, 90):
            _message(f"后端未能在限定时间内启动。请查看日志：\n{BACKEND_LOG}", "MultiTrading 启动失败")
            return 1

        if not _http_ok(web_url, timeout=2.0):
            if _is_port_open(WEB_PORT):
                _message(f"前端端口 {WEB_PORT} 已被占用，但页面不可用。请先关闭占用该端口的程序。", "MultiTrading 启动失败")
                return 1
            fe_env = dict(env)
            fe_env["PORT"] = str(WEB_PORT)
            fe_env["HOSTNAME"] = "127.0.0.1"
            fe_env.setdefault("NODE_ENV", "production")
            _start_logged([str(NODE_EXE), str(FRONTEND_SERVER)], FRONTEND_DIR, fe_env, FRONTEND_LOG)

        if not _wait(web_url, 120):
            _message(f"前端未能在限定时间内启动。请查看日志：\n{FRONTEND_LOG}", "MultiTrading 启动失败")
            return 1

        # Route smoke check. A 200/307/308 is acceptable for auth redirects.
        code = _status_code(f"{web_url}/auth?forceLogin=1", timeout=4.0)
        if code is not None and code >= 500:
            _message(f"前端已启动，但登录页返回异常状态 {code}。请查看日志：\n{FRONTEND_LOG}", "MultiTrading 启动警告")

        webbrowser.open(f"{web_url}/auth?forceLogin=1", new=2)
        return 0
    except Exception as exc:
        try:
            CRASH_LOG.write_text(
                json.dumps({"error": str(exc), "at": time.strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass
        _message(f"启动器异常：{exc}\n\n日志：{CRASH_LOG}", "MultiTrading 启动失败")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
