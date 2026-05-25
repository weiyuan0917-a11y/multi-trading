"""
mcp_extensions.py - MCP Server 扩展工具
新增功能：
  1. 交易日志系统（8个工具）
  2. 智能告警系统（7个工具）
  3. QQQ 0DTE/1DTE live worker MCP 封装工具
在 longport_mcp_server.py 中导入使用
"""
import importlib
import json
import os
import sys
from market_mcp_tools import (
    get_market_analysis_tools, 
    MARKET_TOOL_DISPATCH
)
from notification_mcp_tools import (
     NOTIFICATION_TOOL_DISPATCH
)
from datetime import datetime, date, timedelta
from trade_journal import (
    get_journal, TradeEntry, EmotionTag,
    generate_review_report, analyze_decision_quality as _analyze_decision_quality_core
)
from alert_manager import (
    get_alert_manager, Alert, AlertType, AlertStatus,
    format_alert_message
)
import mcp.types as types
from typing import Any


# 新增这个函数
def get_market_tools() -> list[types.Tool]:
    """返回市场分析工具"""
    return get_market_analysis_tools()

def get_notification_tools() -> list[types.Tool]:
    """返回通知工具"""
    from notification_mcp_tools import get_notification_tools as _get_tools
    return _get_tools()


def get_qqq_live_tools() -> list[types.Tool]:
    """返回 QQQ 0DTE/1DTE live worker MCP 工具定义。"""
    return [
        types.Tool(
            name="qqq_live_get_config",
            description="读取 QQQ live worker 配置（instance=0dte/1dte）",
            inputSchema={
                "type": "object",
                "properties": {
                    "instance": {"type": "string", "enum": ["0dte", "1dte"], "description": "实例名，默认 0dte"},
                },
                "required": [],
            },
        ),
        types.Tool(
            name="qqq_live_update_config",
            description="更新 QQQ live worker 配置（支持部分字段 patch）",
            inputSchema={
                "type": "object",
                "properties": {
                    "instance": {"type": "string", "enum": ["0dte", "1dte"], "description": "实例名，默认 0dte"},
                    "patch": {"type": "object", "description": "配置补丁对象（与 live-worker-config PUT body 一致）"},
                },
                "required": ["patch"],
            },
        ),
        types.Tool(
            name="qqq_live_get_decision_tail",
            description="读取 QQQ live worker 决策尾日志（JSONL tail）",
            inputSchema={
                "type": "object",
                "properties": {
                    "instance": {"type": "string", "enum": ["0dte", "1dte"], "description": "实例名，默认 0dte"},
                    "limit": {"type": "integer", "description": "返回条数，默认 20（1-100）"},
                },
                "required": [],
            },
        ),
        types.Tool(
            name="qqq_live_get_recommendation",
            description="读取 QQQ 系统推荐策略（worker 文件或后端即时计算）",
            inputSchema={
                "type": "object",
                "properties": {
                    "instance": {"type": "string", "enum": ["0dte", "1dte"], "description": "实例名，默认 0dte"},
                },
                "required": [],
            },
        ),
        types.Tool(
            name="qqq_live_start_worker",
            description="启动 QQQ live worker（instance=0dte/1dte）",
            inputSchema={
                "type": "object",
                "properties": {
                    "instance": {"type": "string", "enum": ["0dte", "1dte"], "description": "实例名，默认 0dte"},
                },
                "required": [],
            },
        ),
        types.Tool(
            name="qqq_live_stop_worker",
            description="停止 QQQ live worker（instance=0dte/1dte）",
            inputSchema={
                "type": "object",
                "properties": {
                    "instance": {"type": "string", "enum": ["0dte", "1dte"], "description": "实例名，默认 0dte"},
                },
                "required": [],
            },
        ),
        types.Tool(
            name="qqq_live_services_status",
            description="获取 setup 服务状态（含 qqq_0dte_live_* 与 qqq_1dte_live_*）",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
    ]

def get_agent_strategy_lab_tools() -> list[types.Tool]:
    """Return Agent Strategy Lab MCP tool definitions."""
    instance_schema = {
        "type": "string",
        "enum": ["0dte", "1dte"],
        "description": "QQQ option worker instance. Defaults to 0dte.",
    }
    strategy_schema = {
        "type": "string",
        "enum": ["morning_strangle", "morning_double_strangle", "morning_directional"],
        "description": "Strategy variant to generate and validate.",
    }
    dimension_schema = {
        "type": "string",
        "enum": ["risk_controls", "leg_gap", "time_window", "combined"],
        "description": "Research dimension for candidate generation.",
    }
    generator_schema = {
        "type": "string",
        "enum": ["deterministic", "tradingagents"],
        "description": "Candidate generator. deterministic is the local default.",
    }
    return [
        types.Tool(
            name="agent_strategy_lab_status",
            description="Read Agent Strategy Lab status, data quality, last run, and recent approvals.",
            inputSchema={
                "type": "object",
                "properties": {"instance": instance_schema},
                "required": [],
            },
        ),
        types.Tool(
            name="agent_strategy_lab_create_matrix_task",
            description=(
                "Create an async Agent Strategy Lab candidate validation task. "
                "Returns task_id immediately; poll agent_strategy_lab_get_task for progress."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "instance": instance_schema,
                    "strategy_variant": strategy_schema,
                    "research_dimension": dimension_schema,
                    "candidate_generator": generator_schema,
                    "validation_windows_days": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 1, "maximum": 3650},
                        "description": "Rolling validation windows, for example [60, 120, 180].",
                    },
                    "max_candidates": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "description": "Maximum generated candidates per task.",
                    },
                    "kline": {"type": "string", "description": "K-line period. Defaults to 1m."},
                    "use_server_kline_cache": {
                        "type": "boolean",
                        "description": "Use pre-downloaded server K-line cache when available.",
                    },
                    "rth_only": {"type": "boolean", "description": "Use regular trading hours only."},
                },
                "required": [],
            },
        ),
        types.Tool(
            name="agent_strategy_lab_get_task",
            description="Get Agent Strategy Lab async task progress and final run result.",
            inputSchema={
                "type": "object",
                "properties": {"task_id": {"type": "string", "description": "Task id returned by create_matrix_task."}},
                "required": ["task_id"],
            },
        ),
        types.Tool(
            name="agent_strategy_lab_get_best_candidates",
            description="Rank and summarize the best candidates from a run, task, or latest run.",
            inputSchema={
                "type": "object",
                "properties": {
                    "run_id": {"type": "string", "description": "Optional completed Lab run id."},
                    "task_id": {"type": "string", "description": "Optional async task id."},
                    "instance": instance_schema,
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "Maximum candidates to return."},
                    "passed_only": {"type": "boolean", "description": "Only return candidates that passed validation."},
                },
                "required": [],
            },
        ),
        types.Tool(
            name="agent_strategy_lab_preview_candidate_diff",
            description="Preview the exact live_worker_config strategy_config patch for a candidate. This does not write config.",
            inputSchema={
                "type": "object",
                "properties": {
                    "run_id": {"type": "string", "description": "Completed Lab run id."},
                    "candidate_id": {"type": "string", "description": "Candidate id from the run."},
                },
                "required": ["run_id", "candidate_id"],
            },
        ),
    ]


