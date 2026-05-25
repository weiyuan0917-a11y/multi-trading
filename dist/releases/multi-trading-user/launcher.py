from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import json
import atexit
import ctypes
import hashlib
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import traceback
from pathlib import Path

from config.env_loader import parse_env_file


WEB_PORT = int(str(os.getenv("LONGPORT_WEB_PORT", "3010") or "3010"))
OPENBB_DEFAULT_PORT = 6900
REQUIRED_API_PATHS = {
    "/options/expiries": "get",
    "/options/chain": "get",
    "/options/backtest": "post",
    "/backtest/strategies": "get",
    "/setup/services/stop-all": "post",
}
REQUIRED_WEB_ROUTES = ["/", "/setup", "/options", "/trade", "/backtest", "/strategy/qqq-0dte"]
_INSTANCE_MUTEX_NAME = "Global\\MultiTradingLauncher_SingleInstance_v1"
_WATCHDOG_MUTEX_NAME = "Global\\MultiTradingLauncher_BackendWatchdog_v1"
_instance_mutex_handle = None
_instance_lock_file: Path | None = None
_watchdog_mutex_handle = None


def _win_try_utf8_console() -> None:
    """减轻双击运行时控制台中文乱码（Windows）。"""
    if os.name != "nt":
        return
    try:
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass


def _win_message_box(
    text: str,
    title: str = "MultiTradingLauncher",
    *,
    icon_info: bool = True,
    icon_warning: bool = False,
) -> None:
    """冻结 exe 下用于替代「一闪而过」控制台提示。"""
    if os.name != "nt":
        return
    if icon_warning:
        flags = 0x30  # MB_ICONWARNING
    elif icon_info:
        flags = 0x40  # MB_ICONINFORMATION
    else:
        flags = 0x10  # MB_ICONERROR
    try:
        ctypes.windll.user32.MessageBoxW(None, str(text), str(title), flags)
    except Exception:
        pass


def _flush_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream and hasattr(stream, "flush"):
                stream.flush()
        except Exception:
            pass


def _resolve_root() -> Path:
    # Running as script: project root is file directory.
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parent

    # Running as PyInstaller EXE: executable usually lives in <root>/dist.
    exe_dir = Path(sys.executable).resolve().parent
    candidates = [exe_dir, exe_dir.parent]
    for c in candidates:
        if (c / "frontend").exists() and (c / "api").exists():
            return c
    return exe_dir


ROOT = _resolve_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if getattr(sys, "frozen", False):
    _win_try_utf8_console()

LAUNCHER_CRASH_LOG_FILE = ROOT / "launcher_crash.log"
LAUNCHER_FRONTEND_LOG_FILE = ROOT / "launcher_frontend.log"
LAUNCHER_BACKEND_LOG_FILE = ROOT / "launcher_backend.log"

AUTO_TRADER_WORKER_PID_FILE = ROOT / ".auto_trader_worker.pid"
AUTO_TRADER_SUPERVISOR_PID_FILE = ROOT / ".auto_trader_supervisor.pid"
QQQ_0DTE_LIVE_WORKER_PID_FILE = ROOT / ".qqq_0dte_live_worker.pid"
QQQ_1DTE_LIVE_WORKER_PID_FILE = ROOT / ".qqq_1dte_live_worker.pid"

from backend_uvicorn_spec import DEFAULT_API_PORT, LAUNCHER_UVICORN_HOST, build_uvicorn_argv
from runtime_process_utils import is_pid_alive as _is_pid_alive
from runtime_process_utils import read_pid_file as _read_pid_file

API_PORT = int(str(os.getenv("LONGPORT_API_PORT", "8010") or "8010"))
FRONTEND_DIR = ROOT / "frontend"
WATCHDOG_PID_FILE = ROOT / ".backend_watchdog.pid"
WATCHDOG_PAUSE_FILE = ROOT / ".backend_watchdog.pause"
WATCHDOG_LOG_FILE = ROOT / "launcher_watchdog.log"
WATCHDOG_BUSY_FILE = ROOT / ".backend_watchdog.busy"
WATCHDOG_HEALTH_TIMEOUT_SECONDS = max(2.0, float(os.getenv("LONGPORT_WATCHDOG_HEALTH_TIMEOUT", "6.0")))
WATCHDOG_CONFIRM_TIMEOUT_SECONDS = max(
    WATCHDOG_HEALTH_TIMEOUT_SECONDS,
    float(os.getenv("LONGPORT_WATCHDOG_CONFIRM_TIMEOUT", "20.0")),
)
WATCHDOG_FAILS_BEFORE_RESTART = max(6, int(os.getenv("LONGPORT_WATCHDOG_FAILS_BEFORE_RESTART", "24")))
WATCHDOG_HEALTHY_SLEEP_SECONDS = 5
WATCHDOG_UNHEALTHY_SLEEP_SECONDS = max(3, int(os.getenv("LONGPORT_WATCHDOG_UNHEALTHY_SLEEP", "6")))
WATCHDOG_BUSY_TTL_SECONDS = max(
    20 * 60, int(os.getenv("LONGPORT_WATCHDOG_BUSY_TTL_SECONDS", "10800"))
)
WATCHDOG_RESTART_COOLDOWN_SECONDS = max(60, int(os.getenv("LONGPORT_WATCHDOG_RESTART_COOLDOWN", "300")))
WATCHDOG_STARTUP_GRACE_SECONDS = max(20, int(os.getenv("LONGPORT_WATCHDOG_STARTUP_GRACE", "90")))


def _is_port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


def _swallow_http_errors(exc: BaseException) -> bool:
    """是否吞掉该异常（健康探测不应让启动器崩溃）。含 ExceptionGroup（Py3.11+），无法用 except Exception 捕获。"""
    return not isinstance(exc, (KeyboardInterrupt, SystemExit, GeneratorExit))


def _is_http_healthy(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= int(resp.status) < 500
    except BaseException as e:
        if not _swallow_http_errors(e):
            raise
        return False


def _open_web_ui(url: str) -> tuple[bool, str]:
    """Try multiple browser launch strategies for double-click EXE mode."""
    try:
        if webbrowser.open(url, new=2):
            return True, "webbrowser.open"
    except Exception as e:
        last_err = f"webbrowser.open failed: {e}"
    else:
        last_err = "webbrowser.open returned False"

    if os.name == "nt":
        try:
            os.startfile(url)  # type: ignore[attr-defined]
            return True, "os.startfile"
        except Exception as e:
            last_err = f"os.startfile failed: {e}"
        try:
            flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
            subprocess.Popen(["cmd", "/c", "start", "", url], creationflags=flags)  # noqa: S603
            return True, "cmd start"
        except Exception as e:
            last_err = f"cmd start failed: {e}"

    return False, last_err


def _to_bool(value: str, default: bool = False) -> bool:
    raw = str(value or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def _windows_prepend_path_entries() -> list[str]:
    """
    资源管理器双击 .exe 启动时，进程继承的 PATH 往往不含 Node/Python 安装目录，
    shutil.which 会找不到 npm/python。此处收集常见安装路径并置于 PATH 最前。
    """
    extras: list[str] = []
    seen_norm: set[str] = set()

    def consider(path: Path) -> None:
        try:
            if not path.is_dir():
                return
            key = os.path.normcase(os.path.normpath(str(path.resolve())))
            if key in seen_norm:
                return
            seen_norm.add(key)
            extras.append(str(path))
        except OSError:
            return

    if os.name != "nt":
        return extras

    for env_key in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env_key, "").strip()
        if not base:
            continue
        nodejs = Path(base) / "nodejs"
        if (nodejs / "npm.cmd").exists() or (nodejs / "node.exe").exists():
            consider(nodejs)

    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        volta_bin = Path(local) / "Volta" / "bin"
        if volta_bin.is_dir() and (
            (volta_bin / "node.exe").exists() or (volta_bin / "npm.cmd").exists()
        ):
            consider(volta_bin)
        py_root = Path(local) / "Programs" / "Python"
        if py_root.is_dir():
            try:
                for child in sorted(py_root.iterdir()):
                    if not child.is_dir():
                        continue
                    if (child / "python.exe").is_file():
                        consider(child)
                        scripts = child / "Scripts"
                        if scripts.is_dir():
                            consider(scripts)
            except OSError:
                pass

    for env_key in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env_key, "").strip()
        if not base:
            continue
        try:
            for child in Path(base).iterdir():
                if not child.is_dir():
                    continue
                if not child.name.lower().startswith("python"):
                    continue
                if (child / "python.exe").is_file():
                    consider(child)
                    scripts = child / "Scripts"
                    if scripts.is_dir():
                        consider(scripts)
        except OSError:
            pass

    return extras


def _prepend_path_env(env: dict[str, str], extra_dirs: list[str]) -> None:
    if not extra_dirs:
        return
    sep = ";" if os.name == "nt" else ":"
    prev = env.get("PATH", "") or ""
    env["PATH"] = sep.join(extra_dirs) + sep + prev


def _augment_path_for_gui_launch(env: dict[str, str]) -> None:
    if os.name != "nt":
        return
    extras = _windows_prepend_path_entries()
    if not extras:
        return
    _prepend_path_env(env, extras)
    os.environ["PATH"] = env["PATH"]
    preview = "; ".join(extras[:4])
    if len(extras) > 4:
        preview += "; ..."
    print(f"[INFO] 已向前追加 PATH（解决双击启动找不到 npm/Python）: {preview}")


def _http_get_json(url: str, timeout: float = 3.0) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            if int(resp.status) != 200:
                return None
            raw = resp.read().decode("utf-8", errors="ignore")
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
    except BaseException as e:
        if not _swallow_http_errors(e):
            raise
        return None


def _http_post_json(url: str, payload: dict, timeout: float = 3.0) -> dict | None:
    try:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if int(resp.status) != 200:
                return None
            raw = resp.read().decode("utf-8", errors="ignore")
            parsed = json.loads(raw) if raw else {}
            return parsed if isinstance(parsed, dict) else None
    except BaseException as e:
        if not _swallow_http_errors(e):
            raise
        return None


