from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from typing import Any, Callable

_SETUP_SERVICE_OP_LOCK = threading.RLock()
_FRONTEND_PRIMARY_PORT = 3010
_FRONTEND_LEGACY_PORTS = (3000,)


def start_services(
    *,
    start_feishu_bot: bool,
    enable_auto_trader: bool,
    enable_qqq_0dte_live: bool,
    enable_qqq_1dte_live: bool,
    enable_stock_options_swing: bool = False,
    auto_trader: Any,
    start_auto_trader_worker: Callable[[str | None], str] | Callable[[], str],
    start_qqq_0dte_live_worker: Callable[[str | None], str] | Callable[[], str],
    start_qqq_1dte_live_worker: Callable[[str | None], str] | Callable[[], str],
    start_stock_options_swing_worker: Callable[[str | None], str] | Callable[[], str] | None = None,
    managed_processes: dict[str, Any],
    root: str,
    mcp_dir: str,
    win_subprocess_silent_kwargs: Callable[[], dict[str, Any]],
    owner_id: str | None = None,
) -> dict[str, Any]:
    with _SETUP_SERVICE_OP_LOCK:
        started: dict[str, Any] = {}
        if enable_auto_trader:
            cfg = auto_trader.get_config()
            if not cfg.get("enabled"):
                auto_trader.update_config({"enabled": True})
            auto_trader.stop_scheduler()
            try:
                started["auto_trader"] = start_auto_trader_worker(owner_id)
            except TypeError:
                started["auto_trader"] = start_auto_trader_worker()
        else:
            started["auto_trader"] = "skipped"

        if enable_qqq_0dte_live:
            try:
                started["qqq_0dte_live"] = start_qqq_0dte_live_worker(owner_id)
            except TypeError:
                started["qqq_0dte_live"] = start_qqq_0dte_live_worker()
        else:
            started["qqq_0dte_live"] = "skipped"

        if enable_qqq_1dte_live:
            try:
                started["qqq_1dte_live"] = start_qqq_1dte_live_worker(owner_id)
            except TypeError:
                started["qqq_1dte_live"] = start_qqq_1dte_live_worker()
        else:
            started["qqq_1dte_live"] = "skipped"

        if enable_stock_options_swing:
            if start_stock_options_swing_worker is None:
                started["stock_options_swing"] = "unavailable"
            else:
                try:
                    started["stock_options_swing"] = start_stock_options_swing_worker(owner_id)
                except TypeError:
                    started["stock_options_swing"] = start_stock_options_swing_worker()
        else:
            started["stock_options_swing"] = "skipped"

        if start_feishu_bot:
            p = managed_processes.get("feishu_bot")
            if p and p.poll() is None:
                started["feishu_bot"] = "already_running"
            else:
                env = os.environ.copy()
                env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
                script = os.path.join(mcp_dir, "feishu_command_bot.py")
                managed_processes["feishu_bot"] = subprocess.Popen(  # noqa: S603
                    [sys.executable, "-u", script],
                    cwd=root,
                    env=env,
                    **win_subprocess_silent_kwargs(),
                )
                started["feishu_bot"] = "started"
        else:
            started["feishu_bot"] = "skipped"
        return {"ok": True, "started": started}