def _normalize_instance(args: dict[str, Any]) -> str:
    ins = str((args or {}).get("instance", "0dte") or "0dte").strip().lower()
    return "1dte" if ins == "1dte" else "0dte"


def _runtime_bridge_module():
    """
    懒加载 api.runtime_bridge，避免 MCP 启动期绑定 API 依赖。
    """
    cur_dir = os.path.dirname(__file__)
    root_dir = os.path.dirname(cur_dir)
    if root_dir and root_dir not in sys.path:
        sys.path.insert(0, root_dir)
    mod = importlib.import_module("api.runtime_bridge")
    return mod


def _agent_strategy_lab_service_module():
    """
    Lazy-load Agent Strategy Lab service so MCP startup is not coupled to API imports.
    """
    cur_dir = os.path.dirname(__file__)
    root_dir = os.path.dirname(cur_dir)
    if root_dir and root_dir not in sys.path:
        sys.path.insert(0, root_dir)
    mod = importlib.import_module("api.services.agent_strategy_lab_service")
    return mod


async def qqq_live_get_config(args: dict) -> list[types.TextContent]:
    rt = _runtime_bridge_module()
    obj = args if isinstance(args, dict) else {}
    ins = _normalize_instance(obj)
    owner_id = str(obj.get("owner_id") or obj.get("owner") or "").strip().lower() or None
    cfg = rt.qqq_1dte_live_worker_config_get(owner_id=owner_id) if ins == "1dte" else rt.qqq_0dte_live_worker_config_get(owner_id=owner_id)
    return [types.TextContent(type="text", text=json.dumps({"instance": ins, "config": cfg}, ensure_ascii=False, indent=2))]


async def qqq_live_update_config(args: dict) -> list[types.TextContent]:
    rt = _runtime_bridge_module()
    obj = args if isinstance(args, dict) else {}
    ins = _normalize_instance(obj)
    owner_id = str(obj.get("owner_id") or obj.get("owner") or "").strip().lower() or None
    patch = obj.get("patch")
    if not isinstance(patch, dict):
        return [types.TextContent(type="text", text=json.dumps({"ok": False, "error": "patch 必须是对象"}, ensure_ascii=False, indent=2))]
    ret = rt.qqq_1dte_live_worker_config_put(patch, owner_id=owner_id) if ins == "1dte" else rt.qqq_0dte_live_worker_config_put(patch, owner_id=owner_id)
    ret = dict(ret if isinstance(ret, dict) else {"ok": False, "error": "unknown"})
    ret["instance"] = ins
    return [types.TextContent(type="text", text=json.dumps(ret, ensure_ascii=False, indent=2))]


async def qqq_live_get_decision_tail(args: dict) -> list[types.TextContent]:
    rt = _runtime_bridge_module()
    obj = args if isinstance(args, dict) else {}
    ins = _normalize_instance(obj)
    lim = max(1, min(100, int(obj.get("limit", 20) or 20)))
    ret = rt.qqq_1dte_live_worker_decision_tail_get(lim) if ins == "1dte" else rt.qqq_0dte_live_worker_decision_tail_get(lim)
    ret = dict(ret if isinstance(ret, dict) else {"ok": False, "error": "unknown"})
    ret["instance"] = ins
    return [types.TextContent(type="text", text=json.dumps(ret, ensure_ascii=False, indent=2))]