def _http_status_code(url: str, timeout: float = 2.0) -> int | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as e:
        return int(getattr(e, "code", 0) or 0)
    except BaseException as e:
        if not _swallow_http_errors(e):
            raise
        return None


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _stop_auto_trader_worker_via_api() -> bool:
    """
    尝试通过 API 优雅停止 auto_trader worker/supervisor 与 QQQ 实盘 Worker（0DTE / 1DTE）：
    - 走 /setup/services/stop（stop_feishu_bot=false, stop_auto_trader=true, stop_qqq_0dte_live=true, stop_qqq_1dte_live=true）
    - 成功则返回 True，否则失败返回 False
    """
    stop_url = f"http://127.0.0.1:{API_PORT}/setup/services/stop"
    payload = {
        "stop_feishu_bot": False,
        "stop_auto_trader": True,
        "stop_qqq_0dte_live": True,
        "stop_qqq_1dte_live": True,
    }
    res = _http_post_json(stop_url, payload, timeout=3.0)
    if not isinstance(res, dict):
        return False
    # API 返回结构为 {"ok": True, "stopped": {...}}
    return bool(res.get("ok") is True)


def _stop_auto_trader_worker_via_pid_files() -> None:
    """
    API 不可达时的兜底：
    - 读取 .auto_trader_worker.pid / .auto_trader_supervisor.pid / .qqq_0dte_live_worker.pid / .qqq_1dte_live_worker.pid
    - 若进程存活则 taskkill
    """
    worker_pid = _read_pid_file(AUTO_TRADER_WORKER_PID_FILE)
    supervisor_pid = _read_pid_file(AUTO_TRADER_SUPERVISOR_PID_FILE)
    qqq_pid = _read_pid_file(QQQ_0DTE_LIVE_WORKER_PID_FILE)
    qqq1_pid = _read_pid_file(QQQ_1DTE_LIVE_WORKER_PID_FILE)
    pids: list[int] = []
    if worker_pid and _is_pid_alive(worker_pid):
        pids.append(int(worker_pid))
    if supervisor_pid and _is_pid_alive(supervisor_pid):
        pid_i = int(supervisor_pid)
        if pid_i not in pids:
            pids.append(pid_i)
    if qqq_pid and _is_pid_alive(qqq_pid):
        q = int(qqq_pid)
        if q not in pids:
            pids.append(q)
    if qqq1_pid and _is_pid_alive(qqq1_pid):
        q1 = int(qqq1_pid)
        if q1 not in pids:
            pids.append(q1)
    if pids:
        _kill_pids(pids)


def _stop_auto_trader_before_backend_restart() -> None:
    # 优先 API 优雅停机；失败则用 pid 文件硬停，避免旧 worker 继续扫旧市场。
    if _stop_auto_trader_worker_via_api():
        time.sleep(1.0)
        return
    _stop_auto_trader_worker_via_pid_files()
    time.sleep(1.0)


def _frontend_source_hash() -> str:
    """
    计算前端关键源码哈希，作为“页面版本标记”。
    覆盖 app/components/lib + 关键配置文件，确保任意页面变更都可被感知。
    """
    roots = [FRONTEND_DIR / "app", FRONTEND_DIR / "components", FRONTEND_DIR / "lib"]
    exts = {".ts", ".tsx", ".js", ".jsx", ".css", ".json"}
    files: list[Path] = []
    for r in roots:
        if not r.exists():
            continue
        for p in r.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() in exts:
                files.append(p)
    for p in [
        FRONTEND_DIR / "package.json",
        FRONTEND_DIR / "next.config.js",
        FRONTEND_DIR / "next.config.mjs",
        FRONTEND_DIR / "tsconfig.json",
    ]:
        if p.exists() and p.is_file():
            files.append(p)
    files = sorted(set(files), key=lambda x: str(x).lower())

    h = hashlib.sha256()
    for p in files:
        rel = str(p.relative_to(FRONTEND_DIR)).replace("\\", "/")
        h.update(rel.encode("utf-8", errors="ignore"))
        try:
            content = p.read_bytes()
        except Exception:
            content = b""
        h.update(_sha256_bytes(content).encode("ascii"))
    return h.hexdigest()


def _frontend_hash_marker_path() -> Path:
    return FRONTEND_DIR / ".next" / "launcher_frontend_hash.txt"