def stop_services(
    *,
    stop_auto_trader: bool,
    stop_qqq_0dte_live: bool,
    stop_qqq_1dte_live: bool,
    stop_stock_options_swing: bool = False,
    stop_feishu_bot: bool,
    auto_trader: Any,
    stop_auto_trader_worker: Callable[[], str],
    stop_qqq_0dte_live_worker: Callable[[], str],
    stop_qqq_1dte_live_worker: Callable[[], str],
    stop_stock_options_swing_worker: Callable[[], str] | None = None,
    stop_feishu_bot_managed_or_pidfile: Callable[[], str],
    wait_auto_trader_stopped: Callable[[float], bool] | None = None,
    stop_confirm_timeout_seconds: float = 8.0,
) -> dict[str, Any]:
    with _SETUP_SERVICE_OP_LOCK:
        stopped: dict[str, Any] = {}
        if stop_auto_trader:
            auto_trader.stop_scheduler()
            stopped["auto_trader"] = stop_auto_trader_worker()
            if wait_auto_trader_stopped is not None:
                confirmed = bool(wait_auto_trader_stopped(float(stop_confirm_timeout_seconds)))
                stopped["auto_trader_confirmed_stopped"] = confirmed
                if not confirmed:
                    stopped["auto_trader_confirm_timeout_seconds"] = float(stop_confirm_timeout_seconds)
        else:
            stopped["auto_trader"] = "skipped"

        if stop_qqq_0dte_live:
            stopped["qqq_0dte_live"] = stop_qqq_0dte_live_worker()
        else:
            stopped["qqq_0dte_live"] = "skipped"

        if stop_qqq_1dte_live:
            stopped["qqq_1dte_live"] = stop_qqq_1dte_live_worker()
        else:
            stopped["qqq_1dte_live"] = "skipped"

        if stop_stock_options_swing:
            stopped["stock_options_swing"] = stop_stock_options_swing_worker() if stop_stock_options_swing_worker else "unavailable"
        else:
            stopped["stock_options_swing"] = "skipped"

        if stop_feishu_bot:
            stopped["feishu_bot"] = stop_feishu_bot_managed_or_pidfile()
        else:
            stopped["feishu_bot"] = "skipped"
        return {"ok": True, "stopped": stopped}


def pids_listening_on_port(port: int, *, win_subprocess_silent_kwargs: Callable[[], dict[str, Any]]) -> list[int]:
    if os.name != "nt":
        return []
    try:
        out = subprocess.check_output(  # noqa: S603
            ["netstat", "-ano"],
            text=True,
            encoding="utf-8",
            errors="ignore",
            **win_subprocess_silent_kwargs(),
        )
    except Exception:
        return []
    pids: set[int] = set()
    needle = f":{int(port)}"
    for line in out.splitlines():
        if "LISTENING" not in line or needle not in line:
            continue
        parts = line.split()
        if not parts:
            continue
        try:
            pid = int(parts[-1])
            if pid > 0:
                pids.add(pid)
        except Exception:
            continue
    return sorted(pids)


def frontend_pids(
    *,
    win_subprocess_silent_kwargs: Callable[[], dict[str, Any]],
) -> list[int]:
    ports = (_FRONTEND_PRIMARY_PORT, *_FRONTEND_LEGACY_PORTS)
    pids: set[int] = set()
    for port in ports:
        for pid in pids_listening_on_port(port, win_subprocess_silent_kwargs=win_subprocess_silent_kwargs):
            pids.add(pid)
    return sorted(pids)


def kill_pids(
    pids: list[int],
    *,
    current_pid: int,
    win_subprocess_silent_kwargs: Callable[[], dict[str, Any]],
) -> list[int]:
    killed: list[int] = []
    for pid in pids:
        if pid == current_pid:
            continue
        try:
            proc = subprocess.run(  # noqa: S603
                ["taskkill", "/PID", str(pid), "/F"],
                check=False,
                capture_output=True,
                text=True,
                **win_subprocess_silent_kwargs(),
            )
            if proc.returncode == 0:
                killed.append(pid)
        except Exception:
            continue
    return killed