async def qqq_live_get_recommendation(args: dict) -> list[types.TextContent]:
    rt = _runtime_bridge_module()
    ins = _normalize_instance(args if isinstance(args, dict) else {})
    ret = rt.qqq_1dte_strategy_recommendation_get() if ins == "1dte" else rt.qqq_0dte_strategy_recommendation_get()
    out = dict(ret if isinstance(ret, dict) else {"ok": False, "error": "unknown"})
    out["instance"] = ins
    return [types.TextContent(type="text", text=json.dumps(out, ensure_ascii=False, indent=2))]


async def qqq_live_start_worker(args: dict) -> list[types.TextContent]:
    main_mod = importlib.import_module("api.main")
    ins = _normalize_instance(args if isinstance(args, dict) else {})
    status = main_mod._start_qqq_1dte_live_worker() if ins == "1dte" else main_mod._start_qqq_0dte_live_worker()
    return [types.TextContent(type="text", text=json.dumps({"ok": True, "instance": ins, "status": str(status)}, ensure_ascii=False, indent=2))]


async def qqq_live_stop_worker(args: dict) -> list[types.TextContent]:
    main_mod = importlib.import_module("api.main")
    ins = _normalize_instance(args if isinstance(args, dict) else {})
    status = main_mod._stop_qqq_1dte_live_worker() if ins == "1dte" else main_mod._stop_qqq_0dte_live_worker()
    return [types.TextContent(type="text", text=json.dumps({"ok": True, "instance": ins, "status": str(status)}, ensure_ascii=False, indent=2))]


async def qqq_live_services_status(args: dict) -> list[types.TextContent]:
    rt = _runtime_bridge_module()
    ret = rt.setup_services_status()
    return [types.TextContent(type="text", text=json.dumps(ret if isinstance(ret, dict) else {"ok": False}, ensure_ascii=False, indent=2))]
# ============================================================
# 交易日志 MCP 工具
# ============================================================

def _text_json(payload: Any) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _agent_lab_payload(args: dict[str, Any]) -> dict[str, Any]:
    obj = args if isinstance(args, dict) else {}
    svc = _agent_strategy_lab_service_module()
    windows_raw = obj.get("validation_windows_days")
    windows: list[int] = []
    if isinstance(windows_raw, list):
        for item in windows_raw:
            days = max(1, min(3650, _as_int(item, 0)))
            if days and days not in windows:
                windows.append(days)
    payload = {
        "instance": _normalize_instance(obj),
        "strategy_variant": svc.normalize_lab_strategy_variant(obj.get("strategy_variant")),
        "research_dimension": svc.normalize_research_dimension(obj.get("research_dimension")),
        "candidate_generator": svc.normalize_candidate_generator(obj.get("candidate_generator")),
        "validation_windows_days": windows[:6] or [60, 120, 180],
        "max_candidates": max(1, min(10, _as_int(obj.get("max_candidates"), 3))),
        "kline": str(obj.get("kline") or "1m").strip() or "1m",
        "use_server_kline_cache": bool(obj.get("use_server_kline_cache", True)),
        "rth_only": bool(obj.get("rth_only", True)),
    }
    return payload


