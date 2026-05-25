from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from runtime_process_utils import is_pid_alive, read_pid_file

_CONVEX_DEV_LOCK = threading.RLock()
_CONVEX_DEV_PID_NAME = ".convex_dev.pid"
_CONVEX_DEV_STATUS_NAME = ".convex_dev.status.json"
_CONVEX_DEV_OUT_LOG_NAME = "convex_dev.out.log"
_CONVEX_DEV_ERR_LOG_NAME = "convex_dev.err.log"
_LOG_TAIL_BYTES = 24_000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _paths(root: str) -> dict[str, str]:
    root_path = Path(root).resolve()
    return {
        "pid_file": str(root_path / _CONVEX_DEV_PID_NAME),
        "status_file": str(root_path / _CONVEX_DEV_STATUS_NAME),
        "stdout_log": str(root_path / _CONVEX_DEV_OUT_LOG_NAME),
        "stderr_log": str(root_path / _CONVEX_DEV_ERR_LOG_NAME),
    }


def _write_text(path: str, value: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(value, encoding="utf-8")


def _remove_file_silent(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass


def _read_json_file(path: str) -> dict[str, Any]:
    try:
        raw = Path(path).read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_status_file(path: str, payload: dict[str, Any]) -> None:
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(target)
    except Exception:
        pass


def _tail_file(path: str, *, max_lines: int = 80) -> str:
    try:
        target = Path(path)
        if not target.is_file():
            return ""
        size = target.stat().st_size
        with target.open("rb") as f:
            f.seek(max(0, size - _LOG_TAIL_BYTES))
            raw = f.read()
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        if len(lines) > max_lines:
            lines = lines[-max_lines:]
        return "\n".join(lines)
    except Exception:
        return ""


def _powershell_json(script: str, win_subprocess_silent_kwargs: Callable[[], dict[str, Any]]) -> Any:
    if os.name != "nt":
        return None
    try:
        out = subprocess.check_output(  # noqa: S603
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            text=True,
            encoding="utf-8",
            errors="ignore",
            stderr=subprocess.DEVNULL,
            timeout=5,
            **win_subprocess_silent_kwargs(),
        )
        raw = str(out or "").strip()
        if not raw:
            return None
        return json.loads(raw)
    except Exception:
        return None


def _process_commandline(pid: int | None, win_subprocess_silent_kwargs: Callable[[], dict[str, Any]]) -> str:
    if not pid:
        return ""
    if os.name != "nt":
        return ""
    script = (
        f"$p = Get-CimInstance Win32_Process -Filter \"ProcessId={int(pid)}\"; "
        "if ($p) { $p.CommandLine | ConvertTo-Json -Compress }"
    )
    data = _powershell_json(script, win_subprocess_silent_kwargs)
    return str(data or "").strip()


def _looks_like_convex_dev_command(command_line: str) -> bool:
    cmd = str(command_line or "").replace("\\", "/").lower()
    if "convex" not in cmd:
        return False
    if "setup/convex-dev/" in cmd or "get-ciminstance win32_process" in cmd:
        return False
    if "convex dev" in cmd or "convex:dev" in cmd:
        return True
    if "convex/bin/main.js" in cmd and " dev" in cmd:
        return True
    return bool("convex-local-backend" in cmd and "frontend/.convex/local" in cmd and "cli-anonymous-dev" in cmd)


def _detect_convex_dev_processes(win_subprocess_silent_kwargs: Callable[[], dict[str, Any]]) -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    script = (
        "$rows = Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine -and $_.CommandLine -match 'convex' -and $_.CommandLine -match 'dev' } | "
        "Select-Object ProcessId,ParentProcessId,CommandLine; "
        "$rows | ConvertTo-Json -Depth 3 -Compress"
    )
    data = _powershell_json(script, win_subprocess_silent_kwargs)
    if data is None:
        return []
    rows = data if isinstance(data, list) else [data]
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            pid = int(row.get("ProcessId") or 0)
        except Exception:
            continue
        if pid <= 0 or pid == os.getpid():
            continue
        command_line = str(row.get("CommandLine") or "")
        if not _looks_like_convex_dev_command(command_line):
            continue
        try:
            parent_pid = int(row.get("ParentProcessId") or 0) or None
        except Exception:
            parent_pid = None
        out.append(
            {
                "pid": pid,
                "parent_pid": parent_pid,
                "command_line": command_line,
            }
        )
    return sorted(out, key=lambda item: int(item.get("pid") or 0))


def _kill_process_tree(pid: int, win_subprocess_silent_kwargs: Callable[[], dict[str, Any]]) -> bool:
    if pid <= 0 or pid == os.getpid():
        return False
    if os.name == "nt":
        try:
            subprocess.run(  # noqa: S603
                ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
                timeout=8,
                **win_subprocess_silent_kwargs(),
            )
            return not is_pid_alive(int(pid))
        except Exception:
            return False
    try:
        os.killpg(int(pid), signal.SIGTERM)
    except Exception:
        try:
            os.kill(int(pid), signal.SIGTERM)
        except Exception:
            return False
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not is_pid_alive(int(pid)):
            return True
        time.sleep(0.2)
    return not is_pid_alive(int(pid))


def _status_payload(
    *,
    root: str,
    frontend_dir: str,
    managed_processes: dict[str, Any],
    win_subprocess_silent_kwargs: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    paths = _paths(root)
    proc = managed_processes.get("convex_dev")
    subprocess_running = bool(proc is not None and proc.poll() is None)
    subprocess_pid = int(proc.pid) if subprocess_running and getattr(proc, "pid", None) else None

    pid_file_pid = read_pid_file(paths["pid_file"])
    pid_file_alive = bool(pid_file_pid and is_pid_alive(pid_file_pid))
    pid_file_cmd = _process_commandline(pid_file_pid, win_subprocess_silent_kwargs) if pid_file_alive else ""
    pid_file_valid = bool(pid_file_alive and (not pid_file_cmd or _looks_like_convex_dev_command(pid_file_cmd)))
    if pid_file_pid and pid_file_alive and not pid_file_valid:
        _remove_file_silent(paths["pid_file"])

    detected = _detect_convex_dev_processes(win_subprocess_silent_kwargs)
    detected_pids = [int(row["pid"]) for row in detected if str(row.get("pid") or "").isdigit()]

    running = bool(subprocess_running or pid_file_valid or detected_pids)
    tracking = "none"
    pid: int | None = None
    if subprocess_running:
        tracking = "subprocess"
        pid = subprocess_pid
    elif pid_file_valid:
        tracking = "pid_file"
        pid = pid_file_pid
    elif detected_pids:
        tracking = "detected"
        pid = detected_pids[0]

    last_status = _read_json_file(paths["status_file"])
    return {
        "ok": True,
        "running": running,
        "pid": pid,
        "tracking": tracking,
        "pid_file_pid": pid_file_pid,
        "pid_file_valid": pid_file_valid,
        "detected_pids": detected_pids,
        "detected": detected,
        "cwd": str(Path(frontend_dir).resolve()),
        "command": ["npm", "run", "convex:dev"],
        "pid_file": paths["pid_file"],
        "status_file": paths["status_file"],
        "logs": {
            "stdout": paths["stdout_log"],
            "stderr": paths["stderr_log"],
        },
        "last_action": last_status.get("last_action"),
        "started_at": last_status.get("started_at"),
        "stopped_at": last_status.get("stopped_at"),
        "stdout_tail": _tail_file(paths["stdout_log"], max_lines=40),
        "stderr_tail": _tail_file(paths["stderr_log"], max_lines=40),
    }


def convex_dev_status(
    *,
    root: str,
    frontend_dir: str,
    managed_processes: dict[str, Any],
    win_subprocess_silent_kwargs: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    with _CONVEX_DEV_LOCK:
        return _status_payload(
            root=root,
            frontend_dir=frontend_dir,
            managed_processes=managed_processes,
            win_subprocess_silent_kwargs=win_subprocess_silent_kwargs,
        )


def start_convex_dev(
    *,
    root: str,
    frontend_dir: str,
    managed_processes: dict[str, Any],
    win_subprocess_silent_kwargs: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    with _CONVEX_DEV_LOCK:
        current = _status_payload(
            root=root,
            frontend_dir=frontend_dir,
            managed_processes=managed_processes,
            win_subprocess_silent_kwargs=win_subprocess_silent_kwargs,
        )
        if current.get("running"):
            return {**current, "action": "already_running"}

        frontend_path = Path(frontend_dir).resolve()
        if not frontend_path.is_dir():
            raise FileNotFoundError(f"frontend_dir_not_found: {frontend_path}")
        if not (frontend_path / "package.json").is_file():
            raise FileNotFoundError(f"package_json_not_found: {frontend_path / 'package.json'}")

        paths = _paths(root)
        Path(paths["stdout_log"]).parent.mkdir(parents=True, exist_ok=True)
        stdout = open(paths["stdout_log"], "ab", buffering=0)
        stderr = open(paths["stderr_log"], "ab", buffering=0)
        env = os.environ.copy()
        env["MT_CONVEX_DEV_MANAGED"] = "1"
        env["MT_CONVEX_DEV_ROOT"] = str(Path(root).resolve())

        if os.name == "nt":
            command = ["cmd.exe", "/d", "/c", "npm", "run", "convex:dev"]
        else:
            command = ["npm", "run", "convex:dev"]

        try:
            proc = subprocess.Popen(  # noqa: S603
                command,
                cwd=str(frontend_path),
                env=env,
                stdout=stdout,
                stderr=stderr,
                **win_subprocess_silent_kwargs(),
            )
        finally:
            try:
                stdout.close()
            except Exception:
                pass
            try:
                stderr.close()
            except Exception:
                pass

        managed_processes["convex_dev"] = proc
        _write_text(paths["pid_file"], str(int(proc.pid)))
        _write_status_file(
            paths["status_file"],
            {
                "last_action": "started",
                "started_at": _now_iso(),
                "pid": int(proc.pid),
                "cwd": str(frontend_path),
                "command": command,
            },
        )
        time.sleep(0.4)
        out = _status_payload(
            root=root,
            frontend_dir=frontend_dir,
            managed_processes=managed_processes,
            win_subprocess_silent_kwargs=win_subprocess_silent_kwargs,
        )
        return {**out, "action": "started"}


def stop_convex_dev(
    *,
    root: str,
    frontend_dir: str,
    managed_processes: dict[str, Any],
    win_subprocess_silent_kwargs: Callable[[], dict[str, Any]],
    include_detected: bool = True,
) -> dict[str, Any]:
    with _CONVEX_DEV_LOCK:
        paths = _paths(root)
        before = _status_payload(
            root=root,
            frontend_dir=frontend_dir,
            managed_processes=managed_processes,
            win_subprocess_silent_kwargs=win_subprocess_silent_kwargs,
        )
        candidate_pids: set[int] = set()
        proc = managed_processes.get("convex_dev")
        if proc is not None and getattr(proc, "pid", None):
            candidate_pids.add(int(proc.pid))
        pid_file_pid = read_pid_file(paths["pid_file"])
        if pid_file_pid:
            candidate_pids.add(int(pid_file_pid))
        if include_detected:
            for pid in before.get("detected_pids") or []:
                try:
                    candidate_pids.add(int(pid))
                except Exception:
                    continue

        stopped_pids: list[int] = []
        for pid in sorted(candidate_pids):
            if _kill_process_tree(pid, win_subprocess_silent_kwargs):
                stopped_pids.append(pid)

        managed_processes.pop("convex_dev", None)
        _remove_file_silent(paths["pid_file"])
        _write_status_file(
            paths["status_file"],
            {
                "last_action": "stopped",
                "stopped_at": _now_iso(),
                "stopped_pids": stopped_pids,
            },
        )
        time.sleep(0.3)
        after = _status_payload(
            root=root,
            frontend_dir=frontend_dir,
            managed_processes=managed_processes,
            win_subprocess_silent_kwargs=win_subprocess_silent_kwargs,
        )
        return {**after, "action": "stopped", "stopped_pids": stopped_pids, "before": before}


def restart_convex_dev(
    *,
    root: str,
    frontend_dir: str,
    managed_processes: dict[str, Any],
    win_subprocess_silent_kwargs: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    with _CONVEX_DEV_LOCK:
        stopped = stop_convex_dev(
            root=root,
            frontend_dir=frontend_dir,
            managed_processes=managed_processes,
            win_subprocess_silent_kwargs=win_subprocess_silent_kwargs,
            include_detected=True,
        )
        started = start_convex_dev(
            root=root,
            frontend_dir=frontend_dir,
            managed_processes=managed_processes,
            win_subprocess_silent_kwargs=win_subprocess_silent_kwargs,
        )
        return {**started, "action": "restarted", "stopped": stopped}
