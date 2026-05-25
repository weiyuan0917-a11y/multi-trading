from __future__ import annotations

from typing import Any, Optional

from runtime_process_utils import is_pid_alive, managed_subprocess_status, read_pid_file


def build_setup_services_status(
    *,
    managed_processes: dict[str, Any],
    feishu_pid_file: str,
    auto_trader_supervisor_pid_file: str,
    auto_runtime: dict[str, Any],
    qqq_0dte_live_worker_pid_file: str,
    qqq_0dte_live_runtime: dict[str, Any],
    qqq_1dte_live_worker_pid_file: str,
    qqq_1dte_live_runtime: dict[str, Any],
) -> dict[str, Any]:
    """
    Build `/setup/services/status` response payload from managed-process handles and worker runtime.
    Keep response schema identical to the existing API contract.
    """
    feishu_running, feishu_tracking, feishu_pid_out = managed_subprocess_status(
        managed_processes.get("feishu_bot"), feishu_pid_file
    )
    sup_running, sup_tracking, sup_pid_out = managed_subprocess_status(
        managed_processes.get("auto_trader_supervisor"), auto_trader_supervisor_pid_file
    )
    qqq_running, qqq_tracking, qqq_pid_out = managed_subprocess_status(
        managed_processes.get("qqq_0dte_live_worker"), qqq_0dte_live_worker_pid_file
    )
    qqq1_running, qqq1_tracking, qqq1_pid_out = managed_subprocess_status(
        managed_processes.get("qqq_1dte_live_worker"), qqq_1dte_live_worker_pid_file
    )

    wr = bool(auto_runtime.get("worker_running"))
    wp_int = auto_runtime.get("worker_pid")
    if not isinstance(wp_int, int):
        wp_int = None
    wp_alive = is_pid_alive(wp_int) if wp_int else False
    worker_tracking = "pid_file" if wp_alive else ("runtime" if wr else "none")
    worker_pid_out: Optional[int] = wp_int if wr or wp_alive else None

    q_wr = bool(qqq_0dte_live_runtime.get("worker_running"))
    q_wp = qqq_0dte_live_runtime.get("pid")
    q_wp_int: Optional[int] = None
    if q_wp is not None and str(q_wp).strip().isdigit():
        q_wp_int = int(str(q_wp).strip())
    if q_wp_int is None:
        q_wp_int = read_pid_file(qqq_0dte_live_worker_pid_file)
    q_alive = is_pid_alive(q_wp_int) if q_wp_int else False
    q_display_running = bool(qqq_running or q_alive or q_wr)
    q_track_out = str(qqq_tracking)
    if q_track_out == "none" and q_alive:
        q_track_out = "pid_file"
    elif q_track_out == "none" and q_wr:
        q_track_out = "runtime"
    q_pid_disp: Optional[int] = qqq_pid_out if qqq_pid_out else q_wp_int

    q1_wr = bool(qqq_1dte_live_runtime.get("worker_running"))
    q1_wp = qqq_1dte_live_runtime.get("pid")
    q1_wp_int: Optional[int] = None
    if q1_wp is not None and str(q1_wp).strip().isdigit():
        q1_wp_int = int(str(q1_wp).strip())
    if q1_wp_int is None:
        q1_wp_int = read_pid_file(qqq_1dte_live_worker_pid_file)
    q1_alive = is_pid_alive(q1_wp_int) if q1_wp_int else False
    q1_display_running = bool(qqq1_running or q1_alive or q1_wr)
    q1_track_out = str(qqq1_tracking)
    if q1_track_out == "none" and q1_alive:
        q1_track_out = "pid_file"
    elif q1_track_out == "none" and q1_wr:
        q1_track_out = "runtime"
    q1_pid_disp: Optional[int] = qqq1_pid_out if qqq1_pid_out else q1_wp_int

    return {
        "feishu_bot_running": feishu_running,
        "feishu_bot_tracking": feishu_tracking,
        "feishu_bot_pid": feishu_pid_out,
        "auto_trader_scheduler_running": bool(auto_runtime.get("worker_running")),
        "auto_trader_supervisor_running": sup_running,
        "auto_trader_supervisor_tracking": sup_tracking,
        "auto_trader_supervisor_pid": sup_pid_out,
        "auto_trader_worker_tracking": worker_tracking,
        "auto_trader_worker_pid": worker_pid_out,
        "auto_trader_runtime": auto_runtime,
        "qqq_0dte_live_running": q_display_running,
        "qqq_0dte_live_tracking": q_track_out,
        "qqq_0dte_live_pid": q_pid_disp if q_display_running else None,
        "qqq_0dte_live_runtime": qqq_0dte_live_runtime,
        "qqq_1dte_live_running": q1_display_running,
        "qqq_1dte_live_tracking": q1_track_out,
        "qqq_1dte_live_pid": q1_pid_disp if q1_display_running else None,
        "qqq_1dte_live_runtime": qqq_1dte_live_runtime,
    }