def _agent_lab_run_from_args(args: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    obj = args if isinstance(args, dict) else {}
    svc = _agent_strategy_lab_service_module()
    run_id = str(obj.get("run_id") or "").strip()
    task_id = str(obj.get("task_id") or "").strip()
    if run_id:
        out = svc.get_lab_run(run_id)
        run = out.get("run") if isinstance(out, dict) else None
        return (run if isinstance(run, dict) else None), {"source": "run_id", "run_id": run_id}
    if task_id:
        task_out = svc.get_lab_task(task_id)
        task = task_out.get("task") if isinstance(task_out, dict) else None
        if not isinstance(task, dict):
            return None, {"source": "task_id", "task_id": task_id, "error": "task_not_found"}
        run = task.get("run") if isinstance(task.get("run"), dict) else None
        if not run and task.get("run_id"):
            run_out = svc.get_lab_run(str(task.get("run_id")))
            run = run_out.get("run") if isinstance(run_out, dict) else None
        return (run if isinstance(run, dict) else None), {
            "source": "task_id",
            "task_id": task_id,
            "task_status": task.get("status"),
            "run_id": task.get("run_id"),
        }
    ins = _normalize_instance(obj)
    runs_out = svc.list_lab_runs(instance=ins, limit=10)
    runs = runs_out.get("items") if isinstance(runs_out, dict) else []
    for run in runs if isinstance(runs, list) else []:
        if isinstance(run, dict) and isinstance(run.get("candidates"), list):
            return run, {"source": "latest_run", "instance": ins, "run_id": run.get("run_id")}
    return None, {"source": "latest_run", "instance": ins, "error": "run_not_found"}


def _candidate_score(candidate: dict[str, Any]) -> float:
    validation = candidate.get("validation") if isinstance(candidate.get("validation"), dict) else {}
    summary = validation.get("summary") if isinstance(validation.get("summary"), dict) else {}
    rows = validation.get("rows") if isinstance(validation.get("rows"), list) else []
    return_values: list[float] = []
    win_values: list[float] = []
    closed_values: list[int] = []
    drawdown_values: list[float] = []
    for row in rows:
        metrics = row.get("metrics") if isinstance(row, dict) and isinstance(row.get("metrics"), dict) else None
        if not metrics:
            continue
        if metrics.get("return_pct") is not None:
            return_values.append(_as_float(metrics.get("return_pct"), 0.0))
        if metrics.get("win_rate_pct") is not None:
            win_values.append(_as_float(metrics.get("win_rate_pct"), 0.0))
        closed_values.append(_as_int(metrics.get("closed_trades"), 0))
        drawdown_values.append(abs(_as_float(metrics.get("max_drawdown_usd"), 0.0)))
    avg_return = (
        sum(return_values) / len(return_values)
        if return_values
        else _as_float(summary.get("avg_return_pct"), 0.0)
    )
    avg_win = sum(win_values) / len(win_values) if win_values else 0.0
    min_closed = min(closed_values) if closed_values else _as_int(summary.get("min_closed_trades"), 0)
    worst_drawdown = max(drawdown_values) if drawdown_values else abs(_as_float(summary.get("worst_drawdown_usd"), 0.0))
    blockers = validation.get("blockers") if isinstance(validation.get("blockers"), list) else []
    score = avg_return * 10.0 + avg_win * 0.05 + min_closed * 0.25 - worst_drawdown * 0.001 - len(blockers) * 20.0
    if validation.get("passed"):
        score += 1000.0
    return round(score, 6)


def _candidate_summary(candidate: dict[str, Any]) -> dict[str, Any]:
    validation = candidate.get("validation") if isinstance(candidate.get("validation"), dict) else {}
    return {
        "candidate_id": candidate.get("candidate_id"),
        "title": candidate.get("title"),
        "strategy_variant": candidate.get("strategy_variant"),
        "score": _candidate_score(candidate),
        "passed": bool(validation.get("passed")),
        "status": candidate.get("status"),
        "reason": candidate.get("reason"),
        "strategy_config_patch": candidate.get("strategy_config_patch") if isinstance(candidate.get("strategy_config_patch"), dict) else {},
        "validation_summary": validation.get("summary") if isinstance(validation.get("summary"), dict) else {},
        "validation_blockers": validation.get("blockers") if isinstance(validation.get("blockers"), list) else [],
    }


async def agent_strategy_lab_status(args: dict) -> list[types.TextContent]:
    svc = _agent_strategy_lab_service_module()
    ins = _normalize_instance(args if isinstance(args, dict) else {})
    return _text_json(svc.lab_status(instance=ins))


async def agent_strategy_lab_create_matrix_task(args: dict) -> list[types.TextContent]:
    svc = _agent_strategy_lab_service_module()
    payload = _agent_lab_payload(args if isinstance(args, dict) else {})
    ret = svc.create_lab_task(payload)
    return _text_json(ret if isinstance(ret, dict) else {"ok": False, "error": "unknown"})


async def agent_strategy_lab_get_task(args: dict) -> list[types.TextContent]:
    obj = args if isinstance(args, dict) else {}
    task_id = str(obj.get("task_id") or "").strip()
    if not task_id:
        return _text_json({"ok": False, "error": "task_id_required"})
    svc = _agent_strategy_lab_service_module()
    return _text_json(svc.get_lab_task(task_id))


async def agent_strategy_lab_get_best_candidates(args: dict) -> list[types.TextContent]:
    obj = args if isinstance(args, dict) else {}
    limit = max(1, min(20, _as_int(obj.get("limit"), 5)))
    passed_only = bool(obj.get("passed_only", False))
    run, source = _agent_lab_run_from_args(obj)
    if not isinstance(run, dict):
        return _text_json({"ok": False, **source})
    candidates = [x for x in (run.get("candidates") or []) if isinstance(x, dict)]
    if passed_only:
        candidates = [
            x for x in candidates
            if isinstance(x.get("validation"), dict) and bool(x["validation"].get("passed"))
        ]
    ranked = sorted(candidates, key=_candidate_score, reverse=True)
    return _text_json({
        "ok": True,
        **source,
        "run_id": run.get("run_id"),
        "instance": run.get("instance"),
        "strategy_variant": (run.get("request") or {}).get("strategy_variant") if isinstance(run.get("request"), dict) else None,
        "returned": min(len(ranked), limit),
        "items": [_candidate_summary(x) for x in ranked[:limit]],
        "ranking_note": "Score favors passed candidates, average return, win rate, closed trades, and lower drawdown/blockers.",
    })


async def agent_strategy_lab_preview_candidate_diff(args: dict) -> list[types.TextContent]:
    obj = args if isinstance(args, dict) else {}
    run_id = str(obj.get("run_id") or "").strip()
    candidate_id = str(obj.get("candidate_id") or "").strip()
    if not run_id or not candidate_id:
        return _text_json({"ok": False, "error": "run_id_and_candidate_id_required"})
    svc = _agent_strategy_lab_service_module()
    return _text_json(svc.preview_candidate_diff(run_id, candidate_id))


def get_journal_tools() -> list[types.Tool]:
    """返回交易日志相关的 MCP 工具列表"""
    return [
        types.Tool(
            name="save_trade_note",
            description="记录交易决策理由、市场环境、情绪标签",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol":          {"type": "string", "description": "股票代码"},
                    "action":          {"type": "string", "enum": ["buy", "sell"]},
                    "quantity":        {"type": "integer"},
                    "price":           {"type": "number"},
                    "decision_reason": {"type": "string", "description": "决策理由"},
                    "strategy_used":   {"type": "string", "description": "使用的策略"},
                    "emotion_tag":     {"type": "string", "enum": list(EmotionTag.__members__.values()), 
                                        "description": "情绪标签"},
                    "market_trend":    {"type": "string", "enum": ["上涨", "下跌", "震荡"]},
                    "market_sentiment": {"type": "number", "description": "Fear & Greed Index (0-100)"},
                },
                "required": ["symbol", "action", "quantity", "price"],
            },
        ),
        types.Tool(
            name="update_trade_exit",
            description="更新交易平仓信息（价格、盈亏、持有天数）",
            inputSchema={
                "type": "object",
                "properties": {
                    "trade_id":       {"type": "string", "description": "交易ID"},
                    "exit_price":     {"type": "number"},
                    "pnl":            {"type": "number", "description": "盈亏金额"},
                    "pnl_pct":        {"type": "number", "description": "盈亏百分比"},
                },
                "required": ["trade_id", "exit_price"],
            },
        ),
        types.Tool(
            name="add_trade_review",
            description="添加交易复盘（经验教训、错误类型、评分）",
            inputSchema={
                "type": "object",
                "properties": {
                    "trade_id":       {"type": "string"},
                    "lesson_learned": {"type": "string", "description": "经验教训"},
                    "mistake_type":   {"type": "string", "description": "错误类型"},
                    "rating":         {"type": "integer", "description": "评分 1-5星"},
                },
                "required": ["trade_id", "lesson_learned"],
            },
        ),
        types.Tool(
            name="get_trade_history",
            description="查询历史交易记录",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol":      {"type": "string"},
                    "start_date":  {"type": "string", "description": "YYYY-MM-DD"},
                    "end_date":    {"type": "string", "description": "YYYY-MM-DD"},
                    "action":      {"type": "string", "enum": ["buy", "sell"]},
                    "emotion_tag": {"type": "string"},
                    "has_review":  {"type": "boolean", "description": "是否有复盘"},
                    "limit":       {"type": "integer", "description": "返回数量，默认100"},
                },
                "required": [],
            },
        ),
        types.Tool(
            name="generate_review",
            description="自动生成周/月复盘报告",
            inputSchema={
                "type": "object",
                "properties": {
                    "period": {"type": "string", "enum": ["week", "month", "quarter"]},
                    "symbol": {"type": "string", "description": "指定股票，不填则全部"},
                },
                "required": ["period"],
            },
        ),
        types.Tool(
            name="analyze_decision_quality",
            description="分析决策质量，识别盈亏模式（情绪、市场环境、持仓时间）",
            inputSchema={
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "分析最近N天，默认30"},
                },
                "required": [],
            },
        ),
        types.Tool(
            name="get_trade_statistics",
            description="获取交易统计数据（胜率、盈亏比、情绪分布等）",
            inputSchema={
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "end_date":   {"type": "string", "description": "YYYY-MM-DD"},
                    "symbol":     {"type": "string"},
                },
                "required": [],
            },
        ),
        types.Tool(
            name="find_similar_trades",
            description="找出与当前情况类似的历史交易（策略、价格区间、市场环境）",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol":         {"type": "string"},
                    "strategy_used":  {"type": "string"},
                    "market_trend":   {"type": "string", "enum": ["上涨", "下跌", "震荡"]},
                    "limit":          {"type": "integer", "description": "返回数量，默认10"},
                },
                "required": ["symbol"],
            },
        ),
    ]