def pause_backend_watchdog(
    *,
    watchdog_pause_file: str,
    watchdog_pid_file: str,
    read_pid_file: Callable[[str], int | None],
    is_pid_alive: Callable[[int | None], bool],
    win_subprocess_silent_kwargs: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {"pause_file": watchdog_pause_file, "watchdog_pid": None, "watchdog_killed": False}
    try:
        with open(watchdog_pause_file, "w", encoding="utf-8") as f:
            f.write(str(datetime.now().isoformat()))
    except Exception:
        pass
    pid = read_pid_file(watchdog_pid_file)
    result["watchdog_pid"] = pid
    if pid and is_pid_alive(pid):
        try:
            subprocess.run(  # noqa: S603
                ["taskkill", "/PID", str(pid), "/F"],
                check=False,
                capture_output=True,
                text=True,
                **win_subprocess_silent_kwargs(),
            )
            result["watchdog_killed"] = True
        except Exception:
            result["watchdog_killed"] = False
    return result


def schedule_backend_shutdown(delay_seconds: float = 1.2) -> None:
    def _shutdown():
        try:
            time.sleep(max(0.2, float(delay_seconds)))
        finally:
            os._exit(0)

    threading.Thread(target=_shutdown, daemon=True).start()


def stop_all_services(
    *,
    stop_backend: bool,
    stop_frontend: bool,
    stop_feishu_bot: bool,
    stop_auto_trader: bool,
    stop_qqq_0dte_live: bool,
    stop_qqq_1dte_live: bool,
    stop_stock_options_swing: bool = False,
    auto_trader: Any,
    stop_auto_trader_worker: Callable[[], str],
    stop_qqq_0dte_live_worker: Callable[[], str],
    stop_qqq_1dte_live_worker: Callable[[], str],
    stop_stock_options_swing_worker: Callable[[], str] | None = None,
    stop_feishu_bot_managed_or_pidfile: Callable[[], str],
    watchdog_pause_file: str,
    watchdog_pid_file: str,
    read_pid_file: Callable[[str], int | None],
    is_pid_alive: Callable[[int | None], bool],
    win_subprocess_silent_kwargs: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    with _SETUP_SERVICE_OP_LOCK:
        stopped: dict[str, Any] = {}

        if stop_auto_trader:
            try:
                auto_trader.stop_scheduler()
                stopped["auto_trader"] = stop_auto_trader_worker()
            except Exception as e:
                stopped["auto_trader"] = f"error: {e}"
        else:
            stopped["auto_trader"] = "skipped"

        if stop_qqq_0dte_live:
            try:
                stopped["qqq_0dte_live"] = stop_qqq_0dte_live_worker()
            except Exception as e:
                stopped["qqq_0dte_live"] = f"error: {e}"
        else:
            stopped["qqq_0dte_live"] = "skipped"

        if stop_qqq_1dte_live:
            try:
                stopped["qqq_1dte_live"] = stop_qqq_1dte_live_worker()
            except Exception as e:
                stopped["qqq_1dte_live"] = f"error: {e}"
        else:
            stopped["qqq_1dte_live"] = "skipped"

        if stop_stock_options_swing:
            try:
                stopped["stock_options_swing"] = (
                    stop_stock_options_swing_worker() if stop_stock_options_swing_worker else "unavailable"
                )
            except Exception as e:
                stopped["stock_options_swing"] = f"error: {e}"
        else:
            stopped["stock_options_swing"] = "skipped"

        if stop_feishu_bot:
            try:
                stopped["feishu_bot"] = stop_feishu_bot_managed_or_pidfile()
            except Exception as e:
                stopped["feishu_bot"] = f"error: {e}"
        else:
            stopped["feishu_bot"] = "skipped"

        if stop_frontend:
            detected_frontend_pids = frontend_pids(win_subprocess_silent_kwargs=win_subprocess_silent_kwargs)
            killed = kill_pids(
                detected_frontend_pids,
                current_pid=os.getpid(),
                win_subprocess_silent_kwargs=win_subprocess_silent_kwargs,
            )
            stopped["frontend"] = {
                "ports": [_FRONTEND_PRIMARY_PORT, *_FRONTEND_LEGACY_PORTS],
                "detected_pids": detected_frontend_pids,
                "killed_pids": killed,
            }
        else:
            stopped["frontend"] = "skipped"

        if stop_backend:
            stopped["watchdog"] = pause_backend_watchdog(
                watchdog_pause_file=watchdog_pause_file,
                watchdog_pid_file=watchdog_pid_file,
                read_pid_file=read_pid_file,
                is_pid_alive=is_pid_alive,
                win_subprocess_silent_kwargs=win_subprocess_silent_kwargs,
            )
            stopped["backend"] = "scheduled_shutdown"
            schedule_backend_shutdown()
        else:
            stopped["backend"] = "skipped"

        return {"ok": True, "stopped": stopped, "message": "停止命令已发送"}