def _read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _write_text_file(path: Path, content: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except Exception:
        pass


def _frontend_build_ready() -> bool:
    return (FRONTEND_DIR / ".next" / "BUILD_ID").exists()


def _frontend_build_hash_matches(expected_hash: str) -> bool:
    marker = _frontend_hash_marker_path()
    if not marker.exists():
        return False
    return _read_text_file(marker) == expected_hash


def _api_spec_has_required_paths(spec: dict | None) -> bool:
    if not isinstance(spec, dict):
        return False
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return False
    for path, method in REQUIRED_API_PATHS.items():
        item = paths.get(path)
        if not isinstance(item, dict):
            return False
        if method and str(method).lower() not in {str(k).lower() for k in item.keys()}:
            return False
    return True


def _describe_missing_required_paths(spec: dict | None) -> str:
    """供启动失败时在控制台打印：哪些必选路由与 launcher 期望不一致。"""
    if not isinstance(spec, dict):
        return "未能获取 OpenAPI（后端未监听、崩溃或返回非 JSON）。"
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return "OpenAPI JSON 中缺少 paths 字段。"
    lines: list[str] = []
    for path, method in REQUIRED_API_PATHS.items():
        item = paths.get(path)
        if not isinstance(item, dict):
            lines.append(f"  - {path}  [{method}]：路由不存在")
            continue
        methods = {str(k).lower() for k in item.keys()}
        if method and str(method).lower() not in methods:
            lines.append(f"  - {path}：缺少 HTTP {method.upper()}，当前仅有 {sorted(methods)}")
    return "\n".join(lines) if lines else "必选路由检测通过（若仍失败多为启动过慢或 /health 不可用）。"


def _pids_listening_on_port(port: int) -> list[int]:
    """枚举本地监听指定端口的 PID（Windows 优先 Get-NetTCPConnection，更可靠）。"""
    if os.name != "nt":
        return []
    ps_cmd = (
        f"$x = Get-NetTCPConnection -LocalPort {int(port)} -State Listen -ErrorAction SilentlyContinue; "
        "if ($null -ne $x) { $x.OwningProcess | Sort-Object -Unique }"
    )
    popen_kw: dict[str, object] = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "ignore",
        "timeout": 15,
        "check": False,
    }
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        popen_kw["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    try:
        ps_exe = shutil.which("powershell.exe") or shutil.which("pwsh.exe") or "powershell.exe"
        proc = subprocess.run([ps_exe, "-NoProfile", "-NonInteractive", "-Command", ps_cmd], **popen_kw)  # noqa: S603
        out = (proc.stdout or "").strip()
        pids: set[int] = set()
        for line in out.splitlines():
            line = line.strip()
            if not line or not line.isdigit():
                continue
            try:
                pids.add(int(line))
            except Exception:
                continue
        if pids:
            return sorted(pids)
    except Exception:
        pass
    try:
        out = subprocess.check_output(  # noqa: S603
            ["netstat", "-ano"],
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
    except Exception:
        return []
    needles = (f":{port}", f"]:{port}")
    pids2: set[int] = set()
    for line in out.splitlines():
        if "LISTENING" not in line:
            continue
        if not any(n in line for n in needles):
            continue
        parts = line.split()
        if not parts:
            continue
        try:
            pids2.add(int(parts[-1]))
        except Exception:
            continue
    return sorted(pids2)


def _kill_pids(pids: list[int]) -> None:
    for pid in pids:
        try:
            subprocess.run(  # noqa: S603
                ["taskkill", "/PID", str(pid), "/F"],
                check=False,
                capture_output=True,
                text=True,
            )
        except Exception:
            pass


def _pid_commandline(pid: int) -> str:
    """
    读取进程命令行，用于识别监听端口的进程是否为本项目 uvicorn/next。
    新版 Windows 常移除/禁用 WMIC，优先用 PowerShell CIM。
    """
    if os.name != "nt":
        return ""
    pid = int(pid)
    ps_cmd = (
        f'$p = Get-CimInstance Win32_Process -Filter "ProcessId={pid}" -ErrorAction SilentlyContinue; '
        f"if ($null -ne $p) {{ $p.CommandLine }} else {{ '' }}"
    )
    popen_kw: dict[str, object] = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "ignore",
        "timeout": 10,
        "check": False,
    }
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        popen_kw["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    try:
        ps_exe = shutil.which("powershell.exe") or shutil.which("pwsh.exe") or "powershell.exe"
        proc = subprocess.run([ps_exe, "-NoProfile", "-NonInteractive", "-Command", ps_cmd], **popen_kw)  # noqa: S603
        out = (proc.stdout or "").strip()
        if out:
            return out.lower()
    except Exception:
        pass
    try:
        wmic_kw: dict[str, object] = {
            "check": False,
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "ignore",
            "timeout": 10,
        }
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            wmic_kw["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        proc = subprocess.run(  # noqa: S603
            ["wmic", "process", "where", f"processid={pid}", "get", "CommandLine", "/value"],
            **wmic_kw,
        )
        out = str(proc.stdout or "")
        for line in out.splitlines():
            if line.startswith("CommandLine="):
                return line.split("=", 1)[1].strip().lower()
    except Exception:
        return ""
    return ""


def _is_backend_cmdline(cmdline: str) -> bool:
    c = str(cmdline or "").lower()
    if not c:
        return False
    # 兼容 api.main:app / 引号路径 / 通过 -m uvicorn 启动
    if "uvicorn" in c and "api.main" in c:
        return True
    if ("longportlauncher" in c or "multitradinglauncher" in c) and "api.main" in c:
        return True
    if "--embedded-backend" in c:
        return True
    return False


def _is_frontend_cmdline(cmdline: str) -> bool:
    c = str(cmdline or "").lower()
    return (
        ("next" in c and ("start" in c or "dev" in c))
        or ("next-server" in c or "next_server" in c)
        or ("next\\dist\\server\\lib\\start-server.js" in c)
        or ("next/dist/server/lib/start-server.js" in c)
        or ("next\\dist\\bin\\next" in c)
        or ("next/dist/bin/next" in c)
        or ("\\next\\node_modules\\" in c and "node.exe" in c)
        or ("/next/node_modules/" in c and "node" in c)
        or ("npm" in c and (" run start" in c or " run dev" in c))
    )


def _is_openbb_cmdline(cmdline: str) -> bool:
    c = str(cmdline or "").lower()
    return "openbb-api" in c or "openbb_platform_api.main" in c


def _cmdline_references_project(cmdline: str) -> bool:
    """命令行是否指向当前 ROOT（用于区分「本项目」与其它占用端口的进程）。"""
    raw = str(cmdline or "").strip()
    if not raw:
        return False
    try:
        root_norm = str(ROOT.resolve()).replace("\\", "/").lower()
    except Exception:
        root_norm = str(ROOT).replace("\\", "/").lower()
    c = raw.lower().replace("\\", "/")
    return root_norm in c


def _remove_next_dev_lock() -> None:
    """Next dev 异常退出后 lock 残留会导致新实例无法启动。"""
    lock = FRONTEND_DIR / ".next" / "dev" / "lock"
    try:
        if lock.is_file():
            lock.unlink()
            print("[INFO] 已移除 Next.js dev 锁 (.next/dev/lock)")
    except Exception as e:
        print(f"[WARN] 无法移除 Next dev 锁（可忽略）: {e}")


def _cleanup_stale_processes_for_launch() -> None:
    """
    双击启动时强制清理占用 API_PORT / WEB_PORT 的陈旧进程，避免 EADDRINUSE、僵尸 node/next。
    可通过 LONGPORT_LAUNCHER_AGGRESSIVE_CLEANUP=0 关闭（不推荐）。
    """
    if not _to_bool(os.getenv("LONGPORT_LAUNCHER_AGGRESSIVE_CLEANUP", "1"), default=True):
        return

    kill_foreign_web = _to_bool(os.getenv("LONGPORT_LAUNCHER_KILL_WEB_UNKNOWN", "1"), default=True)

    for port, kind in ((API_PORT, "backend"), (WEB_PORT, "frontend")):
        pids = _pids_listening_on_port(port)
        if not pids:
            continue
        to_kill: list[int] = []
        unknown: list[int] = []
        my_pid = os.getpid()
        for pid in pids:
            if pid <= 0 or pid == my_pid:
                continue
            cmd = _pid_commandline(pid)
            owns = _cmdline_references_project(cmd)
            if kind == "backend":
                if _is_backend_cmdline(cmd) or owns:
                    to_kill.append(pid)
                else:
                    unknown.append(pid)
            else:
                if _is_frontend_cmdline(cmd) or owns:
                    to_kill.append(pid)
                else:
                    unknown.append(pid)

        if kind == "frontend" and unknown and kill_foreign_web:
            print(
                f"[WARN] 端口 {port} 上另有监听进程 PID={unknown}（非典型 Next/npm），"
                "将一并结束以释放前端端口（可用 LONGPORT_LAUNCHER_KILL_WEB_UNKNOWN=0 关闭）。"
            )
            to_kill.extend(unknown)

        if kind == "backend" and unknown:
            spec = _http_get_json(f"http://127.0.0.1:{port}/openapi.json", timeout=2.5)
            if _api_spec_has_required_paths(spec):
                print(
                    f"[WARN] 端口 {port} 上存在命令行不可识别的监听 PID={unknown}，"
                    "但 OpenAPI 特征与本项目一致，将结束以便 Launcher 重启后端。"
                )
                to_kill.extend(unknown)

        uniq = sorted({int(p) for p in to_kill if p > 0})
        if uniq:
            print(f"[INFO] 启动前端口清理：结束 {kind} 端口 {port} 上进程 PID={uniq}")
            _kill_pids(uniq)
            time.sleep(0.8)

    if FRONTEND_DIR.exists():
        _remove_next_dev_lock()


def _is_embedded_backend_python_cmd(python_cmd: list[str]) -> bool:
    if not python_cmd:
        return False
    first = str(python_cmd[0]).strip().lower()
    cur = str(Path(sys.executable).resolve()).strip().lower()
    return first == cur and "--embedded-backend" in python_cmd


def _collect_managed_pids(port: int, kind: str) -> tuple[list[int], list[int]]:
    all_pids = _pids_listening_on_port(port)
    managed: list[int] = []
    unknown: list[int] = []
    for pid in all_pids:
        cmd = _pid_commandline(pid)
        if kind == "backend":
            if _is_backend_cmdline(cmd):
                managed.append(pid)
            else:
                unknown.append(pid)
        else:
            if _is_frontend_cmdline(cmd):
                managed.append(pid)
            else:
                unknown.append(pid)
    return managed, unknown


def _is_usable_python(path: Path) -> bool:
    try:
        proc = subprocess.run(  # noqa: S603
            [str(path), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
        return proc.returncode == 0
    except Exception:
        return False


def _venv_site_packages(path: Path) -> Path | None:
    lib_dir = path / "Lib" / "site-packages"
    if os.name == "nt" and lib_dir.is_dir():
        return lib_dir
    try:
        candidates = sorted((path / "lib").glob("python*/site-packages"))
    except OSError:
        candidates = []
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def _python_cmd_with_venv_site_packages(base_python: Path, venv_path: Path) -> list[str] | None:
    site_packages = _venv_site_packages(venv_path)
    if not site_packages or not _is_usable_python(base_python):
        return None
    code = (
        "import runpy,sys;"
        f"sys.path.insert(0, {str(site_packages)!r});"
        "sys.argv=['uvicorn',*sys.argv[1:]];"
        "runpy.run_module('uvicorn', run_name='__main__')"
    )
    return [str(base_python), "-c", code]


def _get_python_cmd() -> list[str]:
    # 优先使用项目根目录 `.venv`（与 requirements / TradingAgents 安装位置一致），其次为历史 `.launcher-venv`。
    venv_candidates: list[Path] = []
    if os.name == "nt":
        venv_candidates.extend(
            [
                ROOT / ".venv" / "Scripts" / "python.exe",
                ROOT / ".launcher-venv" / "Scripts" / "python.exe",
            ]
        )
    else:
        venv_candidates.extend(
            [
                ROOT / ".venv" / "bin" / "python3",
                ROOT / ".venv" / "bin" / "python",
                ROOT / ".launcher-venv" / "bin" / "python3",
                ROOT / ".launcher-venv" / "bin" / "python",
            ]
        )
    for candidate in venv_candidates:
        if candidate.exists() and _is_usable_python(candidate):
            return [str(candidate)]

    project_venv = ROOT / ".venv"
    pyvenv_cfg = project_venv / "pyvenv.cfg"
    if pyvenv_cfg.exists():
        cfg_text = ""
        try:
            cfg_text = pyvenv_cfg.read_text(encoding="utf-8", errors="replace")
        except Exception:
            cfg_text = ""
        for line in cfg_text.splitlines():
            if not line.lower().startswith("executable"):
                continue
            _, _, raw = line.partition("=")
            base = Path(raw.strip())
            cmd = _python_cmd_with_venv_site_packages(base, project_venv)
            if cmd:
                print(f"[WARN] .venv python 不可用，改用基础 Python + .venv site-packages: {base}")
                return cmd

    # Script mode can use current interpreter directly.
    if not getattr(sys, "frozen", False):
        return [sys.executable]

    # EXE mode must not use sys.executable (it would recursively start itself).
    # Allow explicit override first.
    env_py = os.getenv("LONGPORT_PYTHON", "").strip()
    if env_py:
        env_py_path = Path(env_py)
        if env_py_path.exists() and env_py_path.is_file() and _is_usable_python(env_py_path):
            return [str(env_py_path)]
        env_py_which = shutil.which(env_py)
        if env_py_which:
            env_py_real = Path(env_py_which)
            if _is_usable_python(env_py_real):
                return [str(env_py_real)]
        print(f"[WARN] LONGPORT_PYTHON 无效或不可用，已忽略: {env_py}")

    py = shutil.which("python.exe") or shutil.which("python")
    if py:
        return [py]

    py_launcher = shutil.which("py.exe") or shutil.which("py")
    if py_launcher:
        return [py_launcher, "-3"]

    if getattr(sys, "frozen", False):
        # 兜底：目标机器若没有 python/py，可直接让 Launcher 自身承载后端进程。
        return [str(Path(sys.executable).resolve()), "--embedded-backend"]

    raise RuntimeError("未找到 Python 解释器，请安装 Python 并确保 python/py 在 PATH 中。")


def _get_npm_cmd() -> str:
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        raise RuntimeError("未找到 npm，请先安装 Node.js 并确保 npm 在 PATH 中。")
    return npm


def _read_text_tail(path: Path, max_lines: int = 45) -> str:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    lines = raw.strip().splitlines()
    if len(lines) <= max_lines:
        return "\n".join(lines)
    return "\n".join(lines[-max_lines:])


def _start_frontend_dev_server(
    npm_cmd: str,
    frontend_script: str,
    web_port: int,
    env: dict[str, str],
) -> subprocess.Popen[bytes]:
    """
    启动 Next/npm；stdout/stderr 写入 launcher_frontend.log。
    打包 exe 下默认通过 cmd /c 调用 npm.cmd，避免 .bat/.cmd 在 CREATE_NO_WINDOW 子进程中启动不稳定。
    """
    env_fe = env.copy()
    env_fe["PORT"] = str(int(web_port))
    node_opts = str(env_fe.get("NODE_OPTIONS") or "").strip()
    if "--max-old-space-size" not in node_opts:
        env_fe["NODE_OPTIONS"] = (node_opts + " --max-old-space-size=8192").strip()

    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        LAUNCHER_FRONTEND_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LAUNCHER_FRONTEND_LOG_FILE, "a", encoding="utf-8", errors="replace") as lf:
            lf.write("\n" + "=" * 72 + "\n")
            lf.write(
                f"[{ts}] cwd={FRONTEND_DIR} npm_cmd={npm_cmd!r} script={frontend_script} port={web_port}\n"
            )
            lf.flush()
    except Exception:
        pass

    log_append = open(LAUNCHER_FRONTEND_LOG_FILE, "a", encoding="utf-8", errors="replace")

    npm_resolved = str(Path(npm_cmd).resolve()) if Path(npm_cmd).exists() else npm_cmd

    # 直接 argv 调用 npm.cmd：比 cmd /s /c 嵌套引号更稳（含空格路径也不会被 cmd 误解析）。
    # 若需旧行为可设 LONGPORT_FRONTEND_CMD_WRAPPER=1。
    use_cmd_shell = os.name == "nt" and _to_bool(
        os.getenv("LONGPORT_FRONTEND_CMD_WRAPPER", ""), default=False
    )
    if use_cmd_shell:
        npm_esc = npm_resolved.replace('"', '""')
        if frontend_script == "dev":
            inner = f'chcp 65001>nul & set "PORT={int(web_port)}" & call "{npm_esc}" run dev -- -p {int(web_port)}'
        else:
            inner = (
                f'chcp 65001>nul & set "PORT={int(web_port)}" & call "{npm_esc}" run '
                f"{frontend_script} -- -p {int(web_port)}"
            )
        # 勿用 /s：会剥离首尾引号，易把含空格路径的 call 弄坏。
        cmd_list = ["cmd.exe", "/d", "/c", inner]
    else:
        if frontend_script == "dev":
            cmd_list = [npm_resolved, "run", "dev", "--", "-p", str(int(web_port))]
        else:
            cmd_list = [npm_resolved, "run", frontend_script, "--", "-p", str(int(web_port))]

    flags = 0
    startupinfo = None
    if os.name == "nt":
        if _to_bool(os.getenv("LONGPORT_CHILD_NEW_CONSOLE", ""), default=False):
            flags = subprocess.CREATE_NEW_CONSOLE
        else:
            flags = (
                subprocess.CREATE_NO_WINDOW
                | subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.DETACHED_PROCESS
            )
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    print(f"[INFO] 前端进程日志文件: {LAUNCHER_FRONTEND_LOG_FILE}")
    print(f"[INFO] 前端启动命令: {cmd_list!r}")

    return subprocess.Popen(  # noqa: S603
        cmd_list,
        cwd=str(FRONTEND_DIR),
        env=env_fe,
        stdout=log_append,
        stderr=subprocess.STDOUT,
        creationflags=flags,
        startupinfo=startupinfo,
    )


def _verify_frontend_spawn_alive(proc: subprocess.Popen[bytes], grace_seconds: float = 7.0) -> tuple[bool, str]:
    time.sleep(max(2.0, grace_seconds))
    code = proc.poll()
    if code is None:
        return True, ""
    tail = _read_text_tail(LAUNCHER_FRONTEND_LOG_FILE, 55)
    brief = tail[-2800:] if tail else "(日志为空或无写入权限)"
    return False, f"前端 npm/next 进程已退出（exit code={code}）。请查看:\n{LAUNCHER_FRONTEND_LOG_FILE}\n\n摘录:\n{brief}"


def _start_process(cmd: list[str], cwd: Path, env: dict[str, str]) -> subprocess.Popen[bytes]:
    flags = 0
    startupinfo = None
    if os.name == "nt":
        # 默认静默启动子进程，避免 Windows 控制台窗口反复弹出。
        # 如需旧行为，可设置 LONGPORT_CHILD_NEW_CONSOLE=1。
        if _to_bool(os.getenv("LONGPORT_CHILD_NEW_CONSOLE", ""), default=False):
            flags = subprocess.CREATE_NEW_CONSOLE
        else:
            flags = (
                subprocess.CREATE_NO_WINDOW
                | subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.DETACHED_PROCESS
            )
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return subprocess.Popen(  # noqa: S603
        cmd,
        cwd=str(cwd),
        env=env,
        creationflags=flags,
        startupinfo=startupinfo,
    )


def _start_backend_logged(cmd: list[str], cwd: Path, env: dict[str, str]) -> subprocess.Popen[bytes]:
    """启动 uvicorn/python 后端，stdout/stderr 写入 launcher_backend.log（静默窗口下排障必需）。"""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        LAUNCHER_BACKEND_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LAUNCHER_BACKEND_LOG_FILE, "a", encoding="utf-8", errors="replace") as lf:
            lf.write("\n" + "=" * 72 + "\n")
            lf.write(f"[{ts}] cwd={cwd}\n[{ts}] cmd={cmd!r}\n")
            lf.flush()
    except Exception:
        pass
    log_append = open(LAUNCHER_BACKEND_LOG_FILE, "a", encoding="utf-8", errors="replace")

    flags = 0
    startupinfo = None
    if os.name == "nt":
        if _to_bool(os.getenv("LONGPORT_CHILD_NEW_CONSOLE", ""), default=False):
            flags = subprocess.CREATE_NEW_CONSOLE
        else:
            flags = subprocess.CREATE_NO_WINDOW
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    print(f"[INFO] 后端进程日志文件: {LAUNCHER_BACKEND_LOG_FILE}")

    return subprocess.Popen(  # noqa: S603
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=log_append,
        stderr=subprocess.STDOUT,
        creationflags=flags,
        startupinfo=startupinfo,
    )


def _run_sync(cmd: list[str], cwd: Path, env: dict[str, str]) -> int:
    try:
        proc = subprocess.run(  # noqa: S603
            cmd,
            cwd=str(cwd),
            env=env,
            check=False,
        )
        return int(proc.returncode)
    except Exception:
        return 1


def _write_pid_file(path: Path, pid: int) -> None:
    try:
        path.write_text(str(pid), encoding="utf-8")
    except Exception:
        pass


def _remove_pid_file(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass


def _watchdog_log(msg: str) -> None:
    _watchdog_log_event(event="watchdog_log", message=msg)


def _append_launcher_crash_log(exc: BaseException) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    tb = traceback.format_exc()
    lines = [
        f"[{ts}] launcher crash",
        f"argv={sys.argv}",
        f"cwd={os.getcwd()}",
        f"exe={sys.executable}",
        f"error={exc!r}",
        tb,
        "-" * 80,
        "",
    ]
    try:
        with open(LAUNCHER_CRASH_LOG_FILE, "a", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception:
        pass


def _pause_before_exit_on_error(exit_code: int) -> None:
    if exit_code == 0:
        return
    if "--embedded-backend" in sys.argv or "--backend-watchdog" in sys.argv:
        return
    if not getattr(sys, "frozen", False):
        return
    print(f"[ERROR] 启动失败，详细日志已写入: {LAUNCHER_CRASH_LOG_FILE}")
    print("[HINT] 可将该日志文件内容发给开发者定位问题。")
    _flush_stdio()
    msg = (
        f"启动失败（退出码 {exit_code}）。\n\n"
        f"详细日志：\n{LAUNCHER_CRASH_LOG_FILE}\n\n"
        "请将 launcher_crash.log 发给开发者或自行查看末尾报错。"
    )
    _win_message_box(msg, "MultiTradingLauncher", icon_info=False)
    try:
        input("按回车键关闭窗口...")
    except Exception:
        time.sleep(20)


def _watchdog_log_event(event: str, message: str, reason_code: str = "", **fields: object) -> None:
    payload: dict[str, object] = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "event": str(event or "watchdog_log"),
        "message": str(message or ""),
    }
    if reason_code:
        payload["reason_code"] = str(reason_code)
    for k, v in fields.items():
        if v is None:
            continue
        payload[str(k)] = v
    line = json.dumps(payload, ensure_ascii=False, default=str) + "\n"
    try:
        with open(WATCHDOG_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def _resolve_backend_pids(port: int) -> tuple[list[int], list[int]]:
    """
    在无法读取进程命令行时（权限/策略/PowerShell 失败），netstat 仍可能给出 PID。
    若端口上仅有 1 个监听进程，且本机 OpenAPI 含必选路由，则视为本项目后端，允许重启。
    """
    managed, unknown = _collect_managed_pids(port, "backend")
    if managed or len(unknown) != 1:
        return managed, unknown
    spec = _http_get_json(f"http://127.0.0.1:{port}/openapi.json", timeout=3.0)
    if not _api_spec_has_required_paths(spec):
        return managed, unknown
    sole = unknown[0]
    _watchdog_log_event(
        event="pid_heuristic",
        message=f"sole listener pid={sole} matched openapi, treating as backend",
        reason_code="openapi_sole_listener",
        pid=sole,
    )
    return [sole], []


def _resolve_openbb_pids(port: int) -> tuple[list[int], list[int]]:
    managed: list[int] = []
    unknown: list[int] = []
    for pid in _pids_listening_on_port(port):
        cmd = _pid_commandline(pid)
        if _is_openbb_cmdline(cmd):
            managed.append(pid)
        else:
            unknown.append(pid)
    return managed, unknown


def _backend_busy_active() -> bool:
    """
    研究/重负载阶段由后端写入 busy 标记，watchdog 在有效期内不重启后端。
    设置环境变量 LONGPORT_WATCHDOG_IGNORE_BUSY=1 可跳过（后端异常退出未清标记时自救）。
    """
    if _to_bool(os.getenv("LONGPORT_WATCHDOG_IGNORE_BUSY", ""), default=False):
        return False
    try:
        if not WATCHDOG_BUSY_FILE.exists():
            return False
        age = time.time() - float(WATCHDOG_BUSY_FILE.stat().st_mtime)
        return age <= float(WATCHDOG_BUSY_TTL_SECONDS)
    except Exception:
        return False


def _backend_cmd(python_cmd: list[str], dev_mode: bool = False) -> list[str]:
    if _is_embedded_backend_python_cmd(python_cmd):
        cmd = [*python_cmd, f"--host={LAUNCHER_UVICORN_HOST}", f"--port={API_PORT}"]
        if dev_mode:
            cmd.append("--reload")
        return cmd
    return [*python_cmd, *build_uvicorn_argv(LAUNCHER_UVICORN_HOST, API_PORT, reload=dev_mode)]


def run_embedded_backend() -> int:
    host = LAUNCHER_UVICORN_HOST
    port = int(API_PORT)
    reload_flag = False
    for arg in sys.argv:
        raw = str(arg or "").strip()
        if raw.startswith("--host="):
            host = raw.split("=", 1)[1].strip() or host
        elif raw.startswith("--port="):
            pv = raw.split("=", 1)[1].strip()
            if pv.isdigit():
                port = int(pv)
        elif raw == "--reload":
            reload_flag = True
    import uvicorn

    uvicorn.run("api.main:app", host=host, port=port, reload=reload_flag)
    return 0


def _ensure_backend_running(
    python_cmd: list[str],
    dev_mode: bool,
    env: dict[str, str],
    *,
    startup_message: str,
) -> tuple[bool, bool]:
    """
    拉起 uvicorn 并等待健康检查与必选路由就绪。
    返回 (是否成功, 是否新启动了子进程)。
    """
    api_health_url = f"http://127.0.0.1:{API_PORT}/health"
    openapi_url = f"http://127.0.0.1:{API_PORT}/openapi.json"
    backend_cmd = _backend_cmd(python_cmd, dev_mode=dev_mode)
    py_hint = " ".join(str(x) for x in python_cmd)
    print(f"[INFO] 后端启动命令: {backend_cmd!r}")
    try:
        backend_proc = _start_backend_logged(backend_cmd, ROOT, env)
    except FileNotFoundError as e:
        print(f"[ERROR] 后端启动失败，找不到可执行文件: {e}")
        print(f"[HINT] backend_cmd = {backend_cmd}")
        print(f"[HINT] ROOT = {ROOT}")
        return False, False
    print(startup_message + (" (dev reload)" if dev_mode else ""))
    try:
        wait_seconds = int(os.getenv("LONGPORT_BACKEND_READY_WAIT_SECONDS", "120") or "120")
    except ValueError:
        wait_seconds = 120
    wait_seconds = max(6, min(wait_seconds, 600))
    try:
        probe_timeout = float(os.getenv("LONGPORT_HEALTH_PROBE_TIMEOUT", "6") or "6")
    except ValueError:
        probe_timeout = 6.0
    probe_timeout = max(1.0, min(probe_timeout, 120.0))
    loops = max(1, wait_seconds * 2)  # 0.5s 一轮
    saw_health_ok = False
    for _ in range(loops):
        try:
            dead = backend_proc.poll()
            if dead is not None:
                tail = _read_text_tail(LAUNCHER_BACKEND_LOG_FILE, 90)
                print(f"[ERROR] 后端进程已退出（exit code={dead}），未完成就绪等待。")
                if tail.strip():
                    print("[INFO] launcher_backend.log 摘录（末尾）：")
                    print(tail[-4500:])
                print(f"[HINT] 完整后端日志: {LAUNCHER_BACKEND_LOG_FILE}")
                print(
                    f"[HINT] 手动验证（在项目根目录）: cd /d \"{ROOT}\" && "
                    f"{py_hint} -m uvicorn api.main:app --host 127.0.0.1 --port {API_PORT}"
                )
                print(f"[HINT] 依赖: {py_hint} -m pip install -r requirements.txt")
                return False, True

            if _is_http_healthy(api_health_url, timeout=probe_timeout):
                saw_health_ok = True
                spec = _http_get_json(openapi_url, timeout=max(3.0, probe_timeout))
                if _api_spec_has_required_paths(spec):
                    return True, True
            time.sleep(0.5)
        except KeyboardInterrupt:
            print(
                "\n[INFO] 已中断等待后端就绪。"
                " 若子进程已启动，后端可能在后台继续运行；也可用任务管理器结束对应 Python。"
            )
            _flush_stdio()
            return False, True
    if saw_health_ok:
        print("[WARN] 后端健康检查已通过，但 OpenAPI 关键路由尚未完全就绪。")
        print("[HINT] 将继续启动前端；若 /options 页面暂不可用，请稍后刷新。")
        return True, True

    dead = backend_proc.poll()
    if dead is not None:
        tail = _read_text_tail(LAUNCHER_BACKEND_LOG_FILE, 90)
        print(f"[ERROR] 后端进程已退出（exit code={dead}）。")
        if tail.strip():
            print("[INFO] launcher_backend.log 摘录（末尾）：")
            print(tail[-4500:])
        print(f"[HINT] 完整后端日志: {LAUNCHER_BACKEND_LOG_FILE}")
        print(f"[HINT] 手动验证: cd /d \"{ROOT}\" && {py_hint} -m uvicorn api.main:app --host 127.0.0.1 --port {API_PORT}")
        return False, True

    print("[ERROR] 后端在超时内未完成就绪（/health 或 OpenAPI 必选路由未满足）。")
    print(f"[HINT] 后端日志: {LAUNCHER_BACKEND_LOG_FILE}")
    print(f"[HINT] 若为首次启动、机器较慢，可增大等待：LONGPORT_BACKEND_READY_WAIT_SECONDS=300")
    spec = _http_get_json(openapi_url, timeout=min(20.0, max(8.0, probe_timeout * 2)))
    print("[INFO] OpenAPI 必选路由诊断：")
    print(_describe_missing_required_paths(spec))
    print(f"[HINT] 当前 Python: {py_hint}")
    print(f"[HINT] 依赖检查: {py_hint} -m pip install -r requirements.txt")
    print(f"[HINT] 健康检查 URL: {api_health_url}")
    return False, True


def _subprocess_env_with_user_secrets(base: dict[str, str]) -> dict[str, str]:
    """将 data/user_env/davies 与根 .env 合并进子进程环境（与 API 进程 bootstrap 一致）。"""
    from config.user_env_store import combined_env_for_cli

    out = dict(base)
    for k, v in combined_env_for_cli(ROOT).items():
        out[k] = str(v)
    return out


def _read_openbb_runtime_config() -> dict[str, object]:
    from config.user_env_store import combined_env_for_cli

    env_file = combined_env_for_cli(ROOT)
    enabled_raw = os.getenv("OPENBB_ENABLED", env_file.get("OPENBB_ENABLED", "0"))
    base_url = os.getenv("OPENBB_BASE_URL", env_file.get("OPENBB_BASE_URL", f"http://127.0.0.1:{OPENBB_DEFAULT_PORT}"))
    auto_start_raw = os.getenv("OPENBB_AUTO_START", env_file.get("OPENBB_AUTO_START", "1"))
    timeout_raw = os.getenv("OPENBB_TIMEOUT_SECONDS", env_file.get("OPENBB_TIMEOUT_SECONDS", "5"))

    enabled = _to_bool(enabled_raw, default=False)
    auto_start = _to_bool(auto_start_raw, default=True)
    base_url = str(base_url or "").strip().rstrip("/")
    if not base_url:
        base_url = f"http://127.0.0.1:{OPENBB_DEFAULT_PORT}"

    parsed = urllib.parse.urlparse(base_url if "://" in base_url else f"http://{base_url}")
    scheme = parsed.scheme or "http"
    host = parsed.hostname or "127.0.0.1"
    port = int(parsed.port or OPENBB_DEFAULT_PORT)
    local_hosts = {"127.0.0.1", "localhost", "0.0.0.0", "::1"}
    is_local = host in local_hosts
    probe_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    probe_url = f"{scheme}://{probe_host}:{port}/"

    try:
        timeout = max(1.0, float(timeout_raw))
    except Exception:
        timeout = 5.0

    return {
        "enabled": enabled,
        "auto_start": auto_start,
        "base_url": base_url,
        "host": host,
        "port": port,
        "probe_host": probe_host,
        "probe_url": probe_url,
        "timeout": timeout,
        "is_local": is_local,
    }


def _get_openbb_cmd() -> tuple[list[str] | None, str]:
    exe_candidates = [
        ROOT / ".openbb-venv" / "Scripts" / "openbb-api.exe",
        ROOT / ".openbb-venv" / "Scripts" / "openbb-api",
        ROOT / ".venv" / "Scripts" / "openbb-api.exe",
        ROOT / ".venv" / "Scripts" / "openbb-api",
    ]
    for p in exe_candidates:
        if p.exists() and p.is_file():
            return [str(p)], str(p)

    which_openbb = shutil.which("openbb-api.exe") or shutil.which("openbb-api")
    if which_openbb:
        return [which_openbb], which_openbb

    py_candidates = [
        ROOT / ".openbb-venv" / "Scripts" / "python.exe",
        ROOT / ".venv" / "Scripts" / "python.exe",
    ]
    for p in py_candidates:
        if p.exists() and p.is_file():
            return [str(p), "-m", "openbb_platform_api.main"], f"{p} -m openbb_platform_api.main"

    return None, ""


def _start_background_process(cmd: list[str], cwd: Path, env: dict[str, str]) -> subprocess.Popen[bytes]:
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    return subprocess.Popen(  # noqa: S603
        cmd,
        cwd=str(cwd),
        env=env,
        creationflags=flags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_for_port_release(port: int, host: str = "127.0.0.1", timeout: float = 8.0) -> bool:
    deadline = time.time() + max(0.5, float(timeout))
    while time.time() < deadline:
        if not _is_port_open(port, host=host):
            return True
        time.sleep(0.5)
    return not _is_port_open(port, host=host)


def _watchdog_running() -> bool:
    pid = _read_pid_file(WATCHDOG_PID_FILE)
    return bool(pid and _is_pid_alive(pid))


def _clear_watchdog_pause() -> None:
    _remove_pid_file(WATCHDOG_PAUSE_FILE)


def _start_backend_watchdog() -> None:
    if _watchdog_running():
        print("[INFO] 后端守护已在运行。")
        return
    env = _subprocess_env_with_user_secrets(os.environ.copy())
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--backend-watchdog"]
    else:
        py = _get_python_cmd()
        cmd = [*py, str(Path(__file__).resolve()), "--backend-watchdog"]
    try:
        p = _start_background_process(cmd, ROOT, env)
        print(f"[OK] 已启动后端守护进程 (pid={p.pid})")
    except Exception as e:
        print(f"[WARN] 后端守护进程启动失败: {e}")


def run_backend_watchdog() -> int:
    global _watchdog_mutex_handle
    if os.name == "nt":
        try:
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.CreateMutexW(None, True, _WATCHDOG_MUTEX_NAME)
            if handle:
                last_error = int(kernel32.GetLastError())
                # ERROR_ALREADY_EXISTS = 183
                if last_error == 183:
                    try:
                        kernel32.CloseHandle(handle)
                    except Exception:
                        pass
                    _watchdog_log_event(
                        event="watchdog_exit",
                        message="watchdog instance already running, exit duplicate",
                        reason_code="duplicate_watchdog_instance",
                    )
                    return 0
                _watchdog_mutex_handle = handle
        except Exception:
            pass

    _write_pid_file(WATCHDOG_PID_FILE, os.getpid())
    def _release_watchdog_runtime() -> None:
        _remove_pid_file(WATCHDOG_PID_FILE)
        if os.name == "nt":
            try:
                if _watchdog_mutex_handle:
                    ctypes.windll.kernel32.ReleaseMutex(_watchdog_mutex_handle)
                    ctypes.windll.kernel32.CloseHandle(_watchdog_mutex_handle)
            except Exception:
                pass

    atexit.register(_release_watchdog_runtime)
    _watchdog_log("backend watchdog started")
    if _to_bool(os.getenv("LONGPORT_WATCHDOG_IGNORE_BUSY", ""), default=False):
        _watchdog_log_event(
            event="watchdog_log",
            message="LONGPORT_WATCHDOG_IGNORE_BUSY=1, backend busy marker will be ignored",
            reason_code="ignore_busy_env",
        )

    env = _subprocess_env_with_user_secrets(os.environ.copy())
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    dev_mode = os.getenv("LONGPORT_DEV", "").strip() == "1"
    python_cmd = _get_python_cmd()
    cmd = _backend_cmd(python_cmd, dev_mode=dev_mode)

    fail_count = 0
    # 防抖：后端刚重启后给一段宽限，避免启动期短暂超时被误判。
    last_restart_ts = 0.0
    while True:
        if WATCHDOG_PAUSE_FILE.exists():
            _watchdog_log_event(
                event="watchdog_exit",
                message="pause file detected, watchdog exiting",
                reason_code="pause_file",
            )
            return 0

        if _backend_busy_active():
            busy_port_open = _is_port_open(API_PORT)
            # busy 标记只用于“重负载保护”，不应阻止“后端已死”时的自恢复重启。
            # 忙时仅做端口探测，避免频繁命令行探测触发 Windows 子进程窗口闪现。
            if busy_port_open:
                _watchdog_log_event(
                    event="health_skip",
                    message="backend busy marker active, skip health restart",
                    reason_code="backend_busy",
                )
                fail_count = 0
                time.sleep(WATCHDOG_HEALTHY_SLEEP_SECONDS)
                continue
            try:
                if WATCHDOG_BUSY_FILE.exists():
                    WATCHDOG_BUSY_FILE.unlink()
            except Exception:
                pass
            _watchdog_log_event(
                event="busy_recover",
                message="backend busy marker stale while backend appears down, enabling auto-restart",
                reason_code="backend_busy_stale",
            )
            # 直接进入重启链路，避免长时间停留在“后端断连”状态。
            fail_count = WATCHDOG_FAILS_BEFORE_RESTART

        if last_restart_ts > 0 and (time.time() - last_restart_ts) < WATCHDOG_STARTUP_GRACE_SECONDS:
            _watchdog_log_event(
                event="health_skip",
                message="backend startup grace active, skip health restart",
                reason_code="startup_grace",
            )
            fail_count = 0
            time.sleep(WATCHDOG_HEALTHY_SLEEP_SECONDS)
            continue

        health_url = f"http://127.0.0.1:{API_PORT}/health"
        healthy = _is_http_healthy(health_url, timeout=WATCHDOG_HEALTH_TIMEOUT_SECONDS)
        if healthy:
            fail_count = 0
            time.sleep(WATCHDOG_HEALTHY_SLEEP_SECONDS)
            continue

        # 二次确认：研究任务等重负载时，2s 探针可能误判；若端口仍在，再给一次更长超时确认。
        if _is_port_open(API_PORT) and _is_http_healthy(health_url, timeout=WATCHDOG_CONFIRM_TIMEOUT_SECONDS):
            fail_count = 0
            time.sleep(WATCHDOG_HEALTHY_SLEEP_SECONDS)
            continue

        fail_count += 1
        if fail_count < WATCHDOG_FAILS_BEFORE_RESTART:
            time.sleep(WATCHDOG_UNHEALTHY_SLEEP_SECONDS)
            continue

        # 最终确认（长超时）：
        # 避免在高负载阶段因为短超时累计误判，从而触发重启风暴导致终端闪跳。
        deep_health_timeout = max(30.0, WATCHDOG_CONFIRM_TIMEOUT_SECONDS * 2.0)
        openapi_url = f"http://127.0.0.1:{API_PORT}/openapi.json"
        if _is_http_healthy(health_url, timeout=deep_health_timeout):
            _watchdog_log_event(
                event="restart_skip",
                message="deep health probe recovered, skip restart",
                reason_code="deep_probe_recovered",
                fail_count=fail_count,
                timeout_seconds=deep_health_timeout,
            )
            fail_count = 0
            time.sleep(WATCHDOG_HEALTHY_SLEEP_SECONDS)
            continue
        if _http_get_json(openapi_url, timeout=deep_health_timeout) is not None:
            _watchdog_log_event(
                event="restart_skip",
                message="openapi probe recovered, skip restart",
                reason_code="openapi_probe_recovered",
                fail_count=fail_count,
                timeout_seconds=deep_health_timeout,
            )
            fail_count = 0
            time.sleep(WATCHDOG_HEALTHY_SLEEP_SECONDS)
            continue

        managed, unknown = _resolve_backend_pids(API_PORT)
        if unknown:
            _watchdog_log_event(
                event="restart_skip",
                message=f"port {API_PORT} occupied by unknown pids={unknown}, skip restart",
                reason_code="port_conflict",
                unknown_pids=unknown,
                fail_count=fail_count,
            )
            fail_count = 0
            time.sleep(WATCHDOG_HEALTHY_SLEEP_SECONDS)
            continue

        if last_restart_ts > 0 and (time.time() - last_restart_ts) < WATCHDOG_RESTART_COOLDOWN_SECONDS:
            _watchdog_log_event(
                event="restart_skip",
                message="restart cooldown active, skip immediate restart",
                reason_code="restart_cooldown",
                fail_count=fail_count,
                cooldown_seconds=WATCHDOG_RESTART_COOLDOWN_SECONDS,
            )
            fail_count = 0
            time.sleep(WATCHDOG_HEALTHY_SLEEP_SECONDS)
            continue
        pid_before = managed[0] if managed else None
        if managed:
            # 已有后端进程但不健康，先清理再重启
            _kill_pids(managed)
            time.sleep(1)

        try:
            # 健康探针失败即将重启后端时，先停止 auto_trader 子进程，
            # 避免旧 worker 在新 API 上继续扫旧 market/pair_pool 配置。
            _stop_auto_trader_before_backend_restart()
            _watchdog_log_event(
                event="restart_attempt",
                message=f"health failed continuously, restarting backend fail_count={fail_count}",
                reason_code="health_timeout",
                fail_count=fail_count,
                pid_before=pid_before,
            )
            p = _start_background_process(cmd, ROOT, env)
            _watchdog_log_event(
                event="restart_success",
                message=f"backend restarted pid={p.pid}",
                reason_code="health_timeout",
                fail_count=fail_count,
                pid_before=pid_before,
                pid_after=int(p.pid),
            )
            last_restart_ts = time.time()
        except Exception as e:
            _watchdog_log_event(
                event="restart_failed",
                message=f"backend restart failed: {e}",
                reason_code="restart_exception",
                fail_count=fail_count,
                pid_before=pid_before,
                error=str(e),
            )

        fail_count = 0
        time.sleep(WATCHDOG_HEALTHY_SLEEP_SECONDS)


def _acquire_single_instance_lock() -> bool:
    """
    防止重复双击导致并发启动。
    - Windows: 使用全局命名 Mutex（推荐）
    - 其他平台: 使用 lock 文件兜底
    """
    global _instance_mutex_handle, _instance_lock_file

    if os.name == "nt":
        kernel32 = ctypes.windll.kernel32
        # BOOL bInitialOwner=True
        handle = kernel32.CreateMutexW(None, True, _INSTANCE_MUTEX_NAME)
        if not handle:
            return True
        last_error = kernel32.GetLastError()
        # ERROR_ALREADY_EXISTS = 183
        if int(last_error) == 183:
            try:
                kernel32.CloseHandle(handle)
            except Exception:
                pass
            return False
        _instance_mutex_handle = handle

        def _release_mutex() -> None:
            try:
                if _instance_mutex_handle:
                    kernel32.ReleaseMutex(_instance_mutex_handle)
                    kernel32.CloseHandle(_instance_mutex_handle)
            except Exception:
                pass

        atexit.register(_release_mutex)
        return True

    # Non-Windows fallback.
    try:
        lock_path = ROOT / ".launcher.lock"
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode("utf-8"))
        os.close(fd)
        _instance_lock_file = lock_path

        def _release_file_lock() -> None:
            try:
                if _instance_lock_file and _instance_lock_file.exists():
                    _instance_lock_file.unlink()
            except Exception:
                pass

        atexit.register(_release_file_lock)
        return True
    except FileExistsError:
        return False
    except Exception:
        return True


def run_force_restart_backend() -> int:
    """
    不占用启动器单实例互斥锁，仅结束后端并重新拉起 uvicorn。
    用法（exe）：在项目 dist 目录打开终端执行
      .\\MultiTradingLauncher.exe --force-restart-backend
    """
    if not FRONTEND_DIR.exists():
        print(f"[ERROR] 未找到前端目录: {FRONTEND_DIR}")
        print("[HINT] 请将 MultiTradingLauncher.exe 放在项目根目录下的 dist 文件夹中运行。")
        return 1
    try:
        if WATCHDOG_BUSY_FILE.exists():
            WATCHDOG_BUSY_FILE.unlink()
    except Exception:
        pass
    env = _subprocess_env_with_user_secrets(os.environ.copy())
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    dev_mode = os.getenv("LONGPORT_DEV", "").strip() == "1"
    python_cmd = _get_python_cmd()
    print(f"[INFO] --force-restart-backend | 项目目录: {ROOT}")
    # 手动重启 backend 时，同步停止 auto_trader worker/supervisor，确保新后端拉起的是最新配置。
    _stop_auto_trader_before_backend_restart()
    managed, unknown = _resolve_backend_pids(API_PORT)
    if unknown:
        print(
            f"[ERROR] 端口 {API_PORT} 上存在无法识别为本项目后端的进程 PID={unknown}，"
            "请用任务管理器结束对应进程后重试。"
        )
        print(
            "[HINT] 若仅有一个 python/uvicorn 在监听该端口，仍失败时检查是否能访问 "
            f"http://127.0.0.1:{API_PORT}/openapi.json"
        )
        return 1
    if managed:
        print(f"[INFO] 正在结束后端进程 PID={managed} …")
        _kill_pids(managed)
        time.sleep(1)
    ok, _ = _ensure_backend_running(
        python_cmd, dev_mode, env, startup_message="[OK] 后端已重新拉起"
    )
    return 0 if ok else 1


def main() -> int:
    if "--embedded-backend" in sys.argv:
        return run_embedded_backend()

    if "--backend-watchdog" in sys.argv:
        return run_backend_watchdog()

    if "--force-restart-backend" in sys.argv:
        return run_force_restart_backend()

    if not _acquire_single_instance_lock():
        print("[WARN] 检测到已有启动器主进程正在执行，请勿重复双击。")
        print("[HINT] 任务管理器中若有多条 MultiTradingLauncher.exe，可先结束再启动。")
        print("[HINT] 需要重启 API：可先关闭启动器控制台，或在终端执行：")
        print(f'      "{Path(sys.executable).resolve()}" --force-restart-backend')
        _flush_stdio()
        dup_msg = (
            "检测到已有主启动器在运行，本次启动已取消（单实例互斥）。\n\n"
            "若任务栏没有启动器窗口：请打开「任务管理器」，结束所有 MultiTradingLauncher.exe 后，再双击启动一次。\n\n"
            "说明：本程序还会拉起后端守护等子进程，任务管理器里可能出现多条同名进程。"
        )
        _win_message_box(dup_msg, icon_info=True)
        return 0

    if not FRONTEND_DIR.exists():
        print(f"[ERROR] 未找到前端目录: {FRONTEND_DIR}")
        print("[HINT] 请将 MultiTradingLauncher.exe 放在项目根目录下的 dist 文件夹中运行（勿单独拷贝 exe）。")
        return 1

    env = _subprocess_env_with_user_secrets(os.environ.copy())
    _augment_path_for_gui_launch(env)
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    dev_mode = os.getenv("LONGPORT_DEV", "").strip() == "1"

    try:
        python_cmd = _get_python_cmd()
        npm_cmd = _get_npm_cmd()
    except RuntimeError as e:
        print(f"[ERROR] {e}")
        print("[HINT] 从资源管理器双击启动时系统 PATH 常不完整；启动器已尝试追加 Program Files\\nodejs 等路径。")
        print("[HINT] 若仍失败：请先安装 Node.js 与 Python，或用「终端」cd 到 dist 目录后运行 .\\MultiTradingLauncher.exe。")
        _flush_stdio()
        if getattr(sys, "frozen", False):
            _win_message_box(
                f"{e}\n\n请确认已安装 Node.js 与 Python，并勿单独拷贝 exe（需与项目 dist 目录一同使用）。",
                "MultiTradingLauncher",
                icon_info=False,
            )
        return 1

    print("[INFO] 启动前清理：释放陈旧监听进程（端口 %s/%s）…" % (API_PORT, WEB_PORT))
    _cleanup_stale_processes_for_launch()

    _clear_watchdog_pause()
    _start_backend_watchdog()

    print(f"[INFO] 项目目录: {ROOT}")
    print(f"[INFO] 启动模式: {'开发模式' if dev_mode else '生产模式优先'}")

    backend_started = False
    frontend_started = False

    api_health_url = f"http://127.0.0.1:{API_PORT}/health"
    api_healthy = _is_http_healthy(api_health_url)
    api_has_required_routes = False
    if _is_port_open(API_PORT) and api_healthy:
        spec = _http_get_json(f"http://127.0.0.1:{API_PORT}/openapi.json", timeout=3.0)
        api_has_required_routes = _api_spec_has_required_paths(spec)
        if not api_has_required_routes:
            print("[WARN] 检测到后端为旧版本（缺少期权/stop-all 路由），将尝试重启后端。")

    # 健康且路由齐全：若为本项目 uvicorn，再次运行 Launcher 时也重启 API，便于加载最新代码。
    if _is_port_open(API_PORT) and api_healthy and api_has_required_routes:
        managed, unknown = _resolve_backend_pids(API_PORT)
        if unknown:
            print(
                f"[WARN] 端口 {API_PORT} 上存在非本项目监听进程 {unknown}，为安全起见不自动结束；"
                "若需重启 API 请先手动释放该端口。"
            )
            print(
                "[HINT] 若你确定占用进程就是本项目后端：可能是系统无法读取进程命令行（旧版依赖 WMIC）。"
                "请重新打包/更新 MultiTradingLauncher.exe（已改用 PowerShell 读取命令行）。"
            )
            print(f"[INFO] 当前后端健康，继续使用: http://127.0.0.1:{API_PORT}")
        elif managed:
            print("[INFO] 再次启动 Launcher：正在重启本项目后端以加载最新代码…")
            _kill_pids(managed)
            time.sleep(1)
            ok, started = _ensure_backend_running(
                python_cmd, dev_mode, env, startup_message="[OK] 已重启后端服务"
            )
            if not ok:
                return 1
            backend_started = started
        else:
            print(
                f"[INFO] 后端已在运行（未能识别为本项目 uvicorn，跳过自动重启）: http://127.0.0.1:{API_PORT}"
            )
    else:
        if _is_port_open(API_PORT) and not api_healthy:
            managed, unknown = _resolve_backend_pids(API_PORT)
            if unknown:
                print(f"[ERROR] 端口 {API_PORT} 被非本项目进程占用: {unknown}")
                print("[HINT] 请先释放该端口后再启动，避免误杀其它应用。")
                return 1
            if managed:
                print(f"[WARN] 检测到后端端口异常占用，正在清理本项目进程: {managed}")
                _kill_pids(managed)
                time.sleep(1)
        elif _is_port_open(API_PORT) and api_healthy and not api_has_required_routes:
            managed, unknown = _resolve_backend_pids(API_PORT)
            if unknown:
                print(f"[ERROR] 后端端口 {API_PORT} 存在非本项目进程: {unknown}")
                print("[HINT] 请手动停止该进程，确保启动器可以加载最新后端。")
                return 1
            if managed:
                print(f"[WARN] 正在重启旧版后端进程: {managed}")
                _kill_pids(managed)
                time.sleep(1)
        ok, started = _ensure_backend_running(
            python_cmd, dev_mode, env, startup_message="[OK] 已启动后端服务"
        )
        if not ok:
            return 1
        backend_started = started

    openbb_cfg = _read_openbb_runtime_config()
    if bool(openbb_cfg.get("enabled")):
        openbb_base_url = str(openbb_cfg.get("base_url") or "")
        openbb_probe_url = str(openbb_cfg.get("probe_url") or "")
        openbb_probe_host = str(openbb_cfg.get("probe_host") or "127.0.0.1")
        openbb_port = int(openbb_cfg.get("port") or OPENBB_DEFAULT_PORT)
        openbb_timeout = float(openbb_cfg.get("timeout") or 5.0)

        if _is_http_healthy(openbb_probe_url, timeout=openbb_timeout):
            print(f"[INFO] OpenBB 已在运行: {openbb_base_url}")
        elif not bool(openbb_cfg.get("auto_start")):
            print(f"[WARN] OpenBB 未连通: {openbb_base_url}")
            print("[HINT] OPENBB_AUTO_START=false，已跳过自动拉起。")
        elif not bool(openbb_cfg.get("is_local")):
            print(f"[WARN] OpenBB 未连通: {openbb_base_url}")
            print("[HINT] 仅支持自动拉起本机 OpenBB 服务；当前目标是远端地址。")
        else:
            openbb_cmd, openbb_cmd_hint = _get_openbb_cmd()
            openbb_skip_hint = ""
            openbb_managed, openbb_unknown = _resolve_openbb_pids(openbb_port)
            if openbb_managed:
                print(f"[WARN] OpenBB port {openbb_port} has stale OpenBB process(es), restarting: {openbb_managed}")
                _kill_pids(openbb_managed)
                if not _wait_for_port_release(openbb_port, host=openbb_probe_host, timeout=max(3.0, openbb_timeout)):
                    print(f"[WARN] OpenBB port {openbb_port} did not release in time; skipped auto start to avoid conflict.")
                    openbb_cmd = None
                    openbb_skip_hint = "OpenBB port did not release in time."
            elif _is_port_open(openbb_port, host=openbb_probe_host):
                print(f"[WARN] OpenBB port {openbb_port} is occupied by unknown process(es); skipped auto restart to avoid killing another app.")
                if openbb_unknown:
                    print(f"[HINT] OpenBB port listener PID(s): {openbb_unknown}")
                openbb_cmd = None
                openbb_skip_hint = "OpenBB port is occupied by unknown process(es)."
            if not openbb_cmd:
                print(f"[WARN] OpenBB 未连通: {openbb_base_url}")
                if openbb_skip_hint:
                    print(f"[HINT] {openbb_skip_hint}")
                else:
                    print("[HINT] 未找到 openbb-api 启动命令，请确认 .openbb-venv 或系统环境已安装 OpenBB API。")
            else:
                try:
                    _start_background_process(openbb_cmd, ROOT, env)
                    print("[OK] 已自动拉起 OpenBB 服务")
                    openbb_ready = False
                    for _ in range(20):
                        if _is_http_healthy(openbb_probe_url, timeout=openbb_timeout):
                            openbb_ready = True
                            break
                        time.sleep(1)
                    if openbb_ready:
                        print(f"[INFO] OpenBB 连接就绪: {openbb_base_url}")
                    else:
                        print(f"[WARN] OpenBB 启动后仍不可达: {openbb_base_url}")
                        if openbb_cmd_hint:
                            print(f"[HINT] 可手动执行: {openbb_cmd_hint}")
                except Exception as e:
                    print(f"[WARN] OpenBB 自动拉起失败: {e}")
                    if openbb_cmd_hint:
                        print(f"[HINT] 可手动执行: {openbb_cmd_hint}")

    web_url = f"http://127.0.0.1:{WEB_PORT}"
    frontend_source_hash = _frontend_source_hash()
    build_ready = _frontend_build_ready()
    build_hash_matches = _frontend_build_hash_matches(frontend_source_hash)
    if not dev_mode:
        print(
            "[INFO] 前端版本标记检查: "
            f"build_ready={build_ready}, hash_match={build_hash_matches}"
        )
    web_healthy = _is_http_healthy(web_url)
    frontend_restart_needed = False
    frontend_force_rebuild = False
    if _is_port_open(WEB_PORT) and web_healthy:
        managed_frontend, unknown_frontend = _collect_managed_pids(WEB_PORT, "frontend")
        if unknown_frontend:
            print(f"[WARN] 端口 {WEB_PORT} 由非启动器进程占用: {unknown_frontend}，将复用现有服务。")
            frontend_restart_needed = False
        elif managed_frontend:
            frontend_restart_needed = True
            print(f"[INFO] 再次启动 Launcher：重启前端以刷新运行时（PID={managed_frontend}）。")
        missing_routes: list[str] = []
        for r in REQUIRED_WEB_ROUTES:
            code = _http_status_code(f"{web_url}{r}", timeout=2.0)
            if code != 200:
                missing_routes.append(f"{r}({code if code is not None else 'N/A'})")
        if missing_routes:
            frontend_restart_needed = True
            frontend_force_rebuild = True
            print(
                "[WARN] 检测到前端关键路由异常: "
                + ", ".join(missing_routes)
                + "，将重启并重建前端。"
            )
        elif not dev_mode and not build_hash_matches:
            frontend_restart_needed = True
            frontend_force_rebuild = True
            print("[WARN] 检测到前端源码版本已变化（哈希不一致），将重启并重建前端。")
        elif not frontend_restart_needed:
            print(f"[INFO] 前端已在运行: {web_url}")
    else:
        frontend_restart_needed = True

    if frontend_restart_needed:
        if _is_port_open(WEB_PORT):
            managed, unknown = _collect_managed_pids(WEB_PORT, "frontend")
            if unknown:
                print(f"[ERROR] 端口 {WEB_PORT} 被非本项目进程占用: {unknown}")
                print("[HINT] 请先释放该端口后再启动，避免误杀其它应用。")
                return 1
            if managed:
                print(f"[WARN] 正在清理前端进程: {managed}")
                _kill_pids(managed)
                time.sleep(1)

        if not (FRONTEND_DIR / "node_modules").exists():
            hint = (
                "未找到 frontend\\node_modules，前端依赖尚未安装。\n\n"
                f"请在终端执行:\n  cd /d \"{FRONTEND_DIR}\"\n  npm install\n\n"
                "完成后再运行 MultiTradingLauncher.exe。"
            )
            print(f"[ERROR] {hint.replace(chr(10), ' ')}")
            _flush_stdio()
            if getattr(sys, "frozen", False):
                _win_message_box(hint, "MultiTradingLauncher — 缺少 node_modules", icon_info=False)
            return 1

        build_ready = _frontend_build_ready()
        need_build = (not dev_mode) and (frontend_force_rebuild or not build_ready or not build_hash_matches)
        if need_build:
            print("[INFO] 正在执行 npm run build（确保包含 /options 路由）...")
            rc = _run_sync([npm_cmd, "run", "build"], FRONTEND_DIR, env)
            build_ready = _frontend_build_ready()
            if rc != 0 or not build_ready:
                print("[WARN] 前端生产构建失败，将回退到 dev 模式启动。")
            else:
                _write_text_file(_frontend_hash_marker_path(), frontend_source_hash)
        forced_mode = str(os.getenv("LONGPORT_FRONTEND_MODE", "") or "").strip().lower()
        if forced_mode in {"dev", "start"}:
            frontend_script = forced_mode
        elif dev_mode or not build_ready or getattr(sys, "frozen", False):
            frontend_script = "dev"
        else:
            frontend_script = "start"
        frontend_proc = _start_frontend_dev_server(npm_cmd, frontend_script, WEB_PORT, env)
        frontend_started = True
        print(f"[OK] 已提交前端启动 ({frontend_script}, port={WEB_PORT})")
        spawn_ok, spawn_detail = _verify_frontend_spawn_alive(frontend_proc)
        if not spawn_ok:
            print(f"[ERROR] {spawn_detail}")
            _flush_stdio()
            if getattr(sys, "frozen", False):
                _win_message_box(
                    spawn_detail[:3900],
                    "MultiTradingLauncher — 前端进程退出",
                    icon_info=False,
                )
            return 1

    if backend_started or frontend_started:
        print("[INFO] 服务启动中，稍后将自动打开浏览器...")
        # 双击启动时前端可能刚执行完 build + next start，20s 常不够；可用 LONGPORT_BROWSER_WAIT_SECONDS 覆盖。
        try:
            wait_override = int(os.getenv("LONGPORT_BROWSER_WAIT_SECONDS", "0") or "0")
        except ValueError:
            wait_override = 0
        if wait_override > 0:
            wait_loops = max(1, wait_override)
        elif frontend_started:
            wait_loops = 120
        else:
            wait_loops = 45
        all_ready = False
        for _ in range(wait_loops):
            if _is_http_healthy(api_health_url) and _is_http_healthy(f"http://127.0.0.1:{WEB_PORT}"):
                all_ready = True
                backend_ok = True
                break
            time.sleep(1)
        if not all_ready:
            print(
                "[WARN] 服务尚未完全就绪，但将继续打开浏览器。"
                f" 可稍候刷新 {web_url}，或增大等待：LONGPORT_BROWSER_WAIT_SECONDS=180"
            )
        fe_probe = f"http://127.0.0.1:{WEB_PORT}"
        if frontend_started and not _is_http_healthy(fe_probe, timeout=4.0):
            tail = _read_text_tail(LAUNCHER_FRONTEND_LOG_FILE, 42)
            excerpt = tail[-2400:] if tail else "(无日志)"
            slow_msg = (
                f"前端在端口 {WEB_PORT} 长时间未响应 HTTP。\n\n"
                f"日志文件:\n{LAUNCHER_FRONTEND_LOG_FILE}\n\n"
                f"末尾摘录:\n{excerpt}\n\n"
                "常见原因：node_modules 未安装（请在 frontend 目录执行 npm install）、"
                "或防火墙/杀毒拦截 node.exe。"
            )
            print("[WARN] 前端未就绪，详见 launcher_frontend.log")
            _flush_stdio()
            _win_message_box(slow_msg[:4000], "MultiTradingLauncher — 前端未就绪", icon_warning=True)

    opened, via = _open_web_ui(f"http://127.0.0.1:{WEB_PORT}/auth?forceLogin=1")
    if not opened:
        print(f"[WARN] 自动打开浏览器失败: {via}")
        print(f"[HINT] 请手动访问: {web_url}")
    else:
        print(f"[INFO] 已尝试自动打开浏览器 ({via})")
    print(
        "[HINT] 若使用 QQQ 实盘 Worker：登录后在「首次配置 Setup」→「个人 API Key」创建密钥，"
        "可一键写入 live_worker_config.json；或设置环境变量 QQQ_LIVE_API_KEY。"
    )
    print("[DONE] 启动完成。可关闭本窗口，不影响服务进程。")
    return 0


if __name__ == "__main__":
    _exit_code = 0
    try:
        _exit_code = int(main())
    except KeyboardInterrupt:
        # 关闭控制台 / Ctrl+C 会触发；不应当作崩溃写 crash log，避免 PyInstaller 报 unhandled exception
        print("\n[INFO] 启动器已退出（用户中断）。")
        _flush_stdio()
        _exit_code = 0
    except Exception as e:
        _append_launcher_crash_log(e)
        print(f"[ERROR] 启动器异常: {e}")
        _exit_code = 1
    _pause_before_exit_on_error(_exit_code)
    raise SystemExit(_exit_code)