# ============================================================
# 智能告警 MCP 工具
# ============================================================

def get_alert_tools() -> list[types.Tool]:
    """返回告警系统相关的 MCP 工具列表"""
    return [
        types.Tool(
            name="set_price_alert",
            description="设置价格突破/跌破告警",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol":       {"type": "string"},
                    "target_price": {"type": "number"},
                    "direction":    {"type": "string", "enum": ["above", "below"],
                                     "description": "above=突破, below=跌破"},
                    "message":      {"type": "string", "description": "自定义消息"},
                    "expires_days": {"type": "integer", "description": "过期天数"},
                    "repeat":       {"type": "boolean", "description": "是否可重复触发"},
                },
                "required": ["symbol", "target_price", "direction"],
            },
        ),
        types.Tool(
            name="set_volume_alert",
            description="设置成交量异常告警（超过N倍日均）",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol":              {"type": "string"},
                    "threshold_multiplier": {"type": "number", 
                                            "description": "倍数，如1.5表示1.5倍日均"},
                    "message":             {"type": "string"},
                    "expires_days":        {"type": "integer"},
                },
                "required": ["symbol", "threshold_multiplier"],
            },
        ),
        types.Tool(
            name="list_alerts",
            description="查看所有告警",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol":     {"type": "string"},
                    "status":     {"type": "string", 
                                   "enum": list(AlertStatus.__members__.values())},
                    "alert_type": {"type": "string", 
                                   "enum": list(AlertType.__members__.values())},
                },
                "required": [],
            },
        ),
        types.Tool(
            name="delete_alert",
            description="删除告警",
            inputSchema={
                "type": "object",
                "properties": {
                    "alert_id": {"type": "string"},
                },
                "required": ["alert_id"],
            },
        ),
        types.Tool(
            name="check_triggered_alerts",
            description="立即检查所有活跃告警是否触发",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="get_alert_statistics",
            description="获取告警统计（总数、触发次数、监控的股票）",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="start_alert_monitor",
            description="启动后台告警监控（每N秒检查一次）",
            inputSchema={
                "type": "object",
                "properties": {
                    "interval": {"type": "integer", "description": "检查间隔秒数，默认5"},
                },
                "required": [],
            },
        ),
    ]


# ============================================================
# 交易日志工具实现
# ============================================================

async def save_trade_note(args: dict) -> list[types.TextContent]:
    """保存交易记录"""
    journal = get_journal()
    
    trade_id = f"trade_{args['symbol']}_{int(datetime.now().timestamp() * 1000)}"
    
    entry = TradeEntry(
        trade_id=trade_id,
        symbol=args["symbol"],
        action=args["action"],
        quantity=args["quantity"],
        price=args["price"],
        timestamp=datetime.now().isoformat(),
        decision_reason=args.get("decision_reason", ""),
        strategy_used=args.get("strategy_used", ""),
        emotion_tag=args.get("emotion_tag", EmotionTag.RATIONAL),
        market_trend=args.get("market_trend", ""),
        market_sentiment=args.get("market_sentiment", 50.0),
    )
    
    journal.save_trade(entry)
    
    return [types.TextContent(
        type="text",
        text=f"✅ 交易记录已保存\n"
             f"ID: {trade_id}\n"
             f"标的: {entry.symbol}\n"
             f"操作: {entry.action.upper()}\n"
             f"数量: {entry.quantity}\n"
             f"价格: ${entry.price:.2f}\n"
             f"决策理由: {entry.decision_reason or '未填写'}\n"
             f"情绪标签: {entry.emotion_tag}"
    )]


async def update_trade_exit(args: dict) -> list[types.TextContent]:
    """更新平仓信息"""
    journal = get_journal()
    
    trade = journal.get_trade(args["trade_id"])
    if not trade:
        return [types.TextContent(type="text", text=f"❌ 未找到交易ID: {args['trade_id']}")]
    
    exit_price = args["exit_price"]
    entry_price = trade.price
    
    # 计算盈亏
    if trade.action == "buy":
        pnl = (exit_price - entry_price) * trade.quantity
        pnl_pct = (exit_price - entry_price) / entry_price * 100
    else:  # sell
        pnl = (entry_price - exit_price) * trade.quantity
        pnl_pct = (entry_price - exit_price) / entry_price * 100
    
    # 计算持有天数
    entry_time = datetime.fromisoformat(trade.timestamp)
    exit_time = datetime.now()
    hold_days = (exit_time - entry_time).days
    
    journal.update_exit(
        trade_id=args["trade_id"],
        exit_price=exit_price,
        exit_timestamp=exit_time.isoformat(),
        pnl=round(pnl, 2),
        pnl_pct=round(pnl_pct, 2),
        hold_days=hold_days,
    )
    
    return [types.TextContent(
        type="text",
        text=f"✅ 平仓信息已更新\n"
             f"交易ID: {args['trade_id']}\n"
             f"开仓价: ${entry_price:.2f}\n"
             f"平仓价: ${exit_price:.2f}\n"
             f"盈亏: ${pnl:.2f} ({pnl_pct:+.2f}%)\n"
             f"持有: {hold_days}天"
    )]


async def add_trade_review(args: dict) -> list[types.TextContent]:
    """添加复盘"""
    journal = get_journal()
    
    success = journal.add_review(
        trade_id=args["trade_id"],
        lesson_learned=args["lesson_learned"],
        mistake_type=args.get("mistake_type", ""),
        rating=args.get("rating", 3),
    )
    
    if not success:
        return [types.TextContent(type="text", text=f"❌ 未找到交易ID: {args['trade_id']}")]
    
    return [types.TextContent(
        type="text",
        text=f"✅ 复盘已添加\n"
             f"交易ID: {args['trade_id']}\n"
             f"经验教训: {args['lesson_learned']}\n"
             f"评分: {'⭐' * args.get('rating', 3)}"
    )]


async def get_trade_history(args: dict) -> list[types.TextContent]:
    """查询交易历史"""
    journal = get_journal()
    
    trades = journal.query_trades(
        symbol=args.get("symbol"),
        start_date=args.get("start_date"),
        end_date=args.get("end_date"),
        action=args.get("action"),
        emotion_tag=args.get("emotion_tag"),
        has_review=args.get("has_review"),
        limit=args.get("limit", 100),
    )
    
    if not trades:
        return [types.TextContent(type="text", text="📭 没有找到符合条件的交易记录")]
    
    lines = [f"找到 {len(trades)} 笔交易：", ""]
    for t in trades[:20]:  # 最多显示20笔
        pnl_str = f"{t.pnl_pct:+.2f}%" if t.pnl_pct is not None else "持仓中"
        lines.append(
            f"• {t.timestamp[:10]} {t.symbol} {t.action.upper()} "
            f"{t.quantity}股 @${t.price:.2f} → {pnl_str}"
        )
        if t.decision_reason:
            lines.append(f"  理由: {t.decision_reason[:50]}")
    
    import json
    return [types.TextContent(type="text", text="\n".join(lines))]


async def generate_review(args: dict) -> list[types.TextContent]:
    """生成复盘报告"""
    journal = get_journal()
    
    report = generate_review_report(
        journal=journal,
        period=args["period"],
        symbol=args.get("symbol"),
    )
    
    import json
    return [types.TextContent(
        type="text",
        text=f"# {report['title']}\n\n"
             f"**时间范围**: {report['start_date'][:10]} ~ {report['end_date'][:10]}\n"
             f"**标的**: {report['symbol']}\n\n"
             f"## 📊 统计摘要\n"
             f"- 总交易: {report['statistics']['total_trades']}笔\n"
             f"- 胜率: {report['statistics']['win_rate']:.1f}%\n"
             f"- 盈亏比: {report['statistics']['profit_factor']:.2f}\n"
             f"- 平均盈利: {report['statistics']['avg_win_pct']:.2f}%\n"
             f"- 平均亏损: {report['statistics']['avg_loss_pct']:.2f}%\n\n"
             f"## 💡 改进建议\n"
             + "\n".join(f"- {r}" for r in report['recommendations']) + "\n\n"
             f"详细数据：\n```json\n{json.dumps(report, indent=2, ensure_ascii=False)}\n```"
    )]


async def analyze_decision_quality(args: dict) -> list[types.TextContent]:
    """分析决策质量"""
    journal = get_journal()
    
    analysis = _analyze_decision_quality_core(journal, days=args.get("days", 30))
    
    import json
    return [types.TextContent(
        type="text",
        text=f"# 决策质量分析（近{analysis['period_days']}天）\n\n"
             f"分析了 {analysis['total_analyzed']} 笔交易\n\n"
             f"## 情绪分析\n"
             + (f"⚠️ 最差情绪: {analysis['emotion_analysis']['worst_emotion']} "
                f"（亏损 ${abs(analysis['emotion_analysis']['worst_emotion_loss']):.0f}）\n\n"
                if analysis['emotion_analysis']['worst_emotion'] else "") +
             f"## 💡 洞察建议\n"
             + "\n".join(f"- {i}" for i in analysis['insights']) + "\n\n"
             f"完整数据：\n```json\n{json.dumps(analysis, indent=2, ensure_ascii=False)}\n```"
    )]


async def get_trade_statistics(args: dict) -> list[types.TextContent]:
    """获取统计数据"""
    journal = get_journal()
    
    stats = journal.get_statistics(
        start_date=args.get("start_date"),
        end_date=args.get("end_date"),
        symbol=args.get("symbol"),
    )
    
    import json
    return [types.TextContent(type="text", text=json.dumps(stats, indent=2, ensure_ascii=False))]


async def find_similar_trades(args: dict) -> list[types.TextContent]:
    """查找相似交易"""
    journal = get_journal()
    
    # 简化实现：按策略和市场环境过滤
    trades = journal.query_trades(
        symbol=args["symbol"],
        limit=args.get("limit", 10),
    )
    
    # 过滤
    if "strategy_used" in args:
        trades = [t for t in trades if t.strategy_used == args["strategy_used"]]
    if "market_trend" in args:
        trades = [t for t in trades if t.market_trend == args["market_trend"]]
    
    if not trades:
        return [types.TextContent(type="text", text="📭 没有找到相似的历史交易")]
    
    lines = [f"找到 {len(trades)} 笔相似交易：", ""]
    for t in trades:
        pnl_str = f"{t.pnl_pct:+.2f}%" if t.pnl_pct is not None else "持仓中"
        lines.append(
            f"• {t.timestamp[:10]} {t.action.upper()} {t.quantity}股 @${t.price:.2f} → {pnl_str}\n"
            f"  策略: {t.strategy_used}, 市场: {t.market_trend}, 情绪: {t.emotion_tag}"
        )
    
    return [types.TextContent(type="text", text="\n".join(lines))]


# ============================================================
# 智能告警工具实现
# ============================================================

async def set_price_alert(args: dict) -> list[types.TextContent]:
    """设置价格告警"""
    manager = get_alert_manager()
    
    alert_id = manager.create_price_alert(
        symbol=args["symbol"],
        target_price=args["target_price"],
        direction=args["direction"],
        message=args.get("message", ""),
        expires_in_days=args.get("expires_days"),
        repeat=args.get("repeat", False),
    )
    
    return [types.TextContent(
        type="text",
        text=f"✅ 价格告警已创建\n"
             f"ID: {alert_id}\n"
             f"标的: {args['symbol']}\n"
             f"目标价: ${args['target_price']:.2f}\n"
             f"方向: {'突破' if args['direction'] == 'above' else '跌破'}\n"
             f"可重复: {'是' if args.get('repeat') else '否'}"
    )]


async def set_volume_alert(args: dict) -> list[types.TextContent]:
    """设置成交量告警"""
    manager = get_alert_manager()
    
    alert_id = manager.create_volume_alert(
        symbol=args["symbol"],
        threshold_multiplier=args["threshold_multiplier"],
        message=args.get("message", ""),
        expires_in_days=args.get("expires_days"),
    )
    
    return [types.TextContent(
        type="text",
        text=f"✅ 成交量告警已创建\n"
             f"ID: {alert_id}\n"
             f"标的: {args['symbol']}\n"
             f"阈值: {args['threshold_multiplier']}倍日均成交量"
    )]


async def list_alerts(args: dict) -> list[types.TextContent]:
    """列出告警"""
    manager = get_alert_manager()
    
    alerts = manager.list_alerts(
        symbol=args.get("symbol"),
        status=args.get("status"),
        alert_type=args.get("alert_type"),
    )
    
    if not alerts:
        return [types.TextContent(type="text", text="📭 没有告警")]
    
    lines = [f"共 {len(alerts)} 个告警：", ""]
    for a in alerts[:20]:
        lines.append(
            f"• {a.alert_id[:16]}... | {a.symbol} | {a.alert_type} | {a.status}\n"
            f"  创建: {a.created_at[:10]} | 触发: {a.triggered_count}次"
        )
        if a.target_price:
            lines.append(f"  目标价: ${a.target_price:.2f}")
    
    return [types.TextContent(type="text", text="\n".join(lines))]


async def delete_alert(args: dict) -> list[types.TextContent]:
    """删除告警"""
    manager = get_alert_manager()
    
    success = manager.delete_alert(args["alert_id"])
    
    if not success:
        return [types.TextContent(type="text", text=f"❌ 未找到告警ID: {args['alert_id']}")]
    
    return [types.TextContent(type="text", text=f"✅ 告警已删除: {args['alert_id']}")]


async def check_triggered_alerts(args: dict) -> list[types.TextContent]:
    """检查告警"""
    manager = get_alert_manager()
    manager.check_alerts()
    
    stats = manager.get_statistics()
    
    return [types.TextContent(
        type="text",
        text=f"✅ 告警检查完成\n"
             f"活跃告警: {stats['status_distribution'].get(AlertStatus.ACTIVE, 0)}\n"
             f"已触发: {stats['status_distribution'].get(AlertStatus.TRIGGERED, 0)}\n"
             f"总触发次数: {stats['total_triggers']}"
    )]


async def get_alert_statistics(args: dict) -> list[types.TextContent]:
    """获取告警统计"""
    manager = get_alert_manager()
    stats = manager.get_statistics()
    
    import json
    return [types.TextContent(type="text", text=json.dumps(stats, indent=2, ensure_ascii=False))]


async def start_alert_monitor(args: dict) -> list[types.TextContent]:
    """启动后台监控"""
    manager = get_alert_manager()
    interval = args.get("interval", 5)
    
    manager.start_monitoring(interval=interval)
    
    return [types.TextContent(
        type="text",
        text=f"✅ 告警监控已启动（每 {interval} 秒检查一次）"
    )]


# ============================================================
# 工具分发映射
# ============================================================

TOOL_DISPATCH = {
    # 交易日志
    "save_trade_note":           save_trade_note,
    "update_trade_exit":         update_trade_exit,
    "add_trade_review":          add_trade_review,
    "get_trade_history":         get_trade_history,
    "generate_review":           generate_review,
    "analyze_decision_quality":  analyze_decision_quality,
    "get_trade_statistics":      get_trade_statistics,
    "find_similar_trades":       find_similar_trades,
    
    # 智能告警
    "set_price_alert":           set_price_alert,
    "set_volume_alert":          set_volume_alert,
    "list_alerts":               list_alerts,
    "delete_alert":              delete_alert,
    "check_triggered_alerts":    check_triggered_alerts,
    "get_alert_statistics":      get_alert_statistics,
    "start_alert_monitor":       start_alert_monitor,
    # QQQ live worker
    "qqq_live_get_config":       qqq_live_get_config,
    "qqq_live_update_config":    qqq_live_update_config,
    "qqq_live_get_decision_tail": qqq_live_get_decision_tail,
    "qqq_live_get_recommendation": qqq_live_get_recommendation,
    "qqq_live_start_worker":     qqq_live_start_worker,
    "qqq_live_stop_worker":      qqq_live_stop_worker,
    "qqq_live_services_status":  qqq_live_services_status,
    # Agent Strategy Lab
    "agent_strategy_lab_status": agent_strategy_lab_status,
    "agent_strategy_lab_create_matrix_task": agent_strategy_lab_create_matrix_task,
    "agent_strategy_lab_get_task": agent_strategy_lab_get_task,
    "agent_strategy_lab_get_best_candidates": agent_strategy_lab_get_best_candidates,
    "agent_strategy_lab_preview_candidate_diff": agent_strategy_lab_preview_candidate_diff,
    **MARKET_TOOL_DISPATCH,
    **NOTIFICATION_TOOL_DISPATCH,
}
