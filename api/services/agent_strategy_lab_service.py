from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Callable
from uuid import uuid4

LAB_SCHEMA_VERSION = "agent_strategy_lab.v1"
VALID_INSTANCES = {"0dte": "qqq_0dte", "1dte": "qqq_1dte", "stock_options_swing": "stock_options_swing"}
VALID_LAB_STRATEGY_VARIANTS = {"morning_strangle", "morning_double_strangle", "morning_directional"}
VALID_SWING_STRATEGY_VARIANTS = {
    "swing_trend_call",
    "swing_pullback_call",
    "swing_breakout_call",
    "swing_event_filtered_call",
}
VALID_CANDIDATE_GENERATORS = {"deterministic", "tradingagents"}
VALID_RESEARCH_DIMENSIONS = {"risk_controls", "time_window", "combined", "leg_gap"}
DEFAULT_VALIDATION_WINDOWS_DAYS = [60, 120, 180]

BacktestRunner = Callable[[dict[str, Any]], dict[str, Any]]
_TASK_LOCK = threading.RLock()
_TASKS: dict[str, dict[str, Any]] = {}
_TASK_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent-strategy-lab")
_TASK_FUTURES: dict[str, Future[Any]] = {}


class AgentStrategyLabError(ValueError):
    pass


def default_root() -> Path:
    return Path(os.getenv("MULTITRADING_ROOT") or Path(__file__).resolve().parents[2]).resolve()


def _ensure_repo_import_paths(root: Path | None = None) -> None:
    base = root or default_root()
    mcp_dir = base / "mcp_server"
    for path in (str(base), str(mcp_dir)):
        if path not in sys.path:
            sys.path.insert(0, path)


def normalize_instance(instance: str | None) -> str:
    raw = str(instance or "0dte").strip().lower()
    if raw in {"qqq_0dte", "options-0dte", "0date"}:
        raw = "0dte"
    if raw in {"qqq_1dte", "options-1dte", "1date"}:
        raw = "1dte"
    if raw in {"swing", "options-swing", "stock-options-swing", "stock_options_long", "stock-options-long"}:
        raw = "stock_options_swing"
    if raw not in VALID_INSTANCES:
        raise AgentStrategyLabError("unsupported_instance")
    return raw


def normalize_lab_strategy_variant(value: Any, fallback: str = "morning_strangle") -> str:
    raw = str(value or "").strip().lower()
    if raw in VALID_LAB_STRATEGY_VARIANTS:
        return raw
    fb = str(fallback or "").strip().lower()
    return fb if fb in VALID_LAB_STRATEGY_VARIANTS else "morning_strangle"


def normalize_swing_strategy_variant(value: Any, fallback: str = "swing_trend_call") -> str:
    raw = str(value or "").strip().lower()
    aliases = {
        "long_call": "swing_trend_call",
        "trend_call": "swing_trend_call",
        "trend": "swing_trend_call",
        "pullback": "swing_pullback_call",
        "pullback_call": "swing_pullback_call",
        "breakout": "swing_breakout_call",
        "breakout_call": "swing_breakout_call",
        "event_filtered": "swing_event_filtered_call",
        "event_filtered_call": "swing_event_filtered_call",
    }
    raw = aliases.get(raw, raw)
    if raw in VALID_SWING_STRATEGY_VARIANTS:
        return raw
    fb = aliases.get(str(fallback or "").strip().lower(), str(fallback or "").strip().lower())
    return fb if fb in VALID_SWING_STRATEGY_VARIANTS else "swing_trend_call"


def normalize_strategy_variant_for_instance(instance: str | None, value: Any, fallback: str | None = None) -> str:
    inst = normalize_instance(instance)
    if inst == "stock_options_swing":
        return normalize_swing_strategy_variant(value, fallback or "swing_trend_call")
    return normalize_lab_strategy_variant(value, fallback or "morning_strangle")


def normalize_candidate_generator(value: Any, fallback: str = "deterministic") -> str:
    raw = str(value or "").strip().lower()
    if raw in {"deterministic_mvp", "rule", "rules"}:
        raw = "deterministic"
    if raw in {"tradingagents_adapter", "trading_agents", "trading-agent"}:
        raw = "tradingagents"
    if raw in VALID_CANDIDATE_GENERATORS:
        return raw
    fb = str(fallback or "").strip().lower()
    return fb if fb in VALID_CANDIDATE_GENERATORS else "deterministic"


def normalize_research_dimension(value: Any, fallback: str = "risk_controls") -> str:
    raw = str(value or "").strip().lower()
    aliases = {
        "risk": "risk_controls",
        "risk_control": "risk_controls",
        "risk_controls": "risk_controls",
        "risk-first": "risk_controls",
        "risk_first": "risk_controls",
        "time": "time_window",
        "time_window": "time_window",
        "time-window": "time_window",
        "timing": "time_window",
        "combined": "combined",
        "mix": "combined",
        "mixed": "combined",
        "all": "combined",
        "gap": "leg_gap",
        "leg_gap": "leg_gap",
        "leg-gap": "leg_gap",
        "distance": "leg_gap",
        "leg_distance": "leg_gap",
        "double_strangle_gap": "leg_gap",
    }
    raw = aliases.get(raw, raw)
    if raw in VALID_RESEARCH_DIMENSIONS:
        return raw
    fb = aliases.get(str(fallback or "").strip().lower(), str(fallback or "").strip().lower())
    return fb if fb in VALID_RESEARCH_DIMENSIONS else "risk_controls"


def _data_dir(root: Path, instance: str) -> Path:
    return root / "data" / VALID_INSTANCES[normalize_instance(instance)]


def _lab_dir(root: Path) -> Path:
    return root / "data" / "agent_strategy_lab"


def _runs_path(root: Path) -> Path:
    return _lab_dir(root) / "runs.json"


def _approvals_path(root: Path) -> Path:
    return _lab_dir(root) / "approvals.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _task_view(task: dict[str, Any]) -> dict[str, Any]:
    out = dict(task)
    if "request" in out and isinstance(out["request"], dict):
        out["request"] = dict(out["request"])
    if "events" in out and isinstance(out["events"], list):
        out["events"] = list(out["events"])[-50:]
    return out


def _set_task_progress(task_id: str, *, pct: int, stage: str, text: str) -> None:
    with _TASK_LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return
        now = _now_iso()
        task["progress_pct"] = max(0, min(100, int(pct)))
        task["progress_stage"] = stage
        task["progress_text"] = text
        task["updated_at"] = now
        events = task.setdefault("events", [])
        if isinstance(events, list):
            events.append({"ts": now, "stage": stage, "pct": task["progress_pct"], "text": text})
            del events[:-50]


def _parse_dt(value: Any) -> datetime | None:
    s = str(value or "").strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _read_json(path: Path, fallback: Any = None) -> Any:
    try:
        if not path.is_file():
            return fallback
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _load_approvals(root: Path) -> list[dict[str, Any]]:
    data = _read_json(_approvals_path(root), {}) or {}
    rows = data.get("approvals", []) if isinstance(data, dict) else []
    if not isinstance(rows, list):
        return []
    return [x for x in rows if isinstance(x, dict)]


def _save_approvals(root: Path, approvals: list[dict[str, Any]]) -> None:
    rows = sorted(approvals, key=lambda x: str(x.get("approved_at") or x.get("created_at") or ""))[-200:]
    _write_json_atomic(_approvals_path(root), {"schema": f"{LAB_SCHEMA_VERSION}.approvals", "approvals": rows})


def _flatten_dict(payload: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in payload.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            out.update(_flatten_dict(value, path))
        else:
            out[path] = value
    return out


def build_config_diff(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    left = _flatten_dict(before if isinstance(before, dict) else {})
    right = _flatten_dict(after if isinstance(after, dict) else {})
    rows: list[dict[str, Any]] = []
    for key in sorted(set(left) | set(right)):
        old = left.get(key)
        new = right.get(key)
        if old != new:
            rows.append({"field": key, "before": old, "after": new})
    return rows


def build_patch_diff(before: dict[str, Any], patch: dict[str, Any]) -> list[dict[str, Any]]:
    current = before if isinstance(before, dict) else {}
    patch_flat = _flatten_dict(patch if isinstance(patch, dict) else {})
    rows: list[dict[str, Any]] = []
    for key in sorted(patch_flat):
        old: Any = current
        for part in key.split("."):
            if isinstance(old, dict) and part in old:
                old = old.get(part)
            else:
                old = None
                break
        new = patch_flat[key]
        if old != new:
            rows.append({"field": key, "before": old, "after": new})
    return rows


def _value_at_path(mapping: dict[str, Any], path: str) -> Any:
    cur: Any = mapping
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur.get(part)
        else:
            return None
    return cur


def _set_path(mapping: dict[str, Any], path: str, value: Any) -> None:
    cur = mapping
    parts = path.split(".")
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def compact_strategy_patch(before: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    current = before if isinstance(before, dict) else {}
    patch_flat = _flatten_dict(patch if isinstance(patch, dict) else {})
    out: dict[str, Any] = {}
    for key, new in patch_flat.items():
        if _value_at_path(current, key) != new:
            _set_path(out, key, new)
    return out


def _read_jsonl_tail(path: Path, limit: int = 100) -> list[dict[str, Any]]:
    lim = max(1, min(1000, int(limit)))
    if not path.is_file():
        return []
    try:
        chunk = 1024 * 1024
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size <= chunk:
                f.seek(0)
                raw = f.read().decode("utf-8", errors="ignore")
            else:
                f.seek(max(0, size - chunk))
                raw = f.read().decode("utf-8", errors="ignore")
                first_nl = raw.find("\n")
                if first_nl >= 0:
                    raw = raw[first_nl + 1 :]
        rows: list[dict[str, Any]] = []
        for line in [x.strip() for x in raw.splitlines() if x.strip()][-lim:]:
            try:
                item = json.loads(line)
                if isinstance(item, dict):
                    rows.append(item)
            except Exception:
                continue
        return rows
    except Exception:
        return []


def _severity_rank(severity: str) -> int:
    return {"ok": 0, "info": 1, "warn": 2, "error": 3}.get(str(severity or "").lower(), 1)


def _check(check_id: str, severity: str, title: str, detail: str, value: Any = None) -> dict[str, Any]:
    out = {"id": check_id, "severity": severity, "title": title, "detail": detail}
    if value is not None:
        out["value"] = value
    return out


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        n = float(value)
        if math.isfinite(n):
            return n
    except Exception:
        pass
    return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _hhmm_minutes(value: Any, fallback: str) -> int:
    s = str(value or fallback).strip()
    try:
        h, m = s.split(":", 1)
        return max(0, min(23, int(h))) * 60 + max(0, min(59, int(m)))
    except Exception:
        h, m = fallback.split(":", 1)
        return int(h) * 60 + int(m)


def _fmt_hhmm(minutes: int) -> str:
    m = max(0, min(23 * 60 + 59, int(minutes)))
    return f"{m // 60:02d}:{m % 60:02d}"


def _known_strategy_config(config: dict[str, Any]) -> dict[str, Any]:
    _ensure_repo_import_paths()
    from mcp_server.strategy_qqq_0dte.config import Qqq0dteConfig

    return Qqq0dteConfig.from_dict(config if isinstance(config, dict) else {}).to_dict()


def build_data_quality_report(root: Path | None = None, instance: str = "0dte", tail_limit: int = 200) -> dict[str, Any]:
    root = root or default_root()
    inst = normalize_instance(instance)
    subdir = _data_dir(root, inst)
    cfg_path = subdir / "live_worker_config.json"
    decision_path = subdir / "live_worker_decision_tail.jsonl"
    ledger_path = subdir / "live_worker_execution_ledger.jsonl"
    recommendation_path = subdir / "strategy_recommendation.json"

    cfg = _read_json(cfg_path, {}) or {}
    strategy_config = cfg.get("strategy_config") if isinstance(cfg.get("strategy_config"), dict) else {}
    if inst == "stock_options_swing":
        strategy_config = cfg.get("strategy") if isinstance(cfg.get("strategy"), dict) else {}
    decisions = _read_jsonl_tail(decision_path, tail_limit)
    ledger = _read_jsonl_tail(ledger_path, tail_limit)
    recommendation = _read_json(recommendation_path, {}) or {}
    checks: list[dict[str, Any]] = []

    checks.append(
        _check(
            "live_config_present",
            "ok" if cfg_path.is_file() and isinstance(cfg, dict) else "error",
            "实盘配置文件",
            str(cfg_path),
        )
    )
    checks.append(
        _check(
            "strategy_config_present",
            "ok" if isinstance(strategy_config, dict) and bool(strategy_config) else "error",
            "strategy" if inst == "stock_options_swing" else "strategy_config",
            "已读取策略配置"
            if strategy_config
            else ("live_worker_config.json 里缺少 strategy" if inst == "stock_options_swing" else "live_worker_config.json 里缺少 strategy_config"),
        )
    )
    checks.append(
        _check(
            "decision_log_present",
            "ok" if decisions else "warn",
            "实盘决策日志",
            f"读取到最近 {len(decisions)} 条" if decisions else f"未读取到 {decision_path}",
            len(decisions),
        )
    )
    checks.append(
        _check(
            "execution_ledger_present",
            "ok" if ledger else "info",
            "执行 ledger",
            f"读取到最近 {len(ledger)} 条" if ledger else f"未读取到 {ledger_path}",
            len(ledger),
        )
    )

    latest_decision = decisions[-1] if decisions else {}
    latest_decision_at = _parse_dt(latest_decision.get("at"))
    latest_age_minutes: float | None = None
    if latest_decision_at:
        latest_age_minutes = round((datetime.now(timezone.utc) - latest_decision_at).total_seconds() / 60.0, 2)
        sev = "ok" if latest_age_minutes <= 15 else "warn" if latest_age_minutes <= 180 else "info"
        checks.append(
            _check(
                "latest_decision_age",
                sev,
                "最新决策日志时间",
                f"{latest_age_minutes} 分钟前",
                latest_age_minutes,
            )
        )
    elif decisions:
        checks.append(_check("latest_decision_age", "warn", "最新决策日志时间", "无法解析最新 at 字段"))

    action_counts: dict[str, int] = {}
    bar_times: list[datetime] = []
    quote_timestamps: list[datetime] = []
    owner_missing = 0
    account_missing = 0
    for row in decisions:
        action = row.get("action") if isinstance(row.get("action"), dict) else {}
        action_name = str(action.get("action") or row.get("message") or "").strip() or "unknown"
        action_counts[action_name] = action_counts.get(action_name, 0) + 1
        bt = _parse_dt(row.get("bar_utc") or action.get("bar_utc"))
        if bt:
            bar_times.append(bt)
        if not row.get("owner_id"):
            owner_missing += 1
        if not row.get("account_id"):
            account_missing += 1
        detail = action.get("detail") if isinstance(action.get("detail"), dict) else {}
        resolved = detail.get("resolved") if isinstance(detail.get("resolved"), list) else []
        for item in resolved:
            if not isinstance(item, dict):
                continue
            resolve = item.get("resolve") if isinstance(item.get("resolve"), dict) else {}
            quote = resolve.get("quote") if isinstance(resolve.get("quote"), dict) else {}
            qdt = _parse_dt(quote.get("timestamp"))
            if qdt:
                quote_timestamps.append(qdt)

    if decisions:
        noop_count = sum(v for k, v in action_counts.items() if k == "noop_no_new_bars")
        noop_ratio = round(noop_count / max(1, len(decisions)), 3)
        checks.append(
            _check(
                "noop_no_new_bars_ratio",
                "warn" if noop_ratio >= 0.6 else "ok",
                "等待新 K 线比例",
                f"最近决策中 noop_no_new_bars 占比 {noop_ratio:.1%}",
                noop_ratio,
            )
        )
        if owner_missing or account_missing:
            checks.append(
                _check(
                    "context_fields",
                    "warn",
                    "owner/account 上下文",
                    f"最近日志中 owner 缺失 {owner_missing} 条，account 缺失 {account_missing} 条",
                )
            )
        else:
            checks.append(_check("context_fields", "ok", "owner/account 上下文", "最近日志均带 owner/account"))

    large_bar_gaps = 0
    if len(bar_times) >= 2:
        uniq = sorted(set(bar_times))
        for prev, cur in zip(uniq, uniq[1:]):
            gap = (cur - prev).total_seconds()
            if gap > 180:
                large_bar_gaps += 1
        checks.append(
            _check(
                "bar_gap_scan",
                "warn" if large_bar_gaps else "ok",
                "K 线连续性",
                f"最近尾部发现 {large_bar_gaps} 个超过 3 分钟的 bar_utc 间隔",
                large_bar_gaps,
            )
        )

    if quote_timestamps:
        latest_quote = max(quote_timestamps)
        quote_age = round((datetime.now(timezone.utc) - latest_quote).total_seconds() / 60.0, 2)
        checks.append(
            _check(
                "quote_timestamp",
                "ok" if quote_age <= 15 else "warn",
                "期权报价时间",
                f"最近解析到的期权 quote timestamp 距今 {quote_age} 分钟",
                quote_age,
            )
        )
    else:
        checks.append(
            _check(
                "quote_timestamp",
                "info",
                "期权报价时间",
                "最近决策尾部没有可解析的 resolved quote timestamp；Lab 不据此放宽风控",
            )
        )

    buy_count = sum(1 for x in ledger if str(x.get("side") or "").lower() == "buy")
    sell_count = sum(1 for x in ledger if str(x.get("side") or "").lower() == "sell")
    checks.append(
        _check(
            "ledger_side_balance",
            "ok" if buy_count or sell_count else "info",
            "ledger 买卖记录",
            f"最近 ledger buy={buy_count}, sell={sell_count}",
            {"buy": buy_count, "sell": sell_count},
        )
    )

    rec_features = recommendation.get("features") if isinstance(recommendation.get("features"), dict) else {}
    if rec_features:
        checks.append(
            _check(
                "market_features_present",
                "ok",
                "推荐层行情特征",
                "已读取 strategy_recommendation.json 的行情快照",
            )
        )
    else:
        checks.append(
            _check(
                "market_features_present",
                "info",
                "推荐层行情特征",
                "未读取到 strategy_recommendation.json，候选生成只基于配置与日志",
            )
        )

    worst = max(checks, key=lambda x: _severity_rank(str(x.get("severity") or "info"))) if checks else {}
    summary = {
        "status": str(worst.get("severity") or "info"),
        "checks_total": len(checks),
        "warnings": sum(1 for x in checks if str(x.get("severity")) == "warn"),
        "errors": sum(1 for x in checks if str(x.get("severity")) == "error"),
        "latest_decision_at": latest_decision.get("at") if latest_decision else None,
        "latest_decision_age_minutes": latest_age_minutes,
        "action_counts": action_counts,
    }
    return {
        "schema": f"{LAB_SCHEMA_VERSION}.data_quality",
        "ok": summary["errors"] == 0,
        "instance": inst,
        "generated_at": _now_iso(),
        "paths": {
            "config": str(cfg_path),
            "decision_tail": str(decision_path),
            "execution_ledger": str(ledger_path),
            "strategy_recommendation": str(recommendation_path),
        },
        "summary": summary,
        "checks": checks,
        "current_config": cfg,
        "strategy_recommendation": recommendation,
    }


def _strategy_base_from_live_config(live_config: dict[str, Any], strategy_variant: str = "morning_strangle") -> dict[str, Any]:
    sc = live_config.get("strategy_config") if isinstance(live_config.get("strategy_config"), dict) else {}
    base = _known_strategy_config(sc)
    base["strategy_variant"] = normalize_lab_strategy_variant(strategy_variant, str(base.get("strategy_variant") or "morning_strangle"))
    base["max_trades_per_day"] = max(1, _safe_int(base.get("max_trades_per_day"), 1))
    base["initial_option_contracts"] = max(1, _safe_int(base.get("initial_option_contracts"), 1))
    base["strangle_long_leg_take_profit_pct"] = max(0.0, _safe_float(base.get("strangle_long_leg_take_profit_pct"), 0.0))
    base["strangle_short_leg_take_profit_pct"] = max(0.0, _safe_float(base.get("strangle_short_leg_take_profit_pct"), 0.0))
    if base["strategy_variant"] == "morning_double_strangle":
        base["double_strangle_call_long_strikes_otm"] = max(1, _safe_int(base.get("double_strangle_call_long_strikes_otm"), 2))
        base["double_strangle_call_short_strikes_otm"] = max(0, _safe_int(base.get("double_strangle_call_short_strikes_otm"), 1))
        base["double_strangle_put_long_strikes_otm"] = max(1, _safe_int(base.get("double_strangle_put_long_strikes_otm"), 2))
        base["double_strangle_put_short_strikes_otm"] = max(0, _safe_int(base.get("double_strangle_put_short_strikes_otm"), 1))
        if base["double_strangle_call_long_strikes_otm"] <= base["double_strangle_call_short_strikes_otm"]:
            base["double_strangle_call_long_strikes_otm"] = base["double_strangle_call_short_strikes_otm"] + 1
        if base["double_strangle_put_long_strikes_otm"] <= base["double_strangle_put_short_strikes_otm"]:
            base["double_strangle_put_long_strikes_otm"] = base["double_strangle_put_short_strikes_otm"] + 1
    return base


def _swing_strategy_base_from_live_config(live_config: dict[str, Any]) -> dict[str, Any]:
    sc = live_config.get("strategy") if isinstance(live_config.get("strategy"), dict) else {}
    risk = live_config.get("risk") if isinstance(live_config.get("risk"), dict) else {}
    return {
        "strategy_variant": normalize_swing_strategy_variant(sc.get("strategy_variant")),
        "mode": str(sc.get("mode") or "long_call"),
        "trend_fast_ma": _safe_int(sc.get("trend_fast_ma"), 20),
        "trend_slow_ma": _safe_int(sc.get("trend_slow_ma"), 50),
        "long_ma": _safe_int(sc.get("long_ma"), 200),
        "min_trend_score": _safe_int(sc.get("min_trend_score"), 3),
        "min_price_above_slow_ma_pct": _safe_float(sc.get("min_price_above_slow_ma_pct"), 0.0),
        "max_price_above_fast_ma_pct": _safe_float(sc.get("max_price_above_fast_ma_pct"), 0.12),
        "min_dte": _safe_int(sc.get("min_dte"), 45),
        "target_dte": _safe_int(sc.get("target_dte"), 90),
        "max_dte": _safe_int(sc.get("max_dte"), 180),
        "target_delta_min": _safe_float(sc.get("target_delta_min"), 0.35),
        "target_delta_max": _safe_float(sc.get("target_delta_max"), 0.7),
        "fallback_otm_pct": _safe_float(sc.get("fallback_otm_pct"), 0.03),
        "spread_width_pct": _safe_float(sc.get("spread_width_pct"), 0.05),
        "max_spread_debit": _safe_float(sc.get("max_spread_debit"), 600.0),
        "max_spread_debit_to_width_pct": _safe_float(sc.get("max_spread_debit_to_width_pct"), 0.45),
        "spread_min_hold_days_before_stop": _safe_int(sc.get("spread_min_hold_days_before_stop"), 5),
        "min_open_interest": _safe_int(sc.get("min_open_interest"), 50),
        "min_option_volume": _safe_int(sc.get("min_option_volume"), 1),
        "max_bid_ask_spread_pct": _safe_float(sc.get("max_bid_ask_spread_pct"), 0.18),
        "take_profit_pct": _safe_float(sc.get("take_profit_pct"), 0.8),
        "stop_loss_pct": _safe_float(sc.get("stop_loss_pct"), 0.45),
        "dte_exit_days": _safe_int(sc.get("dte_exit_days"), 21),
        "trend_exit_below_ma": _safe_int(sc.get("trend_exit_below_ma"), 50),
        "trend_exit_confirm_bars": _safe_int(sc.get("trend_exit_confirm_bars"), 2),
        "max_contracts_per_order": _safe_int(risk.get("max_contracts_per_order"), 1),
        "max_open_contracts": _safe_int(risk.get("max_open_contracts"), 10),
        "max_premium_per_order": _safe_float(risk.get("max_premium_per_order"), 800.0),
        "max_premium_per_symbol": _safe_float(risk.get("max_premium_per_symbol"), 1500.0),
        "max_total_option_premium": _safe_float(risk.get("max_total_option_premium"), 4000.0),
        "max_new_premium_per_day": _safe_float(risk.get("max_new_premium_per_day"), 1500.0),
    }


def _normalize_symbol(symbol: Any) -> str:
    s = str(symbol or "").strip().upper()
    if not s:
        return ""
    return s if "." in s else f"{s}.US"


def _normalize_stock_pool(live_config: dict[str, Any]) -> list[str]:
    values: list[str] = []
    pool = live_config.get("stock_pool")
    if isinstance(pool, list):
        values.extend(str(x) for x in pool)
    elif isinstance(pool, str):
        values.extend(pool.replace(";", ",").replace("\n", ",").split(","))
    primary = _normalize_symbol(live_config.get("symbol") or "QQQ.US")
    if primary:
        values.insert(0, primary)
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        sym = _normalize_symbol(item)
        if sym and sym not in seen:
            seen.add(sym)
            out.append(sym)
    return out or ["QQQ.US"]


def generate_stock_options_swing_candidates(
    data_quality: dict[str, Any],
    *,
    max_candidates: int = 3,
    research_dimension: str = "risk_controls",
    candidate_generator: str = "deterministic",
    strategy_variant: str = "swing_trend_call",
) -> list[dict[str, Any]]:
    live_config = data_quality.get("current_config") if isinstance(data_quality.get("current_config"), dict) else {}
    base = _swing_strategy_base_from_live_config(live_config)
    variant = normalize_swing_strategy_variant(strategy_variant, str(base.get("strategy_variant") or "swing_trend_call"))
    dimension = normalize_research_dimension(research_dimension)
    generator = normalize_candidate_generator(candidate_generator)
    candidates: list[dict[str, Any]] = []

    def _candidate(cid: str, title: str, strategy_patch: dict[str, Any], risk_patch: dict[str, Any], reasoning: list[str]) -> None:
        strategy_patch = {"strategy_variant": variant, **strategy_patch}
        patch = {"strategy": strategy_patch, "risk": risk_patch}
        candidates.append(
            {
                "candidate_id": cid,
                "title": title,
                "generator": generator,
                "generator_mode": "stock_options_swing_research_mvp",
                "agent_action": "normal_size" if "guard" not in cid else "reduce_size",
                "confidence": 0.55 if generator == "tradingagents" else 0.5,
                "reasoning": reasoning,
                "strategy_config": patch,
                "strategy_config_patch": patch,
                "research_dimension": dimension,
                "swing_strategy_variant": variant,
                "safety_note": "股票期权中长线 Lab 会做股票日线 + 理论期权路径的粗略验证；这不是真实期权历史成交回测，也不会写配置或下单。",
            }
        )

    base_risk = {
        "max_contracts_per_order": max(1, base["max_contracts_per_order"]),
        "max_open_contracts": max(3, base["max_open_contracts"]),
        "max_premium_per_order": max(300.0, base["max_premium_per_order"]),
        "max_premium_per_symbol": max(500.0, base["max_premium_per_symbol"]),
        "max_total_option_premium": max(1000.0, base["max_total_option_premium"]),
        "max_new_premium_per_day": max(500.0, base["max_new_premium_per_day"]),
    }
    if variant == "swing_trend_call":
        _candidate(
            "swing_trend_call_baseline",
            "趋势买入 Call 基线",
            {
                "mode": "long_call",
                "trend_fast_ma": base["trend_fast_ma"],
                "trend_slow_ma": base["trend_slow_ma"],
                "long_ma": base["long_ma"],
                "min_trend_score": max(3, base["min_trend_score"]),
                "min_dte": max(30, base["min_dte"]),
                "target_dte": max(60, base["target_dte"]),
                "max_dte": max(base["max_dte"], 120),
                "fallback_otm_pct": min(max(base["fallback_otm_pct"], 0.0), 0.08),
                "take_profit_pct": min(max(base["take_profit_pct"], 0.5), 1.2),
                "stop_loss_pct": min(max(base["stop_loss_pct"], 0.3), 0.6),
                "dte_exit_days": max(14, base["dte_exit_days"]),
                "trend_exit_below_ma": base["trend_exit_below_ma"],
                "trend_exit_confirm_bars": max(1, base["trend_exit_confirm_bars"]),
            },
            base_risk,
            ["趋势 Call：顺着 MA20/50/200 和动量买入 45-180 DTE Call，核心验证趋势分、DTE、止盈止损和预算。"],
        )
    elif variant == "swing_pullback_call":
        _candidate(
            "swing_pullback_call_baseline",
            "回调买入 Call 基线",
            {
                "mode": "long_call",
                "trend_fast_ma": 20,
                "trend_slow_ma": 50,
                "long_ma": 200,
                "min_trend_score": max(3, base["min_trend_score"]),
                "max_price_above_fast_ma_pct": min(max(base.get("max_price_above_fast_ma_pct", 0.06), 0.025), 0.07),
                "min_dte": 45,
                "target_dte": min(max(base["target_dte"], 75), 120),
                "max_dte": max(base["max_dte"], 150),
                "fallback_otm_pct": min(max(base["fallback_otm_pct"], 0.01), 0.04),
                "take_profit_pct": min(max(base["take_profit_pct"], 0.65), 1.1),
                "stop_loss_pct": min(max(base["stop_loss_pct"], 0.35), 0.5),
                "dte_exit_days": max(24, base["dte_exit_days"]),
                "trend_exit_below_ma": 50,
                "trend_exit_confirm_bars": 2,
            },
            {**base_risk, "max_contracts_per_order": 1},
            ["回调 Call：只在大趋势仍向上且价格不过度远离 MA20 时入场，减少追高。"],
        )
    elif variant == "swing_breakout_call":
        _candidate(
            "swing_breakout_call_baseline",
            "突破买入 Call 基线",
            {
                "mode": "long_call",
                "trend_fast_ma": 10,
                "trend_slow_ma": 30,
                "long_ma": 150,
                "min_trend_score": max(4, base["min_trend_score"]),
                "max_price_above_fast_ma_pct": min(max(base.get("max_price_above_fast_ma_pct", 0.10), 0.06), 0.14),
                "min_dte": 45,
                "target_dte": min(max(base["target_dte"], 60), 100),
                "max_dte": max(base["max_dte"], 120),
                "fallback_otm_pct": min(max(base["fallback_otm_pct"], 0.02), 0.05),
                "take_profit_pct": min(max(base["take_profit_pct"], 0.8), 1.4),
                "stop_loss_pct": min(max(base["stop_loss_pct"], 0.30), 0.42),
                "dte_exit_days": max(18, base["dte_exit_days"]),
                "trend_exit_below_ma": 30,
                "trend_exit_confirm_bars": 1,
            },
            {**base_risk, "max_contracts_per_order": 1, "max_premium_per_order": min(base_risk["max_premium_per_order"], 700.0)},
            ["突破 Call：提高趋势分并收紧止损，适合强趋势突破，但不放宽单笔预算。"],
        )
    else:
        _candidate(
            "swing_event_filtered_call_baseline",
            "事件过滤趋势 Call 基线",
            {
                "mode": "long_call",
                "trend_fast_ma": 20,
                "trend_slow_ma": 50,
                "long_ma": 200,
                "min_trend_score": max(4, base["min_trend_score"]),
                "min_dte": 60,
                "target_dte": min(max(base["target_dte"], 90), 150),
                "max_dte": max(base["max_dte"], 180),
                "fallback_otm_pct": min(max(base["fallback_otm_pct"], 0.01), 0.04),
                "take_profit_pct": min(max(base["take_profit_pct"], 0.7), 1.2),
                "stop_loss_pct": min(max(base["stop_loss_pct"], 0.32), 0.45),
                "dte_exit_days": max(30, base["dte_exit_days"]),
                "earnings_blackout_days": max(10, _safe_int(base.get("earnings_blackout_days"), 7)),
                "trend_exit_below_ma": 50,
                "trend_exit_confirm_bars": 2,
            },
            {**base_risk, "max_contracts_per_order": 1, "max_total_option_premium": min(base_risk["max_total_option_premium"], 3000.0)},
            ["事件过滤 Call：更长 DTE 和更严格趋势门槛，配合事件黑名单/财报窗口降低跳空风险。"],
        )
    if dimension in {"risk_controls", "combined", "leg_gap"}:
        _candidate(
            f"{variant}_debit_spread_budget",
            "Call Debit Spread 控权利金",
            {
                "mode": "call_debit_spread",
                "trend_fast_ma": base["trend_fast_ma"],
                "trend_slow_ma": base["trend_slow_ma"],
                "long_ma": base["long_ma"],
                "min_trend_score": max(3, base["min_trend_score"]),
                "min_dte": max(45, base["min_dte"]),
                "target_dte": min(max(base["target_dte"], 75), 120),
                "max_dte": max(base["max_dte"], 150),
                "fallback_otm_pct": min(max(base["fallback_otm_pct"], 0.02), 0.06),
                "spread_width_pct": min(max(base["spread_width_pct"], 0.03), 0.08),
                "max_spread_debit": min(max(base["max_spread_debit"], 300.0), 700.0),
                "max_spread_debit_to_width_pct": min(max(base["max_spread_debit_to_width_pct"], 0.35), 0.45),
                "spread_min_hold_days_before_stop": max(3, min(base["spread_min_hold_days_before_stop"], 8)),
                "sim_spread_slippage_pct": 0.006,
                "max_bid_ask_spread_pct": min(base["max_bid_ask_spread_pct"], 0.16),
                "take_profit_pct": min(max(base["take_profit_pct"], 0.45), 0.9),
                "stop_loss_pct": min(max(base["stop_loss_pct"], 0.28), 0.45),
                "dte_exit_days": max(21, base["dte_exit_days"]),
                "trend_exit_below_ma": 50,
                "trend_exit_confirm_bars": max(1, base["trend_exit_confirm_bars"]),
            },
            {
                **base_risk,
                "max_contracts_per_order": 1,
                "max_premium_per_order": min(max(base["max_premium_per_order"], 300.0), 700.0),
                "max_new_premium_per_day": min(max(base["max_new_premium_per_day"], 500.0), 1500.0),
            },
            [
                "用 Call Debit Spread 限定单笔最大亏损和权利金占用；当前实盘 worker 只允许研究/预览，暂不开放价差结构自动提交。",
                "适合高价股或 Long Call 权利金经常超预算的股票池，但上方卖腿会限制极端上涨收益。",
            ],
        )
    if dimension in {"risk_controls", "combined"}:
        _candidate(
            f"{variant}_drawdown_guard",
            "回撤保护型",
            {
                "mode": "long_call",
                "min_trend_score": max(4, base["min_trend_score"]),
                "target_dte": min(max(base["target_dte"], 75), 120),
                "max_bid_ask_spread_pct": min(base["max_bid_ask_spread_pct"], 0.15),
                "take_profit_pct": min(max(base["take_profit_pct"], 0.65), 0.95),
                "stop_loss_pct": min(max(base["stop_loss_pct"], 0.3), 0.4),
                "dte_exit_days": max(28, base["dte_exit_days"]),
                "trend_exit_below_ma": 50,
                "trend_exit_confirm_bars": 2,
            },
            {
                "max_contracts_per_order": 1,
                "max_open_contracts": min(max(base["max_open_contracts"], 3), 8),
                "max_premium_per_order": min(max(base["max_premium_per_order"], 300.0), 700.0),
                "max_premium_per_symbol": min(max(base["max_premium_per_symbol"], 500.0), 1200.0),
                "max_total_option_premium": min(max(base["max_total_option_premium"], 1000.0), 3000.0),
            },
            ["提高趋势分门槛，提前 DTE 退出，收紧止损和预算，目标是降低单笔与组合回撤。"],
        )
    if dimension in {"time_window", "combined", "leg_gap"}:
        _candidate(
            f"{variant}_longer_dte",
            "更长 DTE 持有",
            {
                "mode": "long_call",
                "trend_fast_ma": 20,
                "trend_slow_ma": 50,
                "long_ma": 200,
                "min_trend_score": max(3, base["min_trend_score"]),
                "min_dte": 60,
                "target_dte": 120,
                "max_dte": 210,
                "fallback_otm_pct": min(max(base["fallback_otm_pct"], 0.02), 0.06),
                "take_profit_pct": min(max(base["take_profit_pct"], 0.8), 1.5),
                "stop_loss_pct": min(max(base["stop_loss_pct"], 0.4), 0.55),
                "dte_exit_days": 35,
                "trend_exit_below_ma": 50,
                "trend_exit_confirm_bars": 3,
            },
            {
                "max_contracts_per_order": 1,
                "max_open_contracts": min(max(base["max_open_contracts"], 4), 10),
                "max_premium_per_order": min(max(base["max_premium_per_order"], 500.0), 1000.0),
                "max_premium_per_symbol": min(max(base["max_premium_per_symbol"], 800.0), 1800.0),
                "max_total_option_premium": min(max(base["max_total_option_premium"], 1500.0), 5000.0),
            },
            ["拉长目标 DTE，给趋势更多时间，但使用 3 根确认的 MA50 破坏作为退出过滤。"],
        )
    return candidates[: max(1, min(10, int(max_candidates)))]


def _volume_action(
    volume_ratio: float | None,
    abs_change_pct: float | None,
    data_quality_ok: bool,
    strategy_variant: str = "morning_strangle",
) -> tuple[str, float, list[str]]:
    reasons: list[str] = []
    action = "normal_size"
    confidence = 0.62
    if not data_quality_ok:
        action = "skip"
        confidence = 0.35
        reasons.append("数据质量存在 error，候选只用于研究，不建议写入实盘。")
    if volume_ratio is not None:
        if volume_ratio < 0.7:
            action = "reduce_size" if action != "skip" else action
            confidence -= 0.08
            reasons.append(f"今日量能相对偏低（约 {volume_ratio:.2f}×），建议降低尺寸或等待确认。")
        elif volume_ratio >= 1.2:
            confidence += 0.08
            reasons.append(f"今日量能充足（约 {volume_ratio:.2f}×），参数可用性相对更高。")
    if abs_change_pct is not None:
        if strategy_variant in {"morning_strangle", "morning_double_strangle"} and abs_change_pct > 0.85:
            action = "skip" if action != "normal_size" else "reduce_size"
            confidence -= 0.12
            reasons.append(f"相对前收波动约 {abs_change_pct:.2f}%，不完全符合早盘宽跨的窄幅假设。")
        elif strategy_variant in {"morning_strangle", "morning_double_strangle"} and abs_change_pct <= 0.35:
            confidence += 0.08
            reasons.append(f"相对前收波动约 {abs_change_pct:.2f}%，更接近早盘宽跨假设。")
        elif strategy_variant == "morning_directional" and abs_change_pct >= 0.85:
            confidence += 0.08
            reasons.append(f"相对前收波动约 {abs_change_pct:.2f}%，更接近早盘方向单假设。")
        elif strategy_variant == "morning_directional" and abs_change_pct < 0.45:
            action = "reduce_size" if action != "skip" else action
            confidence -= 0.08
            reasons.append(f"相对前收波动约 {abs_change_pct:.2f}%，方向性偏弱，建议降低尺寸或跳过。")
    return action, round(max(0.1, min(0.95, confidence)), 2), reasons


def generate_candidate_parameters(
    data_quality: dict[str, Any],
    max_candidates: int = 3,
    strategy_variant: str = "morning_strangle",
    candidate_generator: str = "deterministic",
    research_dimension: str = "risk_controls",
) -> list[dict[str, Any]]:
    live_config = data_quality.get("current_config") if isinstance(data_quality.get("current_config"), dict) else {}
    variant = normalize_lab_strategy_variant(strategy_variant)
    generator = normalize_candidate_generator(candidate_generator)
    dimension = normalize_research_dimension(research_dimension)
    base = _strategy_base_from_live_config(live_config, variant)
    recommendation = data_quality.get("strategy_recommendation") if isinstance(data_quality.get("strategy_recommendation"), dict) else {}
    features = recommendation.get("features") if isinstance(recommendation.get("features"), dict) else {}
    volume_ratio = features.get("volume_ratio_today_vs_recent_days")
    volume_ratio_f = _safe_float(volume_ratio, float("nan"))
    if not math.isfinite(volume_ratio_f):
        volume_ratio_f = None
    change_pct = features.get("change_pct_from_prev_close")
    change_pct_f = _safe_float(change_pct, float("nan"))
    abs_change_pct = abs(change_pct_f) if math.isfinite(change_pct_f) else None
    dq_ok = bool(data_quality.get("ok"))
    action, confidence, action_reasons = _volume_action(volume_ratio_f, abs_change_pct, dq_ok, variant)
    generator_reasons: list[str] = []
    generator_mode = "deterministic_mvp"
    if generator == "tradingagents":
        generator_mode = "tradingagents_adapter"
        generator_reasons.append(
            "TradingAgents 候选入口已启用：当前版本把数据质量、策略推荐快照和行情特征转成候选参数，仍必须经过确定性回测和人工审批。"
        )

    current_start = _hhmm_minutes(base.get("strangle_entry_start_hhmm_et"), "09:35")
    current_end = _hhmm_minutes(base.get("strangle_entry_end_hhmm_et"), "10:00")
    current_force = _hhmm_minutes(base.get("strangle_force_close_hhmm_et"), "12:00")
    base_range = max(0.001, _safe_float(base.get("strangle_range_pct"), 0.003))
    base_tp = max(0.05, _safe_float(base.get("strangle_take_profit_return"), 0.5))
    base_stop = max(0.0, _safe_float(base.get("strangle_stop_loss_return"), 0.0))
    base_stop_cooldown = max(0, _safe_int(base.get("strangle_stop_loss_cooldown_minutes"), 0))
    base_leg_sl = max(0.0, _safe_float(base.get("strangle_leg_stop_loss_pct"), 0.0))
    base_directional_tp = max(0.05, _safe_float(base.get("directional_take_profit_return"), 1.0))
    base_directional_sl = max(0.0, _safe_float(base.get("directional_stop_loss_pct"), 0.0))
    base_short_tp = _safe_float(base.get("strangle_short_leg_take_profit_pct"), 0.0) or 0.30
    base_long_tp = _safe_float(base.get("strangle_long_leg_take_profit_pct"), 0.0) or max(0.80, base_short_tp)
    base_call_steps = max(0, _safe_int(base.get("call_strikes_otm"), 0))
    base_put_steps = max(0, _safe_int(base.get("put_strikes_otm"), 0))
    near_call_steps = max(0, base_call_steps - 1)
    near_put_steps = max(0, base_put_steps - 1)
    wide_call_steps = min(12, base_call_steps + 1)
    wide_put_steps = min(12, base_put_steps + 1)
    base_d_call_long_steps = max(1, _safe_int(base.get("double_strangle_call_long_strikes_otm"), 2))
    base_d_call_short_steps = max(0, _safe_int(base.get("double_strangle_call_short_strikes_otm"), 1))
    base_d_put_long_steps = max(1, _safe_int(base.get("double_strangle_put_long_strikes_otm"), 2))
    base_d_put_short_steps = max(0, _safe_int(base.get("double_strangle_put_short_strikes_otm"), 1))
    if base_d_call_long_steps <= base_d_call_short_steps:
        base_d_call_long_steps = base_d_call_short_steps + 1
    if base_d_put_long_steps <= base_d_put_short_steps:
        base_d_put_long_steps = base_d_put_short_steps + 1
    base_d_call_long_tp = _safe_float(base.get("double_strangle_call_long_leg_take_profit_pct"), 1.0)
    base_d_call_short_tp = _safe_float(base.get("double_strangle_call_short_leg_take_profit_pct"), 0.35)
    base_d_put_long_tp = _safe_float(base.get("double_strangle_put_long_leg_take_profit_pct"), 1.0)
    base_d_put_short_tp = _safe_float(base.get("double_strangle_put_short_leg_take_profit_pct"), 0.35)
    base_d_leg_sl = max(0.0, _safe_float(base.get("double_strangle_single_leg_stop_loss_pct"), 0.35))
    base_d_combo_tp = max(0.05, _safe_float(base.get("double_strangle_combo_take_profit_pct"), 0.60))
    base_d_combo_sl = max(0.0, _safe_float(base.get("double_strangle_combo_stop_loss_pct"), 0.30))
    near_d_call_short_steps = max(0, base_d_call_short_steps - 1)
    near_d_call_long_steps = max(near_d_call_short_steps + 1, base_d_call_long_steps - 1)
    near_d_put_short_steps = max(0, base_d_put_short_steps - 1)
    near_d_put_long_steps = max(near_d_put_short_steps + 1, base_d_put_long_steps - 1)
    wide_d_call_short_steps = min(11, base_d_call_short_steps + 1)
    wide_d_call_long_steps = min(12, max(wide_d_call_short_steps + 1, base_d_call_long_steps + 1))
    wide_d_put_short_steps = min(11, base_d_put_short_steps + 1)
    wide_d_put_long_steps = min(12, max(wide_d_put_short_steps + 1, base_d_put_long_steps + 1))

    def _double_gap_patch(gap: int) -> dict[str, Any]:
        g = max(1, min(8, int(gap)))
        cs = max(0, min(10, base_d_call_short_steps))
        ps = max(0, min(10, base_d_put_short_steps))
        return {
            "strategy_variant": "morning_double_strangle",
            "max_trades_per_day": 1,
            "double_strangle_call_long_strikes_otm": min(12, cs + g),
            "double_strangle_call_short_strikes_otm": cs,
            "double_strangle_put_long_strikes_otm": min(12, ps + g),
            "double_strangle_put_short_strikes_otm": ps,
            "double_strangle_call_long_leg_take_profit_pct": base_d_call_long_tp,
            "double_strangle_call_short_leg_take_profit_pct": base_d_call_short_tp,
            "double_strangle_put_long_leg_take_profit_pct": base_d_put_long_tp,
            "double_strangle_put_short_leg_take_profit_pct": base_d_put_short_tp,
            "double_strangle_single_leg_stop_loss_pct": base_d_leg_sl,
            "double_strangle_combo_take_profit_pct": base_d_combo_tp,
            "double_strangle_combo_stop_loss_pct": base_d_combo_sl,
        }

    candidates: list[dict[str, Any]] = []

    def _candidate(
        candidate_id: str,
        title: str,
        patch: dict[str, Any],
        reasons: list[str],
        local_action: str | None = None,
        local_confidence: float | None = None,
    ) -> None:
        merged = dict(base)
        merged.update(patch)
        merged = _known_strategy_config(merged)
        merged["strategy_variant"] = variant
        candidates.append(
            {
                "candidate_id": candidate_id,
                "title": title,
                "generator": generator,
                "generator_mode": generator_mode,
                "research_dimension": dimension,
                "agent_action": local_action or action,
                "confidence": round(float(local_confidence if local_confidence is not None else confidence), 2),
                "strategy_config_patch": patch,
                "strategy_config": merged,
                "research_controls": {
                    "max_bid_ask_spread_pct": 12,
                    "min_volume_ratio": 1.2,
                    "avoid_event_minutes": 30,
                    "human_approval_required": True,
                    "l3_confirmation_required_for_orders": True,
                },
                "reasoning": generator_reasons + reasons + action_reasons,
                "safety_note": "候选参数只进入回测验证与人工审批；不会直接下单，也不会绕过 worker 的硬风控。",
            }
        )

    early_window = {
        "strangle_entry_start_hhmm_et": _fmt_hhmm(max(current_start, _hhmm_minutes("09:35", "09:35"))),
        "strangle_entry_end_hhmm_et": _fmt_hhmm(min(max(current_end, _hhmm_minutes("10:15", "10:15")), _hhmm_minutes("10:45", "10:45"))),
        "strangle_force_close_hhmm_et": _fmt_hhmm(min(current_force, _hhmm_minutes("11:30", "11:30"))),
    }
    late_window = {
        "strangle_entry_start_hhmm_et": _fmt_hhmm(max(current_start, _hhmm_minutes("10:05", "10:05"))),
        "strangle_entry_end_hhmm_et": _fmt_hhmm(min(max(current_end, _hhmm_minutes("10:45", "10:45")), _hhmm_minutes("11:00", "11:00"))),
        "strangle_force_close_hhmm_et": _fmt_hhmm(min(current_force, _hhmm_minutes("11:30", "11:30"))),
    }

    if variant == "morning_directional":
        directional_threshold = 0.0085 if abs_change_pct is None or abs_change_pct >= 0.85 else 0.0045
        _candidate(
            "directional_baseline_guarded",
            "当前方向单安全归一化",
            {
                "strategy_variant": "morning_directional",
                "max_trades_per_day": max(1, min(2, _safe_int(base.get("max_trades_per_day"), 1))),
                "initial_option_contracts": max(1, _safe_int(base.get("initial_option_contracts"), 1)),
                "strangle_entry_end_hhmm_et": _fmt_hhmm(min(current_end, _hhmm_minutes("11:00", "11:00"))),
                "strangle_force_close_hhmm_et": _fmt_hhmm(min(current_force, _hhmm_minutes("12:00", "12:00"))),
                "directional_take_profit_return": base_directional_tp,
                "directional_stop_loss_pct": base_directional_sl,
                "call_strikes_otm": base_call_steps,
                "put_strikes_otm": base_put_steps,
            },
            ["以当前 live_worker_config 为基线，验证早盘方向单参数。"],
        )
        if dimension == "time_window":
            _candidate(
                "directional_early_window",
                "较早确认时间窗",
                {"strategy_variant": "morning_directional", **early_window},
                ["仅验证入场开始、入场结束和强平时刻，不主动改变止盈止损或步长。"],
            )
            _candidate(
                "directional_late_window",
                "延后确认时间窗",
                {"strategy_variant": "morning_directional", **late_window},
                ["仅验证更晚的确认窗口，便于把收益差异归因到时间。"],
                local_action="reduce_size" if action == "normal_size" else action,
                local_confidence=max(0.1, confidence - 0.05),
            )
        elif dimension == "combined":
            _candidate(
                "directional_open_momentum",
                "开盘动量综合方向单",
                {
                    "strategy_variant": "morning_directional",
                    "max_trades_per_day": 1,
                    **early_window,
                    "directional_down_pct": directional_threshold,
                    "directional_up_pct": directional_threshold,
                    "directional_take_profit_return": min(max(base_directional_tp, 0.45), 0.90),
                    "directional_stop_loss_pct": max(base_directional_sl, 0.25),
                    "call_strikes_otm": near_call_steps,
                    "put_strikes_otm": near_put_steps,
                },
                ["综合验证时间窗口、方向触发阈值、止盈止损和选约步长；这是混合变量，若通过建议再单独拆分验证。"],
            )
            _candidate(
                "directional_late_confirmed",
                "延后确认综合方向单",
                {
                    "strategy_variant": "morning_directional",
                    "max_trades_per_day": 1,
                    "initial_option_contracts": 1,
                    **late_window,
                    "directional_down_pct": min(max(directional_threshold, 0.006), 0.012),
                    "directional_up_pct": min(max(directional_threshold, 0.006), 0.012),
                    "directional_take_profit_return": min(max(base_directional_tp, 0.35), 0.75),
                    "directional_stop_loss_pct": max(base_directional_sl, 0.25),
                    "call_strikes_otm": wide_call_steps,
                    "put_strikes_otm": wide_put_steps,
                },
                ["综合验证延后时间窗、更严格方向确认、止盈止损和远一步选约；这是混合变量，需二次验证确认归因。"],
                local_action="reduce_size" if action == "normal_size" else action,
                local_confidence=max(0.1, confidence - 0.05),
            )
        else:
            _candidate(
                "directional_risk_near_tp",
                "近一步止盈方向单",
                {
                    "strategy_variant": "morning_directional",
                    "max_trades_per_day": 1,
                    "directional_down_pct": directional_threshold,
                    "directional_up_pct": directional_threshold,
                    "directional_take_profit_return": min(max(base_directional_tp, 0.45), 0.90),
                    "directional_stop_loss_pct": max(base_directional_sl, 0.25),
                    "call_strikes_otm": near_call_steps,
                    "put_strikes_otm": near_put_steps,
                },
                ["只验证方向阈值、止盈止损和近一步选约，不改变时间窗口。"],
            )
            _candidate(
                "directional_risk_wide_guarded",
                "远一步止损方向单",
                {
                    "strategy_variant": "morning_directional",
                    "max_trades_per_day": 1,
                    "initial_option_contracts": 1,
                    "directional_down_pct": min(max(directional_threshold, 0.006), 0.012),
                    "directional_up_pct": min(max(directional_threshold, 0.006), 0.012),
                    "directional_take_profit_return": min(max(base_directional_tp, 0.35), 0.75),
                    "directional_stop_loss_pct": max(base_directional_sl, 0.25),
                    "call_strikes_otm": wide_call_steps,
                    "put_strikes_otm": wide_put_steps,
                },
                ["只验证更保守的方向阈值、止盈止损和远一步选约，不改变时间窗口。"],
                local_action="reduce_size" if action == "normal_size" else action,
                local_confidence=max(0.1, confidence - 0.05),
            )
    elif variant == "morning_double_strangle":
        _candidate(
            "double_strangle_baseline_guarded",
            "早盘双宽跨基线",
            {
                "strategy_variant": "morning_double_strangle",
                "max_trades_per_day": 1,
                "initial_option_contracts": max(1, _safe_int(base.get("initial_option_contracts"), 1)),
                "strangle_entry_start_hhmm_et": _fmt_hhmm(current_start),
                "strangle_entry_end_hhmm_et": _fmt_hhmm(min(current_end, _hhmm_minutes("11:00", "11:00"))),
                "strangle_force_close_hhmm_et": _fmt_hhmm(min(current_force, _hhmm_minutes("12:00", "12:00"))),
                "strangle_range_pct": base_range,
                "double_strangle_call_long_strikes_otm": base_d_call_long_steps,
                "double_strangle_call_short_strikes_otm": base_d_call_short_steps,
                "double_strangle_put_long_strikes_otm": base_d_put_long_steps,
                "double_strangle_put_short_strikes_otm": base_d_put_short_steps,
                "double_strangle_call_long_leg_take_profit_pct": base_d_call_long_tp,
                "double_strangle_call_short_leg_take_profit_pct": base_d_call_short_tp,
                "double_strangle_put_long_leg_take_profit_pct": base_d_put_long_tp,
                "double_strangle_put_short_leg_take_profit_pct": base_d_put_short_tp,
                "double_strangle_single_leg_stop_loss_pct": base_d_leg_sl,
                "double_strangle_combo_take_profit_pct": base_d_combo_tp,
                "double_strangle_combo_stop_loss_pct": base_d_combo_sl,
            },
            ["以当前 live_worker_config 为基线，验证四腿双宽跨的步长、单腿止盈和组合风控。"],
        )
        if dimension == "leg_gap":
            _candidate(
                "double_strangle_gap_1",
                "Double strangle gap 1",
                _double_gap_patch(1),
                ["Only changes the long/short leg gap to 1 strike step; timing and TP/SL stay at baseline."],
            )
            _candidate(
                "double_strangle_gap_2",
                "Double strangle gap 2",
                _double_gap_patch(2),
                ["Only changes the long/short leg gap to 2 strike steps; timing and TP/SL stay at baseline."],
            )
            _candidate(
                "double_strangle_gap_3",
                "Double strangle gap 3",
                _double_gap_patch(3),
                ["Only changes the long/short leg gap to 3 strike steps; timing and TP/SL stay at baseline."],
                local_action="reduce_size" if action == "normal_size" else action,
                local_confidence=max(0.1, confidence - 0.04),
            )
        elif dimension == "time_window":
            _candidate(
                "double_strangle_early_window",
                "双宽跨较早窗口",
                {"strategy_variant": "morning_double_strangle", **early_window},
                ["只验证入场开始、入场结束和强平时刻，不主动改变四腿步长或止盈止损。"],
            )
            _candidate(
                "double_strangle_late_window",
                "双宽跨延后窗口",
                {"strategy_variant": "morning_double_strangle", **late_window},
                ["只验证更晚确认窗口，便于观察双宽跨对时间窗口的敏感度。"],
                local_action="reduce_size" if action == "normal_size" else action,
                local_confidence=max(0.1, confidence - 0.05),
            )
        elif dimension == "combined":
            _candidate(
                "double_strangle_near_fast_tp",
                "近腿快止盈双宽跨",
                {
                    "strategy_variant": "morning_double_strangle",
                    "max_trades_per_day": 1,
                    **early_window,
                    "strangle_range_pct": max(0.0015, min(base_range, 0.006 if abs_change_pct is None or abs_change_pct <= 0.6 else 0.0035)),
                    "double_strangle_call_long_strikes_otm": near_d_call_long_steps,
                    "double_strangle_call_short_strikes_otm": near_d_call_short_steps,
                    "double_strangle_put_long_strikes_otm": near_d_put_long_steps,
                    "double_strangle_put_short_strikes_otm": near_d_put_short_steps,
                    "double_strangle_call_long_leg_take_profit_pct": min(max(base_d_call_long_tp * 0.85, 0.70), 1.10),
                    "double_strangle_call_short_leg_take_profit_pct": min(max(base_d_call_short_tp * 0.85, 0.22), 0.42),
                    "double_strangle_put_long_leg_take_profit_pct": min(max(base_d_put_long_tp * 0.85, 0.70), 1.10),
                    "double_strangle_put_short_leg_take_profit_pct": min(max(base_d_put_short_tp * 0.85, 0.22), 0.42),
                    "double_strangle_single_leg_stop_loss_pct": base_d_leg_sl,
                    "double_strangle_combo_take_profit_pct": min(max(base_d_combo_tp * 0.85, 0.35), 0.70),
                    "double_strangle_combo_stop_loss_pct": base_d_combo_sl,
                },
                ["综合验证较早窗口、近一组四腿步长和更快落袋的单腿/组合止盈。"],
            )
            _candidate(
                "double_strangle_wide_guarded",
                "远腿保护双宽跨",
                {
                    "strategy_variant": "morning_double_strangle",
                    "max_trades_per_day": 1,
                    "initial_option_contracts": 1,
                    **late_window,
                    "strangle_range_pct": min(max(base_range, 0.003), 0.008),
                    "double_strangle_call_long_strikes_otm": wide_d_call_long_steps,
                    "double_strangle_call_short_strikes_otm": wide_d_call_short_steps,
                    "double_strangle_put_long_strikes_otm": wide_d_put_long_steps,
                    "double_strangle_put_short_strikes_otm": wide_d_put_short_steps,
                    "double_strangle_call_long_leg_take_profit_pct": min(max(base_d_call_long_tp, 0.90), 1.40),
                    "double_strangle_call_short_leg_take_profit_pct": min(max(base_d_call_short_tp, 0.35), 0.60),
                    "double_strangle_put_long_leg_take_profit_pct": min(max(base_d_put_long_tp, 0.90), 1.40),
                    "double_strangle_put_short_leg_take_profit_pct": min(max(base_d_put_short_tp, 0.35), 0.60),
                    "double_strangle_single_leg_stop_loss_pct": min(max(base_d_leg_sl if base_d_leg_sl > 0 else 0.35, 0.25), 0.55),
                    "double_strangle_combo_take_profit_pct": min(max(base_d_combo_tp, 0.45), 0.90),
                    "double_strangle_combo_stop_loss_pct": min(max(base_d_combo_sl if base_d_combo_sl > 0 else 0.25, 0.20), 0.50),
                },
                ["综合验证延后窗口、远一组四腿步长和更明确的单腿/组合止损保护。"],
                local_action="reduce_size" if action == "normal_size" else action,
                local_confidence=max(0.1, confidence - 0.05),
            )
        else:
            _candidate(
                "double_strangle_gap_2_fast_tp",
                "双宽跨止盈敏感度",
                {
                    "strategy_variant": "morning_double_strangle",
                    "max_trades_per_day": 1,
                    "double_strangle_call_long_strikes_otm": _double_gap_patch(2)["double_strangle_call_long_strikes_otm"],
                    "double_strangle_call_short_strikes_otm": _double_gap_patch(2)["double_strangle_call_short_strikes_otm"],
                    "double_strangle_put_long_strikes_otm": _double_gap_patch(2)["double_strangle_put_long_strikes_otm"],
                    "double_strangle_put_short_strikes_otm": _double_gap_patch(2)["double_strangle_put_short_strikes_otm"],
                    "double_strangle_call_long_leg_take_profit_pct": min(max(base_d_call_long_tp * 0.85, 0.70), 1.10),
                    "double_strangle_call_short_leg_take_profit_pct": min(max(base_d_call_short_tp * 0.85, 0.22), 0.42),
                    "double_strangle_put_long_leg_take_profit_pct": min(max(base_d_put_long_tp * 0.85, 0.70), 1.10),
                    "double_strangle_put_short_leg_take_profit_pct": min(max(base_d_put_short_tp * 0.85, 0.22), 0.42),
                    "double_strangle_combo_take_profit_pct": min(max(base_d_combo_tp * 0.85, 0.35), 0.70),
                    "double_strangle_single_leg_stop_loss_pct": base_d_leg_sl,
                    "double_strangle_combo_stop_loss_pct": base_d_combo_sl,
                },
                ["验证四腿步长与长/短腿单腿止盈阈值，观察是否提升落袋效率。"],
            )
            _candidate(
                "double_strangle_gap_3_guarded",
                "双宽跨止损保护",
                {
                    "strategy_variant": "morning_double_strangle",
                    "max_trades_per_day": 1,
                    "initial_option_contracts": 1,
                    "double_strangle_call_long_strikes_otm": _double_gap_patch(3)["double_strangle_call_long_strikes_otm"],
                    "double_strangle_call_short_strikes_otm": _double_gap_patch(3)["double_strangle_call_short_strikes_otm"],
                    "double_strangle_put_long_strikes_otm": _double_gap_patch(3)["double_strangle_put_long_strikes_otm"],
                    "double_strangle_put_short_strikes_otm": _double_gap_patch(3)["double_strangle_put_short_strikes_otm"],
                    "double_strangle_call_long_leg_take_profit_pct": min(max(base_d_call_long_tp, 0.90), 1.40),
                    "double_strangle_call_short_leg_take_profit_pct": min(max(base_d_call_short_tp, 0.35), 0.60),
                    "double_strangle_put_long_leg_take_profit_pct": min(max(base_d_put_long_tp, 0.90), 1.40),
                    "double_strangle_put_short_leg_take_profit_pct": min(max(base_d_put_short_tp, 0.35), 0.60),
                    "double_strangle_single_leg_stop_loss_pct": min(max(base_d_leg_sl if base_d_leg_sl > 0 else 0.35, 0.25), 0.55),
                    "double_strangle_combo_take_profit_pct": min(max(base_d_combo_tp, 0.45), 0.90),
                    "double_strangle_combo_stop_loss_pct": min(max(base_d_combo_sl if base_d_combo_sl > 0 else 0.25, 0.20), 0.50),
                },
                ["验证远一组四腿步长、单腿止损和组合止损，观察最大回撤是否下降。"],
                local_action="reduce_size" if action == "normal_size" else action,
                local_confidence=max(0.1, confidence - 0.05),
            )
    else:
        _candidate(
            "baseline_guarded",
            "当前配置安全归一化",
            {
                "strategy_variant": "morning_strangle",
                "max_trades_per_day": max(1, min(2, _safe_int(base.get("max_trades_per_day"), 1))),
                "initial_option_contracts": max(1, _safe_int(base.get("initial_option_contracts"), 1)),
                "strangle_entry_end_hhmm_et": _fmt_hhmm(min(current_end, _hhmm_minutes("11:00", "11:00"))),
                "strangle_force_close_hhmm_et": _fmt_hhmm(min(current_force, _hhmm_minutes("12:00", "12:00"))),
                "strangle_take_profit_return": base_tp,
                "strangle_stop_loss_return": base_stop,
                "strangle_stop_loss_cooldown_minutes": base_stop_cooldown,
                "strangle_long_leg_take_profit_pct": base_long_tp,
                "strangle_short_leg_take_profit_pct": base_short_tp,
                "strangle_leg_stop_loss_pct": base_leg_sl,
                "call_strikes_otm": base_call_steps,
                "put_strikes_otm": base_put_steps,
            },
            ["以当前 live_worker_config 为基线，只补齐 Lab 需要的安全口径。"],
        )

        if dimension == "time_window":
            _candidate(
                "time_window_early_strangle",
                "较早确认时间窗",
                {"strategy_variant": "morning_strangle", **early_window},
                ["仅验证入场开始、入场结束和强平时刻，不主动改变止盈止损或 Call/Put 步长。"],
            )
            _candidate(
                "time_window_late_strangle",
                "延后确认时间窗",
                {"strategy_variant": "morning_strangle", **late_window},
                ["仅验证更晚的确认窗口，便于把收益差异归因到时间。"],
                local_action="reduce_size" if action == "normal_size" else action,
                local_confidence=max(0.1, confidence - 0.05),
            )
        elif dimension == "combined":
            _candidate(
                "combined_near_tp_strangle",
                "近一步止盈综合宽跨",
                {
                    "strategy_variant": "morning_strangle",
                    "max_trades_per_day": 1,
                    **early_window,
                    "strangle_range_pct": max(0.0015, min(base_range, 0.006 if abs_change_pct is None or abs_change_pct <= 0.6 else 0.0035)),
                    "strangle_take_profit_return": min(max(base_tp * 0.75, 0.28), 0.55),
                    "strangle_stop_loss_return": base_stop,
                    "strangle_stop_loss_cooldown_minutes": base_stop_cooldown,
                    "strangle_long_leg_take_profit_pct": min(max(base_long_tp * 0.85, 0.70), 1.10),
                    "strangle_short_leg_take_profit_pct": min(max(base_short_tp * 0.85, 0.22), 0.42),
                    "strangle_leg_stop_loss_pct": base_leg_sl,
                    "call_strikes_otm": near_call_steps,
                    "put_strikes_otm": near_put_steps,
                },
                ["综合验证较早时间窗、近一步选约和更快止盈；这是混合变量，若通过建议再按时间/风控拆分验证。"],
            )
            _candidate(
                "combined_wide_sl_strangle",
                "远一步止损综合宽跨",
                {
                    "strategy_variant": "morning_strangle",
                    "max_trades_per_day": 1,
                    "initial_option_contracts": 1,
                    **late_window,
                    "strangle_range_pct": min(max(base_range, 0.003), 0.008),
                    "strangle_take_profit_return": min(max(base_tp, 0.40), 0.80),
                    "strangle_stop_loss_return": min(max(base_stop if base_stop > 0 else 0.25, 0.20), 0.50),
                    "strangle_stop_loss_cooldown_minutes": max(base_stop_cooldown, 8),
                    "strangle_long_leg_take_profit_pct": min(max(base_long_tp, 0.90), 1.40),
                    "strangle_short_leg_take_profit_pct": min(max(base_short_tp, 0.35), 0.60),
                    "strangle_leg_stop_loss_pct": min(max(base_leg_sl if base_leg_sl > 0 else 0.35, 0.25), 0.55),
                    "call_strikes_otm": wide_call_steps,
                    "put_strikes_otm": wide_put_steps,
                },
                ["综合验证延后时间窗、远一步选约和止损保护；这是混合变量，需二次验证确认归因。"],
                local_action="reduce_size" if action == "normal_size" else action,
                local_confidence=max(0.1, confidence - 0.05),
            )
        else:
            _candidate(
                "take_profit_sensitive_strangle",
                "近一步止盈宽跨",
                {
                    "strategy_variant": "morning_strangle",
                    "max_trades_per_day": 1,
                    "strangle_take_profit_return": min(max(base_tp * 0.75, 0.28), 0.55),
                    "strangle_stop_loss_return": base_stop,
                    "strangle_stop_loss_cooldown_minutes": base_stop_cooldown,
                    "strangle_long_leg_take_profit_pct": min(max(base_long_tp * 0.85, 0.70), 1.10),
                    "strangle_short_leg_take_profit_pct": min(max(base_short_tp * 0.85, 0.22), 0.42),
                    "strangle_leg_stop_loss_pct": base_leg_sl,
                    "call_strikes_otm": near_call_steps,
                    "put_strikes_otm": near_put_steps,
                },
                ["验证 Call/Put 近一步 OTM 与更快组合/单腿止盈，判断是否能提高成交和落袋效率。"],
            )
            _candidate(
                "stop_loss_guarded_strangle",
                "远一步止损宽跨",
                {
                    "strategy_variant": "morning_strangle",
                    "max_trades_per_day": 1,
                    "initial_option_contracts": 1,
                    "strangle_take_profit_return": min(max(base_tp, 0.40), 0.80),
                    "strangle_stop_loss_return": min(max(base_stop if base_stop > 0 else 0.25, 0.20), 0.50),
                    "strangle_stop_loss_cooldown_minutes": max(base_stop_cooldown, 8),
                    "strangle_long_leg_take_profit_pct": min(max(base_long_tp, 0.90), 1.40),
                    "strangle_short_leg_take_profit_pct": min(max(base_short_tp, 0.35), 0.60),
                    "strangle_leg_stop_loss_pct": min(max(base_leg_sl if base_leg_sl > 0 else 0.35, 0.25), 0.55),
                    "call_strikes_otm": wide_call_steps,
                    "put_strikes_otm": wide_put_steps,
                },
                ["验证 Call/Put 远一步 OTM 与组合止损、单腿止损、冷却时间，判断是否能降低回撤。"],
                local_action="reduce_size" if action == "normal_size" else action,
                local_confidence=max(0.1, confidence - 0.05),
            )

    return candidates[: max(1, min(10, int(max_candidates)))]


def _trade_net_pnls(trades: list[Any]) -> list[float]:
    pnls: list[float] = []
    for row in trades:
        if not isinstance(row, dict) or row.get("event") != "close":
            continue
        pnls.append(_safe_float(row.get("net_pnl"), 0.0))
    return pnls


def _max_drawdown(values: list[float]) -> float:
    peak = 0.0
    cur = 0.0
    worst = 0.0
    for v in values:
        cur += v
        peak = max(peak, cur)
        worst = min(worst, cur - peak)
    return round(worst, 4)


def _max_consecutive_losses(values: list[float]) -> int:
    worst = 0
    cur = 0
    for v in values:
        if v <= 0:
            cur += 1
            worst = max(worst, cur)
        else:
            cur = 0
    return worst


def _max_daily_loss(trades: list[Any]) -> float:
    by_day: dict[str, float] = {}
    for row in trades:
        if not isinstance(row, dict) or row.get("event") != "close":
            continue
        ts = str(row.get("bar_time_et") or row.get("bar_time_local") or row.get("bar_time_raw") or "")[:10]
        if not ts:
            ts = "unknown"
        by_day[ts] = by_day.get(ts, 0.0) + _safe_float(row.get("net_pnl"), 0.0)
    if not by_day:
        return 0.0
    return round(min(by_day.values()), 4)


def _metrics_from_backtest(result: dict[str, Any]) -> dict[str, Any]:
    stats = result.get("stats") if isinstance(result.get("stats"), dict) else {}
    trades = result.get("trades") if isinstance(result.get("trades"), list) else []
    pnls = _trade_net_pnls(trades)
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "bar_count": _safe_int(result.get("bar_count"), 0),
        "open_events": _safe_int(result.get("open_events"), 0),
        "close_events": _safe_int(result.get("close_events"), 0),
        "closed_trades": _safe_int(stats.get("closed_trades"), len(pnls)),
        "wins": _safe_int(stats.get("wins"), len(wins)),
        "losses": _safe_int(stats.get("losses"), len(losses)),
        "win_rate_pct": _safe_float(stats.get("win_rate_pct"), 0.0),
        "realized_pnl": _safe_float(result.get("realized_pnl"), 0.0),
        "return_pct": result.get("return_pct"),
        "total_fee": _safe_float(result.get("total_fee"), 0.0),
        "expectancy_usd": round(mean(pnls), 4) if pnls else 0.0,
        "profit_factor": round(gross_win / gross_loss, 4) if gross_loss > 0 else None,
        "max_drawdown_usd": _max_drawdown(pnls),
        "max_daily_loss_usd": _max_daily_loss(trades),
        "max_consecutive_losses": _max_consecutive_losses(pnls),
    }


def _default_backtest_runner(body: dict[str, Any]) -> dict[str, Any]:
    _ensure_repo_import_paths()
    from api import runtime_bridge as rt

    return rt.qqq_0dte_backtest(body)


def _normalize_validation_windows(raw: Any) -> list[int]:
    if not isinstance(raw, list) or not raw:
        return list(DEFAULT_VALIDATION_WINDOWS_DAYS)
    out: list[int] = []
    for item in raw:
        n = max(1, min(3650, _safe_int(item, 0)))
        if n and n not in out:
            out.append(n)
    return out[:6] or list(DEFAULT_VALIDATION_WINDOWS_DAYS)


def _kline_cache_path(root: Path, symbol: str, kline: str, days: int, periods: int = 0) -> Path:
    stem = str(symbol or "").strip().upper().replace(".", "_").replace("-", "_")
    kl = str(kline or "1d").strip().lower()
    if periods > 0:
        name = f"{stem}__{kl}__p{int(periods)}.json"
    else:
        name = f"{stem}__{kl}__d{max(1, int(days))}.json"
    return root / "data" / "klines" / name


def _parse_bar_dt(value: Any) -> datetime:
    s = str(value or "").strip()
    if not s:
        return datetime.now(timezone.utc)
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def _load_cached_daily_bars(root: Path, symbol: str, days: int) -> tuple[list[dict[str, Any]], str]:
    stem = str(symbol or "").strip().upper().replace(".", "_").replace("-", "_")
    candidates = [
        _kline_cache_path(root, symbol, "1d", days, 0),
        _kline_cache_path(root, symbol, "1d", 0, days),
        _kline_cache_path(root, symbol, "1d", 180, 0),
        _kline_cache_path(root, symbol, "1d", 260, 0),
        _kline_cache_path(root, symbol, "1d", 365, 0),
    ]
    candidates.extend(sorted((root / "data" / "klines").glob(f"{stem}__1d__*.json")))
    best: tuple[list[dict[str, Any]], str] = ([], str(candidates[0]))
    for path in candidates:
        raw = _read_json(path, {}) or {}
        items = raw.get("items") if isinstance(raw, dict) else None
        if not isinstance(items, list) or not items:
            continue
        bars: list[dict[str, Any]] = []
        for row in items:
            if not isinstance(row, dict):
                continue
            try:
                close = float(row.get("close", 0.0) or 0.0)
                bars.append(
                    {
                        "date": _parse_bar_dt(row.get("date")),
                        "open": float(row.get("open", close) or close),
                        "high": float(row.get("high", close) or close),
                        "low": float(row.get("low", close) or close),
                        "close": close,
                        "volume": float(row.get("volume", 0.0) or 0.0),
                    }
                )
            except Exception:
                continue
        bars = [b for b in bars if b.get("close", 0.0) > 0]
        bars.sort(key=lambda x: x["date"])
        if len(bars) > len(best[0]):
            best = (bars, str(path))
    return best


def _sma(values: list[float], n: int) -> float | None:
    n = max(1, int(n))
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def _rsi(values: list[float], n: int = 14) -> float | None:
    if len(values) <= n:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(len(values) - n, len(values)):
        chg = values[i] - values[i - 1]
        if chg >= 0:
            gains += chg
        else:
            losses -= chg
    if losses <= 0:
        return 100.0
    rs = gains / losses
    return 100.0 - (100.0 / (1.0 + rs))


def _trend_signal_from_closes(closes: list[float], strategy: dict[str, Any]) -> dict[str, Any]:
    if len(closes) < max(60, _safe_int(strategy.get("trend_slow_ma"), 50)):
        return {"action": "skip", "reason": "insufficient_daily_bars", "score": 0}
    last = closes[-1]
    prev = closes[-2] if len(closes) >= 2 else closes[-1]
    fast_n = _safe_int(strategy.get("trend_fast_ma"), 20)
    slow_n = _safe_int(strategy.get("trend_slow_ma"), 50)
    long_n = _safe_int(strategy.get("long_ma"), 200)
    fast = _sma(closes, fast_n)
    slow = _sma(closes, slow_n)
    long_ma = _sma(closes, long_n)
    rsi = _rsi(closes, 14)
    score = 0
    reasons: list[str] = []
    if fast is not None and last > fast:
        score += 1
        reasons.append(f"price_above_ma{fast_n}")
    if fast is not None and slow is not None and fast > slow:
        score += 1
        reasons.append(f"ma{fast_n}_above_ma{slow_n}")
    if long_ma is not None and last > long_ma:
        score += 1
        reasons.append(f"price_above_ma{long_n}")
    if last > prev:
        score += 1
        reasons.append("daily_momentum_positive")
    if rsi is not None and 45 <= rsi <= 72:
        score += 1
        reasons.append("rsi_not_extreme")
    fast_gap = (last - fast) / fast if fast else 0.0
    slow_gap = (last - slow) / slow if slow else 0.0
    if slow_gap < _safe_float(strategy.get("min_price_above_slow_ma_pct"), 0.0):
        return {"action": "skip", "reason": "below_slow_ma_threshold", "score": score, "reasons": reasons}
    if fast_gap > _safe_float(strategy.get("max_price_above_fast_ma_pct"), 0.12):
        return {"action": "watch", "reason": "extended_above_fast_ma", "score": score, "reasons": reasons}
    variant = normalize_swing_strategy_variant(strategy.get("strategy_variant"))
    if variant == "swing_pullback_call":
        if fast is None or slow is None or last < slow:
            return {"action": "watch", "reason": "pullback_trend_not_confirmed", "score": score, "reasons": reasons}
        if fast_gap > min(_safe_float(strategy.get("max_price_above_fast_ma_pct"), 0.06), 0.07):
            return {"action": "watch", "reason": "pullback_wait_for_cooldown", "score": score, "reasons": reasons}
        if rsi is not None and rsi > 68:
            return {"action": "watch", "reason": "pullback_rsi_too_hot", "score": score, "reasons": reasons}
    elif variant == "swing_breakout_call":
        lookback = 20
        if len(closes) <= lookback or last < max(closes[-lookback - 1 : -1]):
            return {"action": "watch", "reason": "breakout_not_confirmed", "score": score, "reasons": reasons}
    elif variant == "swing_event_filtered_call":
        if rsi is not None and rsi > 70:
            return {"action": "watch", "reason": "event_filtered_rsi_too_hot", "score": score, "reasons": reasons}
    min_score = _safe_int(strategy.get("min_trend_score"), 3)
    return {
        "action": "candidate_long_call" if score >= min_score else "watch",
        "reason": "trend_candidate" if score >= min_score else "trend_score_low",
        "score": score,
        "reasons": reasons,
        "rsi14": rsi,
        "fast_gap": fast_gap,
        "slow_gap": slow_gap,
    }


def _hist_vol(closes: list[float], lookback: int = 20) -> float:
    if len(closes) < lookback + 1:
        return 0.35
    returns: list[float] = []
    for a, b in zip(closes[-lookback - 1 : -1], closes[-lookback:]):
        if a > 0 and b > 0:
            returns.append(math.log(b / a))
    if len(returns) < 2:
        return 0.35
    avg = sum(returns) / len(returns)
    var = sum((x - avg) ** 2 for x in returns) / max(1, len(returns) - 1)
    return max(0.12, min(1.2, math.sqrt(var) * math.sqrt(252.0)))


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _black_scholes_call(spot: float, strike: float, dte: int, vol: float, rate: float = 0.045) -> float:
    if spot <= 0 or strike <= 0:
        return 0.0
    t = max(1.0 / 365.0, float(dte) / 365.0)
    sigma = max(0.05, min(2.0, float(vol)))
    d1 = (math.log(spot / strike) + (rate + 0.5 * sigma * sigma) * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)
    return max(0.01, spot * _normal_cdf(d1) - strike * math.exp(-rate * t) * _normal_cdf(d2))


def _sim_option_half_spread_pct(strategy: dict[str, Any], dte: int) -> float:
    configured = _safe_float(strategy.get("max_bid_ask_spread_pct"), 0.18)
    dte_adj = 0.08 if int(dte) <= 45 else 0.06
    return max(0.015, min(0.30, max(configured * 0.45, dte_adj)))


def _sim_option_slippage_pct(strategy: dict[str, Any]) -> float:
    return max(0.0, min(0.12, _safe_float(strategy.get("sim_slippage_pct"), 0.02)))


def _sim_option_buy_price(mid: float, strategy: dict[str, Any], dte: int) -> float:
    if mid <= 0:
        return 0.0
    return max(0.01, mid * (1.0 + _sim_option_half_spread_pct(strategy, dte) + _sim_option_slippage_pct(strategy)))


def _sim_option_sell_price(mid: float, strategy: dict[str, Any], dte: int) -> float:
    if mid <= 0:
        return 0.0
    return max(0.01, mid * (1.0 - _sim_option_half_spread_pct(strategy, dte) - _sim_option_slippage_pct(strategy)))


def _sim_spread_half_spread_pct(strategy: dict[str, Any], dte: int) -> float:
    configured = _safe_float(strategy.get("max_bid_ask_spread_pct"), 0.18)
    dte_adj = 0.035 if int(dte) <= 45 else 0.025
    return max(0.01, min(0.12, max(configured * 0.20, dte_adj)))


def _sim_spread_slippage_pct(strategy: dict[str, Any]) -> float:
    return max(0.0, min(0.04, _safe_float(strategy.get("sim_spread_slippage_pct"), 0.006)))


def _sim_spread_buy_price(mid: float, strategy: dict[str, Any], dte: int) -> float:
    if mid <= 0:
        return 0.0
    return max(0.01, mid * (1.0 + _sim_spread_half_spread_pct(strategy, dte) + _sim_spread_slippage_pct(strategy)))


def _sim_spread_sell_price(mid: float, strategy: dict[str, Any], dte: int) -> float:
    if mid <= 0:
        return 0.0
    return max(0.0, mid * (1.0 - _sim_spread_half_spread_pct(strategy, dte) - _sim_spread_slippage_pct(strategy)))


def _compute_trade_stats(pnls: list[float], trade_returns: list[float]) -> dict[str, Any]:
    total = round(sum(pnls), 2)
    premium = sum(abs(p / r) for p, r in zip(pnls, trade_returns) if abs(r) > 1e-9)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    max_loss = 0.0
    consec = 0
    max_consec = 0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
        max_loss = min(max_loss, pnl)
        if pnl < 0:
            consec += 1
            max_consec = max(max_consec, consec)
        else:
            consec = 0
    closed = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    return {
        "realized_pnl": total,
        "return_pct": round((total / premium * 100.0), 2) if premium > 0 else None,
        "win_rate_pct": round(wins / closed * 100.0, 2) if closed else 0.0,
        "closed_trades": closed,
        "max_drawdown_usd": round(max_dd, 2),
        "max_daily_loss_usd": round(max_loss, 2),
        "max_consecutive_losses": max_consec,
    }


def _bump_count(counts: dict[str, int], key: Any, amount: int = 1) -> None:
    raw = str(key or "").strip() or "unknown"
    counts[raw] = counts.get(raw, 0) + int(amount)


def _dominant_count_key(counts: dict[str, int]) -> str | None:
    if not counts:
        return None
    return max(counts.items(), key=lambda item: (item[1], item[0]))[0]


def _top_count_items(counts: dict[str, int], limit: int = 5) -> list[dict[str, Any]]:
    return [
        {"reason": key, "count": value}
        for key, value in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[: max(1, int(limit))]
        if value > 0
    ]


def _compact_signal_snapshot(signal: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(signal, dict):
        return {}
    out: dict[str, Any] = {
        "action": signal.get("action"),
        "reason": signal.get("reason"),
        "score": signal.get("score"),
    }
    for key in ("rsi14", "fast_gap", "slow_gap"):
        value = signal.get(key)
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            out[key] = round(float(value), 4)
    reasons = signal.get("reasons")
    if isinstance(reasons, list):
        out["reasons"] = [str(x) for x in reasons[:6]]
    return out


def _stock_swing_adjustment_suggestions(
    *,
    strategy: dict[str, Any],
    risk: dict[str, Any],
    metrics: dict[str, Any],
) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    no_trade_counts = metrics.get("no_trade_reason_counts") if isinstance(metrics.get("no_trade_reason_counts"), dict) else {}
    signal_counts = metrics.get("signal_reason_counts") if isinstance(metrics.get("signal_reason_counts"), dict) else {}
    primary = str(metrics.get("primary_no_trade_reason") or "")
    closed = _safe_int(metrics.get("closed_trades"), 0)

    def add(code: str, title: str, reason: str, patch: dict[str, Any] | None = None, caution: str = "") -> None:
        if any(item.get("code") == code for item in suggestions):
            return
        row: dict[str, Any] = {"code": code, "title": title, "reason": reason}
        if patch:
            row["suggested_patch"] = patch
        if caution:
            row["caution"] = caution
        suggestions.append(row)

    if no_trade_counts.get("insufficient_daily_bars") or primary == "insufficient_daily_bars":
        add(
            "fetch_or_reduce_stock_pool",
            "先补股票池日线缓存",
            "有股票没有足够日线，验证无法判断趋势信号。",
            caution="优先点击补K线；若个别股票长期缺数据，再从股票池移除。",
        )

    if closed <= 0 and (primary == "no_entry_signal" or signal_counts.get("trend_score_low")):
        current_score = max(1, _safe_int(strategy.get("min_trend_score"), 3))
        if current_score > 2:
            suggested_score = 4 if current_score > 5 else current_score - 1
            add(
                "lower_min_trend_score",
                "放宽趋势分门槛",
                "多数股票没有达到入场趋势分，候选会长期无交易。",
                {"strategy": {"min_trend_score": suggested_score}},
                "降低趋势分会增加交易频率，也会引入更弱趋势的入场。",
            )
        else:
            add(
                "expand_stock_pool_or_wait",
                "扩大股票池或等待趋势形成",
                "趋势分已经较低，继续放宽会明显降低信号质量。",
                caution="建议先增加高流动性股票，或等待日线趋势更清晰。",
            )

    if closed <= 0 and signal_counts.get("below_slow_ma_threshold"):
        current_min = _safe_float(strategy.get("min_price_above_slow_ma_pct"), 0.0)
        patch = {"strategy": {"min_price_above_slow_ma_pct": max(0.0, round(current_min - 0.01, 4))}} if current_min > 0 else None
        add(
            "review_slow_ma_filter",
            "检查慢线过滤是否过严",
            "大量样本没有站稳慢线，说明当前市场或股票池不符合趋势买入 Call 条件。",
            patch,
            "如果价格在慢线下方，直接放宽可能变成逆势买入；更稳妥是等待或换成回调策略。",
        )

    if closed <= 0 and (signal_counts.get("extended_above_fast_ma") or signal_counts.get("pullback_wait_for_cooldown")):
        current_gap = _safe_float(strategy.get("max_price_above_fast_ma_pct"), 0.12)
        add(
            "relax_fast_ma_extension",
            "略放宽距快线偏离限制",
            "有些股票趋势足够强，但因为离快线太远被过滤。",
            {"strategy": {"max_price_above_fast_ma_pct": round(min(0.18, max(current_gap + 0.02, current_gap * 1.2)), 4)}},
            "放宽后可能追高，建议配合更严格止损或更小仓位。",
        )

    if closed <= 0 and signal_counts.get("breakout_not_confirmed"):
        add(
            "use_trend_or_pullback_variant",
            "突破策略信号不足时切回趋势/回调",
            "突破策略只在近期新高确认后入场，样本里没有形成足够突破。",
            {"strategy": {"strategy_variant": "swing_trend_call"}},
            "切换策略后应重新验证，不要直接写入实盘。",
        )

    if no_trade_counts.get("premium_over_budget") or primary == "premium_over_budget" or _safe_int(metrics.get("budget_blocks"), 0) > 0:
        current_otm = _safe_float(strategy.get("fallback_otm_pct"), 0.03)
        current_dte = max(21, _safe_int(strategy.get("target_dte"), 90))
        add(
            "reduce_option_cost",
            "降低单笔权利金占用",
            "有入场信号，但理论权利金超过单笔预算。",
            {
                "strategy": {
                    "fallback_otm_pct": round(min(0.12, current_otm + 0.01), 4),
                    "target_dte": max(45, current_dte - 15),
                }
            },
            "更远 OTM 和更短 DTE 会降低成本，但也会降低胜率或缩短容错时间。",
        )
        current_budget = _safe_float(risk.get("max_premium_per_order"), 800.0)
        add(
            "review_premium_budget",
            "确认单笔预算是否过低",
            "如果目标股票价格较高，Long Call 权利金可能天然高于当前预算。",
            {"risk": {"max_premium_per_order": round(min(current_budget * 1.25, current_budget + 300.0), 2)}},
            "提高预算会直接放大风险；更推荐后续加入 Call Debit Spread。",
        )

    if no_trade_counts.get("no_exit_before_window_end") or primary == "no_exit_before_window_end":
        current_exit = max(5, _safe_int(strategy.get("dte_exit_days"), 21))
        add(
            "tighten_time_exit",
            "缩短持有和退出窗口",
            "有入场但窗口内没有形成闭合交易，说明退出条件太慢或样本尾部未闭合。",
            {"strategy": {"dte_exit_days": min(current_exit + 7, 45)}},
            "中长线不宜过度频繁退出，调整后需要重新验证回撤和胜率。",
        )

    if closed <= 0 and not suggestions:
        add(
            "inspect_filters_manually",
            "人工检查趋势过滤组合",
            "本窗口没有闭合交易，但无法归因到单一过滤项。",
            caution="建议先查看逐股票诊断，再决定放宽趋势、预算或股票池。",
        )

    return suggestions[:6]


def _approx_stock_options_swing_backtest(
    candidate: dict[str, Any],
    *,
    root: Path,
    windows_days: list[int],
    data_quality: dict[str, Any],
) -> dict[str, Any]:
    live_config = data_quality.get("current_config") if isinstance(data_quality.get("current_config"), dict) else {}
    patch = candidate.get("strategy_config_patch") if isinstance(candidate.get("strategy_config_patch"), dict) else {}
    strategy = dict(live_config.get("strategy") if isinstance(live_config.get("strategy"), dict) else {})
    risk = dict(live_config.get("risk") if isinstance(live_config.get("risk"), dict) else {})
    if isinstance(patch.get("strategy"), dict):
        strategy.update(patch["strategy"])
    if isinstance(patch.get("risk"), dict):
        risk.update(patch["risk"])
    pool = _normalize_stock_pool(live_config)
    rows: list[dict[str, Any]] = []
    blockers: list[str] = []
    for days in windows_days:
        pnls: list[float] = []
        returns: list[float] = []
        symbols_checked = 0
        total_loaded_bars = 0
        max_loaded_bars = 0
        cache_paths: dict[str, str] = {}
        symbols_used: list[str] = []
        symbols_skipped: list[dict[str, Any]] = []
        symbols_no_trade: list[dict[str, Any]] = []
        trade_details: list[dict[str, Any]] = []
        no_trade_reason_counts: dict[str, int] = {}
        signal_reason_counts: dict[str, int] = {}
        entry_signals = 0
        accepted_entries = 0
        budget_blocks = 0
        open_without_exit = 0
        for symbol in pool:
            bars, cache_path = _load_cached_daily_bars(root, symbol, int(days))
            cache_paths[symbol] = cache_path
            min_bars = max(80, _safe_int(strategy.get("trend_slow_ma"), 50) + 20)
            if len(bars) < min_bars:
                _bump_count(no_trade_reason_counts, "insufficient_daily_bars")
                symbols_skipped.append(
                    {
                        "symbol": symbol,
                        "reason": "insufficient_daily_bars",
                        "bars": len(bars),
                        "required": min_bars,
                        "cache_path": cache_path,
                    }
                )
                continue
            symbols_checked += 1
            total_loaded_bars += len(bars)
            max_loaded_bars = max(max_loaded_bars, len(bars))
            symbols_used.append(symbol)
            closes = [float(b["close"]) for b in bars if float(b.get("close", 0.0)) > 0]
            symbol_signal_reason_counts: dict[str, int] = {}
            symbol_entry_signals = 0
            symbol_accepted_entries = 0
            symbol_budget_blocks = 0
            symbol_closed_trades = 0
            symbol_last_signal: dict[str, Any] = {}
            symbol_last_budget_block: dict[str, Any] = {}
            open_until_idx: int | None = None
            entry_price = 0.0
            entry_debit = 0.0
            entry_date: date | None = None
            warmup = max(60, _safe_int(strategy.get("trend_slow_ma"), 50))
            start_idx = max(warmup, len(bars) - int(days))
            for idx in range(start_idx, len(bars) - 1):
                current_closes = closes[: idx + 1]
                if open_until_idx is not None and idx <= open_until_idx:
                    continue
                signal = _trend_signal_from_closes(current_closes, strategy)
                symbol_last_signal = _compact_signal_snapshot(signal)
                if signal.get("action") != "candidate_long_call":
                    reason = str(signal.get("reason") or "no_entry_signal")
                    _bump_count(signal_reason_counts, reason)
                    _bump_count(symbol_signal_reason_counts, reason)
                    continue
                entry_signals += 1
                symbol_entry_signals += 1
                entry_spot = closes[idx]
                entry_price = entry_spot
                target_dte = max(21, _safe_int(strategy.get("target_dte"), 90))
                hold_days = max(5, min(target_dte, max(_safe_int(strategy.get("dte_exit_days"), 21), 45)))
                exit_idx = min(len(bars) - 1, idx + hold_days)
                strike = entry_spot * (1.0 + max(0.0, _safe_float(strategy.get("fallback_otm_pct"), 0.03)))
                iv = _hist_vol(current_closes, 20)
                structure_mode = str(strategy.get("mode") or "long_call").strip().lower()
                long_mid = _black_scholes_call(entry_spot, strike, target_dte, iv)
                long_entry_ask = _sim_option_buy_price(long_mid, strategy, target_dte)
                short_strike: float | None = None
                short_mid = 0.0
                short_entry_bid = 0.0
                spread_mid = 0.0
                entry_debit = long_entry_ask
                if structure_mode == "call_debit_spread":
                    width_pct = max(0.01, _safe_float(strategy.get("spread_width_pct"), 0.05))
                    short_strike = max(strike + 0.01, entry_spot * (1.0 + max(0.0, _safe_float(strategy.get("fallback_otm_pct"), 0.03)) + width_pct))
                    short_mid = _black_scholes_call(entry_spot, short_strike, target_dte, iv)
                    short_entry_bid = _sim_option_sell_price(short_mid, strategy, target_dte)
                    spread_mid = max(0.0, long_mid - short_mid)
                    entry_debit = _sim_spread_buy_price(spread_mid, strategy, target_dte)
                max_order_budget = _safe_float(risk.get("max_premium_per_order"), 800.0)
                budget_reason = "premium_over_budget"
                spread_width_value = max(0.0, (short_strike - strike) if short_strike is not None else 0.0)
                debit_to_width = (entry_debit / spread_width_value) if spread_width_value > 0 else None
                if structure_mode == "call_debit_spread":
                    spread_debit_limit = _safe_float(strategy.get("max_spread_debit"), 0.0)
                    if spread_debit_limit > 0:
                        max_order_budget = min(max_order_budget, spread_debit_limit)
                    max_debit_to_width = max(0.05, min(0.9, _safe_float(strategy.get("max_spread_debit_to_width_pct"), 0.45)))
                    if debit_to_width is None or debit_to_width > max_debit_to_width:
                        budget_reason = "spread_debit_too_high_vs_width"
                if entry_debit <= 0 or entry_debit * 100.0 > max_order_budget or budget_reason == "spread_debit_too_high_vs_width":
                    budget_blocks += 1
                    symbol_budget_blocks += 1
                    _bump_count(no_trade_reason_counts, budget_reason)
                    symbol_last_budget_block = {
                        "date": bars[idx]["date"].date().isoformat(),
                        "structure": structure_mode,
                        "reason": budget_reason,
                        "spot": round(entry_spot, 4),
                        "strike": round(strike, 4),
                        "short_strike": round(short_strike, 4) if short_strike is not None else None,
                        "spread_width": round(spread_width_value * 100.0, 2) if spread_width_value > 0 else None,
                        "debit_to_width_pct": round(debit_to_width * 100.0, 2) if debit_to_width is not None else None,
                        "max_debit_to_width_pct": round(_safe_float(strategy.get("max_spread_debit_to_width_pct"), 0.45) * 100.0, 2)
                        if structure_mode == "call_debit_spread"
                        else None,
                        "target_dte": target_dte,
                        "estimated_premium": round(entry_debit * 100.0, 2),
                        "long_mid": round(long_mid * 100.0, 2),
                        "short_mid": round(short_mid * 100.0, 2) if short_mid > 0 else None,
                        "spread_mid": round(spread_mid * 100.0, 2) if spread_mid > 0 else None,
                        "max_premium_per_order": round(max_order_budget, 2),
                    }
                    continue
                accepted_entries += 1
                symbol_accepted_entries += 1
                entry_date = bars[idx]["date"].date()
                stop_loss = -abs(_safe_float(strategy.get("stop_loss_pct"), 0.45))
                take_profit = abs(_safe_float(strategy.get("take_profit_pct"), 0.8))
                trend_exit_ma = _safe_int(strategy.get("trend_exit_below_ma"), 50)
                confirm = max(1, _safe_int(strategy.get("trend_exit_confirm_bars"), 2))
                min_stop_hold_days = (
                    max(0, _safe_int(strategy.get("spread_min_hold_days_before_stop"), 5))
                    if structure_mode == "call_debit_spread"
                    else 0
                )
                closed_this_entry = False
                entry_signal = _compact_signal_snapshot(signal)
                for j in range(idx + 1, exit_idx + 1):
                    remaining_dte = max(1, target_dte - (j - idx))
                    long_exit_mid = _black_scholes_call(closes[j], strike, remaining_dte, iv)
                    long_exit_bid = _sim_option_sell_price(long_exit_mid, strategy, remaining_dte)
                    short_exit_mid = 0.0
                    short_exit_ask = 0.0
                    if structure_mode == "call_debit_spread" and short_strike is not None:
                        short_exit_mid = _black_scholes_call(closes[j], short_strike, remaining_dte, iv)
                        short_exit_ask = _sim_option_buy_price(short_exit_mid, strategy, remaining_dte)
                        exit_spread_mid = max(0.0, long_exit_mid - short_exit_mid)
                        px = _sim_spread_sell_price(exit_spread_mid, strategy, remaining_dte)
                    else:
                        exit_spread_mid = 0.0
                        px = long_exit_bid
                    ret = (px - entry_debit) / entry_debit if entry_debit > 0 else 0.0
                    should_exit = False
                    exit_reason = ""
                    if not should_exit and trend_exit_ma > 0 and j + 1 >= trend_exit_ma + confirm:
                        recent = closes[: j + 1]
                        broken = True
                        for off in range(confirm):
                            end = len(recent) - off
                            ma = _sma(recent[:end], trend_exit_ma)
                            if ma is None or recent[end - 1] >= ma:
                                broken = False
                                break
                        if broken:
                            should_exit = True
                            exit_reason = "trend_exit"
                    if ret >= take_profit:
                        should_exit = True
                        exit_reason = "take_profit"
                    elif not should_exit and ret <= stop_loss and (j - idx) >= min_stop_hold_days:
                        should_exit = True
                        exit_reason = "stop_loss"
                    elif not should_exit and j == exit_idx:
                        should_exit = True
                        exit_reason = "time_exit"
                    if should_exit:
                        pnl = (px - entry_debit) * 100.0
                        pnls.append(round(pnl, 2))
                        returns.append(ret)
                        open_until_idx = j
                        symbol_closed_trades += 1
                        closed_this_entry = True
                        trade_details.append(
                            {
                                "symbol": symbol,
                                "entry_date": entry_date.isoformat() if entry_date else bars[idx]["date"].date().isoformat(),
                                "exit_date": bars[j]["date"].date().isoformat(),
                                "hold_days": j - idx,
                                "entry_spot": round(entry_spot, 4),
                                "exit_spot": round(closes[j], 4),
                                "strike": round(strike, 4),
                                "short_strike": round(short_strike, 4) if short_strike is not None else None,
                                "target_dte": target_dte,
                                "remaining_dte": remaining_dte,
                                "structure": structure_mode,
                                "estimated_entry_premium": round(entry_debit * 100.0, 2),
                                "estimated_exit_value": round(px * 100.0, 2),
                                "long_entry_mid": round(long_mid * 100.0, 2),
                                "short_entry_mid": round(short_mid * 100.0, 2) if short_mid > 0 else None,
                                "spread_entry_mid": round(spread_mid * 100.0, 2) if spread_mid > 0 else None,
                                "spread_exit_mid": round(exit_spread_mid * 100.0, 2) if exit_spread_mid > 0 else None,
                                "spread_width": round(spread_width_value * 100.0, 2) if spread_width_value > 0 else None,
                                "debit_to_width_pct": round(debit_to_width * 100.0, 2) if debit_to_width is not None else None,
                                "min_stop_hold_days": min_stop_hold_days if structure_mode == "call_debit_spread" else None,
                                "long_exit_mid": round(long_exit_mid * 100.0, 2),
                                "short_exit_mid": round(short_exit_mid * 100.0, 2) if short_exit_mid > 0 else None,
                                "pnl_usd": round(pnl, 2),
                                "return_pct": round(ret * 100.0, 2),
                                "exit_reason": exit_reason or "unknown",
                                "entry_signal": entry_signal,
                            }
                        )
                        break
                if not closed_this_entry:
                    open_without_exit += 1
            if symbol_closed_trades <= 0:
                primary_signal_reason = _dominant_count_key(symbol_signal_reason_counts)
                if symbol_entry_signals <= 0:
                    reason = "no_entry_signal"
                elif symbol_accepted_entries <= 0 and symbol_budget_blocks > 0:
                    reason = str(symbol_last_budget_block.get("reason") or "premium_over_budget")
                elif symbol_accepted_entries > 0:
                    reason = "no_exit_before_window_end"
                else:
                    reason = "no_accepted_entry"
                _bump_count(no_trade_reason_counts, reason)
                symbols_no_trade.append(
                    {
                        "symbol": symbol,
                        "reason": reason,
                        "primary_signal_reason": primary_signal_reason,
                        "signal_reason_counts": symbol_signal_reason_counts,
                        "last_signal": symbol_last_signal,
                        "last_budget_block": symbol_last_budget_block,
                        "entry_signals": symbol_entry_signals,
                        "accepted_entries": symbol_accepted_entries,
                        "budget_blocks": symbol_budget_blocks,
                    }
                )
        metrics = _compute_trade_stats(pnls, returns)
        coverage_pct = round(symbols_checked / max(1, len(pool)) * 100.0, 2)
        primary_no_trade_reason = ""
        primary_signal_reason = _dominant_count_key(signal_reason_counts)
        if _safe_int(metrics.get("closed_trades"), 0) <= 0:
            if symbols_checked <= 0:
                primary_no_trade_reason = "insufficient_daily_bars"
            elif entry_signals <= 0:
                primary_no_trade_reason = "no_entry_signal"
            elif accepted_entries <= 0 and budget_blocks > 0:
                primary_no_trade_reason = _dominant_count_key(no_trade_reason_counts) or "premium_over_budget"
            elif open_without_exit > 0:
                primary_no_trade_reason = "no_exit_before_window_end"
            else:
                primary_no_trade_reason = _dominant_count_key(no_trade_reason_counts) or "no_closed_trades"
        diagnostics = {
            "primary_no_trade_reason": primary_no_trade_reason,
            "primary_signal_reason": primary_signal_reason,
            "no_trade_reasons": _top_count_items(no_trade_reason_counts),
            "signal_reasons": _top_count_items(signal_reason_counts),
            "closed_trades": trade_details[:25],
            "symbols_insufficient_bars": symbols_skipped[:25],
            "symbols_no_trade": symbols_no_trade[:25],
        }
        suggestions = _stock_swing_adjustment_suggestions(strategy=strategy, risk=risk, metrics={**metrics, **diagnostics, "no_trade_reason_counts": no_trade_reason_counts, "signal_reason_counts": signal_reason_counts, "budget_blocks": budget_blocks})
        metrics.update(
            {
                "mode": "approx_option_path",
                "model": "daily_trend_signal_black_scholes_proxy_with_spread_slippage",
                "historical_underlying_data": "real_daily_kline_cache",
                "historical_underlying_coverage_pct": coverage_pct,
                "historical_underlying_total_bars": total_loaded_bars,
                "historical_underlying_max_bars_per_symbol": max_loaded_bars,
                "option_price_history": "not_available_proxy_pricing",
                "option_pricing_model": "black_scholes_proxy_with_bid_ask_spread_and_slippage",
                "validation_confidence": "rough_proxy",
                "symbols_checked": symbols_checked,
                "symbols_requested": len(pool),
                "symbols_used": symbols_used,
                "symbols_skipped": symbols_skipped,
                "symbols_no_trade": symbols_no_trade,
                "trade_details": trade_details,
                "cache_paths": cache_paths,
                "entry_signals": entry_signals,
                "accepted_entries": accepted_entries,
                "budget_blocks": budget_blocks,
                "open_without_exit": open_without_exit,
                "no_trade_reason_counts": no_trade_reason_counts,
                "signal_reason_counts": signal_reason_counts,
                "primary_no_trade_reason": primary_no_trade_reason,
                "primary_signal_reason": primary_signal_reason,
                "diagnostics": diagnostics,
                "suggested_adjustments": suggestions,
                "note": "粗略验证：用股票日线触发信号，并用历史波动率近似 IV 的 Black-Scholes Call/Call Debit Spread 路径估算期权盈亏，已加入买卖价差和滑点；不是真实期权历史成交回测。",
            }
        )
        rows.append({"days": int(days), "ok": True, "metrics": metrics})
        if symbols_checked <= 0:
            blockers.append(f"{days}d_no_daily_bars_for_stock_pool")
        elif _safe_int(metrics.get("closed_trades"), 0) <= 0:
            reason = primary_no_trade_reason or "no_closed_trades"
            blockers.append(f"{days}d_{reason}")
    ok_rows = [r for r in rows if r.get("ok") and isinstance(r.get("metrics"), dict)]
    avg_values = [
        _safe_float(r["metrics"].get("return_pct"), float("nan"))
        for r in ok_rows
        if r["metrics"].get("return_pct") is not None
    ]
    avg_values = [x for x in avg_values if math.isfinite(x)]
    min_closed = min((_safe_int(r["metrics"].get("closed_trades"), 0) for r in ok_rows), default=0)
    total_closed = sum((_safe_int(r["metrics"].get("closed_trades"), 0) for r in ok_rows))
    no_trade_windows = [
        _safe_int(r.get("days"), 0)
        for r in ok_rows
        if _safe_int(r["metrics"].get("closed_trades"), 0) <= 0
    ]
    worst_drawdown = min((_safe_float(r["metrics"].get("max_drawdown_usd"), 0.0) for r in ok_rows), default=0.0)
    worst_loss = min((_safe_float(r["metrics"].get("max_daily_loss_usd"), 0.0) for r in ok_rows), default=0.0)
    max_consec = max((_safe_int(r["metrics"].get("max_consecutive_losses"), 0) for r in ok_rows), default=0)
    avg_return = round(mean(avg_values), 4) if avg_values else None
    requested_symbols = max(1, len(pool))
    checked_windows = [max(0, _safe_int(r["metrics"].get("symbols_checked"), 0)) for r in ok_rows]
    avg_coverage = round(mean([x / requested_symbols * 100.0 for x in checked_windows]), 2) if checked_windows else 0.0
    aggregate_no_trade_counts: dict[str, int] = {}
    aggregate_signal_counts: dict[str, int] = {}
    aggregate_suggestions: list[dict[str, Any]] = []
    aggregate_suggestion_codes: set[str] = set()
    for row in ok_rows:
        metrics = row["metrics"]
        counts = metrics.get("no_trade_reason_counts")
        if isinstance(counts, dict):
            for key, value in counts.items():
                _bump_count(aggregate_no_trade_counts, key, _safe_int(value, 0))
        signal_counts = metrics.get("signal_reason_counts")
        if isinstance(signal_counts, dict):
            for key, value in signal_counts.items():
                _bump_count(aggregate_signal_counts, key, _safe_int(value, 0))
        suggestions = metrics.get("suggested_adjustments")
        if isinstance(suggestions, list):
            for item in suggestions:
                if not isinstance(item, dict):
                    continue
                code = str(item.get("code") or item.get("title") or "")
                if not code or code in aggregate_suggestion_codes:
                    continue
                aggregate_suggestion_codes.add(code)
                aggregate_suggestions.append(item)
    primary_no_trade_summary = _dominant_count_key(aggregate_no_trade_counts)
    confidence_level = "low"
    if avg_coverage >= 80.0 and total_closed >= max(3, len(ok_rows)):
        confidence_level = "medium"
    if avg_coverage >= 90.0 and total_closed >= 12 and min_closed >= 2:
        confidence_level = "medium_high"
    if min_closed <= 0:
        blockers.append("approx_no_closed_trades_in_at_least_one_window")
    if avg_return is None:
        blockers.append("approx_return_unavailable")
    elif avg_return <= 0:
        blockers.append("approx_non_positive_average_return_pct")
    if max_consec > 8:
        blockers.append("approx_too_many_consecutive_losses")
    return {
        "passed": not blockers,
        "instance": "stock_options_swing",
        "windows_days": list(windows_days),
        "summary": {
            "mode": "approx_option_backtest",
            "avg_return_pct": avg_return,
            "min_closed_trades": min_closed,
            "total_closed_trades": total_closed,
            "no_trade_windows_days": no_trade_windows,
            "worst_drawdown_usd": worst_drawdown,
            "worst_daily_loss_usd": worst_loss,
            "max_consecutive_losses": max_consec,
            "model": "daily_trend_signal_black_scholes_proxy_with_spread_slippage",
            "historical_validation": {
                "underlying_history": "real_daily_kline_cache",
                "underlying_coverage_pct": avg_coverage,
                "option_price_history": "not_available",
                "option_pricing": "black_scholes_proxy_with_bid_ask_spread_and_slippage",
                "confidence_level": confidence_level,
                "note": "Uses real cached stock daily bars for signals and path, but option prices are theoretical proxies until historical option chains are available.",
            },
            "no_trade_reason_counts": aggregate_no_trade_counts,
            "signal_reason_counts": aggregate_signal_counts,
            "primary_no_trade_reason": primary_no_trade_summary,
            "suggested_adjustments": aggregate_suggestions[:6],
        },
        "rows": rows,
        "blockers": sorted(set(blockers)),
        "gate": {
            "research_only": True,
            "model": "approx_option_path",
            "underlying_history": "real_daily_kline_cache",
            "option_price_history": "not_available",
            "validation_confidence": confidence_level,
            "approval_allowed_with_force_only": True,
            "not_real_option_history": True,
        },
    }


def validate_candidate(
    candidate: dict[str, Any],
    *,
    instance: str,
    windows_days: list[int],
    kline: str,
    use_server_kline_cache: bool,
    rth_only: bool,
    backtest_runner: BacktestRunner | None = None,
    root: Path | None = None,
    data_quality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    inst = normalize_instance(instance)
    if inst == "stock_options_swing":
        return _approx_stock_options_swing_backtest(
            candidate,
            root=root or default_root(),
            windows_days=windows_days,
            data_quality=data_quality if isinstance(data_quality, dict) else {},
        )
    runner = backtest_runner or _default_backtest_runner
    strategy_config = candidate.get("strategy_config") if isinstance(candidate.get("strategy_config"), dict) else {}
    symbol = str(strategy_config.get("symbol") or "QQQ.US").strip().upper() or "QQQ.US"
    rows: list[dict[str, Any]] = []
    blockers: list[str] = []
    for days in windows_days:
        request = {
            "symbol": symbol,
            "days": int(days),
            "periods": 0,
            "kline": kline,
            "use_server_kline_cache": bool(use_server_kline_cache),
            "rth_only": bool(rth_only),
            "strategy_config": strategy_config,
            "save_snapshot": False,
        }
        try:
            raw = runner(request)
            metrics = _metrics_from_backtest(raw if isinstance(raw, dict) else {})
            rows.append({"days": int(days), "ok": True, "request": request, "metrics": metrics})
        except Exception as exc:
            blockers.append(f"{days}d_backtest_failed:{exc}")
            rows.append({"days": int(days), "ok": False, "request": request, "error": str(exc)})

    ok_rows = [r for r in rows if r.get("ok") and isinstance(r.get("metrics"), dict)]
    avg_return_values = [
        _safe_float(r["metrics"].get("return_pct"), float("nan"))
        for r in ok_rows
        if r["metrics"].get("return_pct") is not None
    ]
    avg_return_values = [x for x in avg_return_values if math.isfinite(x)]
    min_closed = min((_safe_int(r["metrics"].get("closed_trades"), 0) for r in ok_rows), default=0)
    total_closed = sum((_safe_int(r["metrics"].get("closed_trades"), 0) for r in ok_rows))
    no_trade_windows = [
        _safe_int(r.get("days"), 0)
        for r in ok_rows
        if _safe_int(r["metrics"].get("closed_trades"), 0) <= 0
    ]
    worst_drawdown = min((_safe_float(r["metrics"].get("max_drawdown_usd"), 0.0) for r in ok_rows), default=0.0)
    worst_daily_loss = min((_safe_float(r["metrics"].get("max_daily_loss_usd"), 0.0) for r in ok_rows), default=0.0)
    max_consec = max((_safe_int(r["metrics"].get("max_consecutive_losses"), 0) for r in ok_rows), default=0)
    avg_return = round(mean(avg_return_values), 4) if avg_return_values else None
    if len(ok_rows) != len(windows_days):
        blockers.append("not_all_validation_windows_completed")
    if min_closed <= 0:
        blockers.append("no_closed_trades_in_at_least_one_window")
    if avg_return is None or avg_return <= 0:
        blockers.append("non_positive_average_return_pct")
    if max_consec > 5:
        blockers.append("too_many_consecutive_losses")
    if worst_daily_loss < -500:
        blockers.append("max_daily_loss_breaches_default_gate")

    return {
        "passed": not blockers,
        "instance": inst,
        "windows_days": list(windows_days),
        "summary": {
            "avg_return_pct": avg_return,
            "min_closed_trades": min_closed,
            "total_closed_trades": total_closed,
            "no_trade_windows_days": no_trade_windows,
            "worst_drawdown_usd": worst_drawdown,
            "worst_daily_loss_usd": worst_daily_loss,
            "max_consecutive_losses": max_consec,
        },
        "rows": rows,
        "blockers": blockers,
        "gate": {
            "requires_all_windows": True,
            "min_closed_trades_per_window": 1,
            "avg_return_pct_gt": 0,
            "max_consecutive_losses_lte": 5,
            "max_daily_loss_usd_gte": -500,
        },
    }


def _load_runs(root: Path) -> list[dict[str, Any]]:
    data = _read_json(_runs_path(root), {}) or {}
    rows = data.get("runs", []) if isinstance(data, dict) else []
    if not isinstance(rows, list):
        return []
    return [x for x in rows if isinstance(x, dict)]


def _save_runs(root: Path, runs: list[dict[str, Any]]) -> None:
    rows = sorted(runs, key=lambda x: str(x.get("created_at") or ""))[-200:]
    _write_json_atomic(_runs_path(root), {"schema": f"{LAB_SCHEMA_VERSION}.runs", "runs": rows})


def create_lab_run(
    body: dict[str, Any] | None = None,
    *,
    root: Path | None = None,
    backtest_runner: BacktestRunner | None = None,
    progress_callback: Callable[[int, str, str], None] | None = None,
) -> dict[str, Any]:
    root = root or default_root()
    payload = body if isinstance(body, dict) else {}
    inst = normalize_instance(payload.get("instance"))
    strategy_variant = normalize_lab_strategy_variant(payload.get("strategy_variant"))
    candidate_generator = normalize_candidate_generator(payload.get("candidate_generator"))
    research_dimension = normalize_research_dimension(payload.get("research_dimension"))
    windows = _normalize_validation_windows(payload.get("validation_windows_days"))
    max_candidates = max(1, min(10, _safe_int(payload.get("max_candidates"), 3)))
    kline = str(payload.get("kline") or "1m")
    if progress_callback:
        progress_callback(8, "data", "读取配置、决策日志和执行 ledger")
    data_quality = build_data_quality_report(root, inst, tail_limit=_safe_int(payload.get("tail_limit"), 200) or 200)
    strategy_variant = normalize_strategy_variant_for_instance(inst, payload.get("strategy_variant"), strategy_variant)
    if progress_callback:
        progress_callback(18, "candidate_generation", "生成候选参数")
    if inst == "stock_options_swing":
        candidates = generate_stock_options_swing_candidates(
            data_quality,
            max_candidates=max_candidates,
            candidate_generator=candidate_generator,
            research_dimension=research_dimension,
            strategy_variant=strategy_variant,
        )
    else:
        candidates = generate_candidate_parameters(
            data_quality,
            max_candidates=max_candidates,
            strategy_variant=strategy_variant,
            candidate_generator=candidate_generator,
            research_dimension=research_dimension,
        )
    total_candidates = max(1, len(candidates))
    for idx, candidate in enumerate(candidates):
        if progress_callback:
            progress_callback(
                22 + int(idx / total_candidates * 68),
                "validation",
                f"验证候选 {idx + 1}/{total_candidates}: {candidate.get('title') or candidate.get('candidate_id')}",
            )
        candidate["validation"] = validate_candidate(
            candidate,
            instance=inst,
            windows_days=windows,
            kline=kline,
            use_server_kline_cache=bool(payload.get("use_server_kline_cache", True)),
            rth_only=bool(payload.get("rth_only", True)),
            backtest_runner=backtest_runner,
            root=root,
            data_quality=data_quality,
        )
        if not data_quality.get("ok"):
            candidate["validation"]["passed"] = False
            candidate["validation"].setdefault("blockers", []).append("data_quality_error")
    if progress_callback:
        progress_callback(94, "finalizing", "写入 Lab 运行结果")

    run = {
        "schema": LAB_SCHEMA_VERSION,
        "run_id": f"asl_{uuid4().hex[:16]}",
        "created_at": _now_iso(),
        "instance": inst,
        "status": "completed",
        "pipeline": [
            {"stage": "data", "label": "数据层", "status": "completed"},
            {
                "stage": "agent_research",
                "label": "智能体研究层",
                "status": "completed",
                "mode": "tradingagents_adapter" if candidate_generator == "tradingagents" else "deterministic_mvp",
            },
            {"stage": "parameter_generation", "label": "参数生成层", "status": "completed"},
            {"stage": "validation", "label": "确定性回测层", "status": "completed"},
            {"stage": "approval_gate", "label": "人工确认 / 自动审批闸门", "status": "waiting_for_human"},
            {"stage": "live_worker", "label": "QQQ 实盘 worker", "status": "not_touched"},
        ],
        "request": {
            "instance": inst,
            "strategy_variant": strategy_variant,
            "candidate_generator": candidate_generator,
            "research_dimension": research_dimension,
            "validation_windows_days": windows,
            "max_candidates": max_candidates,
            "kline": kline,
            "use_server_kline_cache": bool(payload.get("use_server_kline_cache", True)),
            "rth_only": bool(payload.get("rth_only", True)),
        },
        "data_quality": data_quality,
        "candidates": candidates,
        "disclaimer": "Agent Strategy Lab 只生成候选参数、运行确定性验证并等待审批；不会直接下单。",
    }
    runs = _load_runs(root)
    runs.append(run)
    _save_runs(root, runs)
    if progress_callback:
        progress_callback(100, "done", "Lab 运行完成")
    return {"ok": True, "run": run}


def list_lab_runs(root: Path | None = None, instance: str | None = None, limit: int = 20) -> dict[str, Any]:
    root = root or default_root()
    rows = _load_runs(root)
    if instance:
        inst = normalize_instance(instance)
        rows = [x for x in rows if str(x.get("instance") or "") == inst]
    rows.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    lim = max(1, min(100, int(limit)))
    return {"ok": True, "items": rows[:lim], "returned": min(len(rows), lim)}


def get_lab_run(run_id: str, root: Path | None = None) -> dict[str, Any]:
    root = root or default_root()
    rid = str(run_id or "").strip()
    for run in _load_runs(root):
        if str(run.get("run_id") or "") == rid:
            return {"ok": True, "run": run}
    raise AgentStrategyLabError("run_not_found")


def _run_lab_task(task_id: str, body: dict[str, Any], root: Path | None = None) -> None:
    with _TASK_LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return
        task["status"] = "running"
        task["started_at"] = _now_iso()
        task["updated_at"] = task["started_at"]
    try:
        out = create_lab_run(
            body,
            root=root,
            progress_callback=lambda pct, stage, text: _set_task_progress(task_id, pct=pct, stage=stage, text=text),
        )
        run = out.get("run") if isinstance(out, dict) else None
        with _TASK_LOCK:
            task = _TASKS.get(task_id)
            if task is not None:
                task["status"] = "completed"
                task["completed_at"] = _now_iso()
                task["updated_at"] = task["completed_at"]
                task["progress_pct"] = 100
                task["progress_stage"] = "done"
                task["progress_text"] = "Lab 运行完成"
                task["run_id"] = run.get("run_id") if isinstance(run, dict) else None
                task["run"] = run
    except Exception as exc:
        with _TASK_LOCK:
            task = _TASKS.get(task_id)
            if task is not None:
                task["status"] = "failed"
                task["completed_at"] = _now_iso()
                task["updated_at"] = task["completed_at"]
                task["progress_stage"] = "failed"
                task["progress_text"] = "Lab 运行失败"
                task["error"] = str(exc)


def revalidate_candidate_run(
    source_run_id: str,
    candidate_id: str,
    body: dict[str, Any] | None = None,
    *,
    root: Path | None = None,
    backtest_runner: BacktestRunner | None = None,
    progress_callback: Callable[[int, str, str], None] | None = None,
) -> dict[str, Any]:
    root = root or default_root()
    payload = body if isinstance(body, dict) else {}
    source = get_lab_run(source_run_id, root=root).get("run")
    if not isinstance(source, dict):
        raise AgentStrategyLabError("run_not_found")
    candidate = _find_candidate(source, candidate_id)
    inst = normalize_instance(payload.get("instance") or source.get("instance"))
    request = source.get("request") if isinstance(source.get("request"), dict) else {}
    windows = _normalize_validation_windows(payload.get("validation_windows_days") or request.get("validation_windows_days"))
    kline = str(payload.get("kline") or request.get("kline") or "1m")
    use_server_kline_cache = bool(payload.get("use_server_kline_cache", request.get("use_server_kline_cache", True)))
    rth_only = bool(payload.get("rth_only", request.get("rth_only", True)))
    if progress_callback:
        progress_callback(8, "data", "Read data quality snapshot")
    data_quality = build_data_quality_report(root, inst, tail_limit=_safe_int(payload.get("tail_limit"), 200) or 200)
    new_candidate = {
        **candidate,
        "candidate_id": f"recheck_{candidate.get('candidate_id') or candidate_id}",
        "source_run_id": str(source.get("run_id") or source_run_id),
        "source_candidate_id": str(candidate.get("candidate_id") or candidate_id),
        "revalidated_at": _now_iso(),
    }
    if progress_callback:
        progress_callback(25, "validation", f"Revalidate candidate: {candidate.get('title') or candidate_id}")
    new_candidate["validation"] = validate_candidate(
        new_candidate,
        instance=inst,
        windows_days=windows,
        kline=kline,
        use_server_kline_cache=use_server_kline_cache,
        rth_only=rth_only,
        backtest_runner=backtest_runner,
        root=root,
        data_quality=data_quality,
    )
    if not data_quality.get("ok"):
        new_candidate["validation"]["passed"] = False
        new_candidate["validation"].setdefault("blockers", []).append("data_quality_error")
    if progress_callback:
        progress_callback(92, "finalizing", "Write revalidation result")
    strategy_config = new_candidate.get("strategy_config") if isinstance(new_candidate.get("strategy_config"), dict) else {}
    run = {
        "schema": LAB_SCHEMA_VERSION,
        "run_id": f"asl_{uuid4().hex[:16]}",
        "created_at": _now_iso(),
        "instance": inst,
        "status": "completed",
        "source_run_id": str(source.get("run_id") or source_run_id),
        "source_candidate_id": str(candidate.get("candidate_id") or candidate_id),
        "pipeline": [
            {"stage": "data", "label": "data", "status": "completed"},
            {"stage": "agent_research", "label": "agent_research", "status": "not_rerun", "mode": "historical_candidate_revalidation"},
            {"stage": "parameter_generation", "label": "parameter_generation", "status": "reused_historical_candidate"},
            {"stage": "validation", "label": "validation", "status": "completed"},
            {"stage": "approval_gate", "label": "approval_gate", "status": "waiting_for_human"},
            {"stage": "live_worker", "label": "live_worker", "status": "not_touched"},
        ],
        "request": {
            "instance": inst,
            "strategy_variant": normalize_strategy_variant_for_instance(inst, strategy_config.get("strategy_variant")),
            "candidate_generator": str(candidate.get("generator") or request.get("candidate_generator") or "deterministic"),
            "research_dimension": str(candidate.get("research_dimension") or request.get("research_dimension") or "risk_controls"),
            "validation_windows_days": windows,
            "max_candidates": 1,
            "kline": kline,
            "use_server_kline_cache": use_server_kline_cache,
            "rth_only": rth_only,
            "mode": "historical_candidate_revalidation",
            "source_run_id": str(source.get("run_id") or source_run_id),
            "source_candidate_id": str(candidate.get("candidate_id") or candidate_id),
        },
        "data_quality": data_quality,
        "candidates": [new_candidate],
        "disclaimer": "Historical candidate revalidation reuses the original candidate parameters and only reruns backtests. It never starts workers or places orders.",
    }
    runs = _load_runs(root)
    runs.append(run)
    _save_runs(root, runs)
    if progress_callback:
        progress_callback(100, "done", "Candidate revalidation completed")
    return {"ok": True, "run": run}


def _run_revalidate_candidate_task(
    task_id: str,
    source_run_id: str,
    candidate_id: str,
    body: dict[str, Any],
    root: Path | None = None,
) -> None:
    with _TASK_LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return
        task["status"] = "running"
        task["started_at"] = _now_iso()
        task["updated_at"] = task["started_at"]
    try:
        out = revalidate_candidate_run(
            source_run_id,
            candidate_id,
            body,
            root=root,
            progress_callback=lambda pct, stage, text: _set_task_progress(task_id, pct=pct, stage=stage, text=text),
        )
        run = out.get("run") if isinstance(out, dict) else None
        with _TASK_LOCK:
            task = _TASKS.get(task_id)
            if task is not None:
                task["status"] = "completed"
                task["completed_at"] = _now_iso()
                task["updated_at"] = task["completed_at"]
                task["progress_pct"] = 100
                task["progress_stage"] = "done"
                task["progress_text"] = "Candidate revalidation completed"
                task["run_id"] = run.get("run_id") if isinstance(run, dict) else None
                task["run"] = run
    except Exception as exc:
        with _TASK_LOCK:
            task = _TASKS.get(task_id)
            if task is not None:
                task["status"] = "failed"
                task["completed_at"] = _now_iso()
                task["updated_at"] = task["completed_at"]
                task["progress_stage"] = "failed"
                task["progress_text"] = "Candidate revalidation failed"
                task["error"] = str(exc)


def create_revalidate_candidate_task(
    source_run_id: str,
    candidate_id: str,
    body: dict[str, Any] | None = None,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    payload = body if isinstance(body, dict) else {}
    source = get_lab_run(source_run_id, root=root).get("run")
    if not isinstance(source, dict):
        raise AgentStrategyLabError("run_not_found")
    _find_candidate(source, candidate_id)
    inst = normalize_instance(payload.get("instance") or source.get("instance"))
    task_id = f"aslt_{uuid4().hex[:16]}"
    now = _now_iso()
    task = {
        "task_id": task_id,
        "status": "queued",
        "created_at": now,
        "updated_at": now,
        "instance": inst,
        "request": {
            **payload,
            "instance": inst,
            "source_run_id": str(source_run_id),
            "candidate_id": str(candidate_id),
            "mode": "historical_candidate_revalidation",
        },
        "progress_pct": 0,
        "progress_stage": "queued",
        "progress_text": "Waiting to revalidate candidate",
        "events": [{"ts": now, "stage": "queued", "pct": 0, "text": "Candidate revalidation task created"}],
    }
    with _TASK_LOCK:
        _TASKS[task_id] = task
        _TASK_FUTURES[task_id] = _TASK_EXECUTOR.submit(
            _run_revalidate_candidate_task,
            task_id,
            str(source_run_id),
            str(candidate_id),
            dict(task["request"]),
            root,
        )
    return {"ok": True, "async_run": True, "task": _task_view(task)}


def create_lab_task(body: dict[str, Any] | None = None, *, root: Path | None = None) -> dict[str, Any]:
    payload = body if isinstance(body, dict) else {}
    inst = normalize_instance(payload.get("instance"))
    task_id = f"aslt_{uuid4().hex[:16]}"
    now = _now_iso()
    task = {
        "task_id": task_id,
        "status": "queued",
        "created_at": now,
        "updated_at": now,
        "instance": inst,
        "request": {**payload, "instance": inst},
        "progress_pct": 0,
        "progress_stage": "queued",
        "progress_text": "等待 Lab 后台任务执行",
        "events": [{"ts": now, "stage": "queued", "pct": 0, "text": "任务已创建"}],
    }
    with _TASK_LOCK:
        _TASKS[task_id] = task
        stale_cutoff = time.time() - 24 * 60 * 60
        for tid, row in list(_TASKS.items()):
            created = _parse_dt(row.get("created_at"))
            if created and created.timestamp() < stale_cutoff:
                _TASKS.pop(tid, None)
                _TASK_FUTURES.pop(tid, None)
        _TASK_FUTURES[task_id] = _TASK_EXECUTOR.submit(_run_lab_task, task_id, dict(task["request"]), root)
    return {"ok": True, "async_run": True, "task": _task_view(task)}


def get_lab_task(task_id: str) -> dict[str, Any]:
    tid = str(task_id or "").strip()
    with _TASK_LOCK:
        task = _TASKS.get(tid)
        if not task:
            raise AgentStrategyLabError("task_not_found")
        return {"ok": True, "task": _task_view(task)}


def list_lab_tasks(instance: str | None = None, limit: int = 20) -> dict[str, Any]:
    inst = normalize_instance(instance) if instance else None
    with _TASK_LOCK:
        rows = list(_TASKS.values())
    if inst:
        rows = [x for x in rows if str(x.get("instance") or "") == inst]
    rows.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    lim = max(1, min(100, int(limit)))
    return {"ok": True, "items": [_task_view(x) for x in rows[:lim]], "returned": min(len(rows), lim)}


def _find_candidate(run: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    cid = str(candidate_id or "").strip()
    for candidate in run.get("candidates") or []:
        if isinstance(candidate, dict) and str(candidate.get("candidate_id") or "") == cid:
            return candidate
    raise AgentStrategyLabError("candidate_not_found")


def approve_candidate(
    run_id: str,
    candidate_id: str,
    *,
    root: Path | None = None,
    force: bool = False,
    approved_by: str | None = None,
) -> dict[str, Any]:
    root = root or default_root()
    runs = _load_runs(root)
    rid = str(run_id or "").strip()
    run: dict[str, Any] | None = None
    for row in runs:
        if str(row.get("run_id") or "") == rid:
            run = row
            break
    if run is None:
        raise AgentStrategyLabError("run_not_found")
    candidate = _find_candidate(run, candidate_id)
    validation = candidate.get("validation") if isinstance(candidate.get("validation"), dict) else {}
    if not force and not bool(validation.get("passed")):
        raise AgentStrategyLabError("candidate_validation_not_passed")

    inst = normalize_instance(run.get("instance"))
    subdir = _data_dir(root, inst)
    cfg_path = subdir / "live_worker_config.json"
    live_config = _read_json(cfg_path, {}) or {}
    if not isinstance(live_config, dict):
        live_config = {}
    current_sc = live_config.get("strategy_config") if isinstance(live_config.get("strategy_config"), dict) else {}
    candidate_patch = candidate.get("strategy_config_patch") if isinstance(candidate.get("strategy_config_patch"), dict) else {}
    if not candidate_patch:
        req_variant = run.get("request", {}).get("strategy_variant") if isinstance(run.get("request"), dict) else None
        if normalize_instance(run.get("instance")) == "stock_options_swing":
            candidate_patch = {"strategy": {"strategy_variant": normalize_swing_strategy_variant(req_variant)}}
        else:
            candidate_patch = {"strategy_variant": normalize_lab_strategy_variant(req_variant)}
    if inst == "stock_options_swing":
        previous_config = dict(live_config)
        next_config = json.loads(json.dumps(live_config, ensure_ascii=False, default=str))
        strategy_patch = candidate_patch.get("strategy") if isinstance(candidate_patch.get("strategy"), dict) else {}
        risk_patch = candidate_patch.get("risk") if isinstance(candidate_patch.get("risk"), dict) else {}
        strategy_before = next_config.get("strategy") if isinstance(next_config.get("strategy"), dict) else {}
        risk_before = next_config.get("risk") if isinstance(next_config.get("risk"), dict) else {}
        next_config["strategy"] = {**strategy_before, **strategy_patch}
        next_config["risk"] = {**risk_before, **risk_patch}
        diff = build_patch_diff(previous_config, {"strategy": strategy_patch, "risk": risk_patch})
        _write_json_atomic(cfg_path, next_config)
        approval = {
            "schema": f"{LAB_SCHEMA_VERSION}.approval",
            "approval_id": f"asla_{uuid4().hex[:16]}",
            "approved_at": _now_iso(),
            "approved_by": approved_by or None,
            "run_id": rid,
            "candidate_id": str(candidate.get("candidate_id") or ""),
            "instance": inst,
            "live_config_path": str(cfg_path),
            "worker_started": False,
            "orders_sent": False,
            "strategy_config": next_config.get("strategy"),
            "strategy_config_patch": {"strategy": strategy_patch, "risk": risk_patch},
            "previous_strategy_config": previous_config.get("strategy") if isinstance(previous_config.get("strategy"), dict) else {},
            "previous_config": previous_config,
            "next_config": next_config,
            "diff": diff,
            "validation_summary": validation.get("summary") if isinstance(validation, dict) else {},
        }
        _write_json_atomic(subdir / "agent_strategy_lab_approved_draft.json", approval)
        approvals_store = _load_approvals(root)
        approvals_store.append(approval)
        _save_approvals(root, approvals_store)
        approvals = run.setdefault("approvals", [])
        if isinstance(approvals, list):
            approvals.append(approval)
        run["status"] = "approved"
        run["approved_candidate_id"] = str(candidate.get("candidate_id") or "")
        _save_runs(root, runs)
        return {"ok": True, "approval": approval, "config": next_config}
    effective_patch = compact_strategy_patch(current_sc, candidate_patch)
    approved_sc = dict(current_sc)
    approved_sc.update(effective_patch)
    previous_config = dict(live_config)
    previous_strategy_config = dict(current_sc)
    next_config = dict(live_config)
    next_config["strategy_config"] = approved_sc
    diff = build_patch_diff(previous_strategy_config, effective_patch)
    live_config["strategy_config"] = approved_sc
    _write_json_atomic(cfg_path, live_config)

    approval = {
        "schema": f"{LAB_SCHEMA_VERSION}.approval",
        "approval_id": f"asla_{uuid4().hex[:16]}",
        "approved_at": _now_iso(),
        "approved_by": approved_by or None,
        "run_id": rid,
        "candidate_id": str(candidate.get("candidate_id") or ""),
        "instance": inst,
        "live_config_path": str(cfg_path),
        "worker_started": False,
        "orders_sent": False,
        "strategy_config": approved_sc,
        "strategy_config_patch": effective_patch,
        "previous_strategy_config": previous_strategy_config,
        "previous_config": previous_config,
        "next_config": next_config,
        "diff": diff,
        "validation_summary": validation.get("summary") if isinstance(validation, dict) else {},
    }
    _write_json_atomic(subdir / "agent_strategy_lab_approved_draft.json", approval)
    approvals_store = _load_approvals(root)
    approvals_store.append(approval)
    _save_approvals(root, approvals_store)
    approvals = run.setdefault("approvals", [])
    if isinstance(approvals, list):
        approvals.append(approval)
    run["status"] = "approved"
    run["approved_candidate_id"] = str(candidate.get("candidate_id") or "")
    _save_runs(root, runs)
    return {"ok": True, "approval": approval, "config": live_config}


def preview_candidate_diff(run_id: str, candidate_id: str, *, root: Path | None = None) -> dict[str, Any]:
    root = root or default_root()
    run = get_lab_run(run_id, root).get("run")
    if not isinstance(run, dict):
        raise AgentStrategyLabError("run_not_found")
    candidate = _find_candidate(run, candidate_id)
    inst = normalize_instance(run.get("instance"))
    cfg_path = _data_dir(root, inst) / "live_worker_config.json"
    live_config = _read_json(cfg_path, {}) or {}
    if not isinstance(live_config, dict):
        live_config = {}
    current_sc = live_config.get("strategy_config") if isinstance(live_config.get("strategy_config"), dict) else {}
    candidate_patch = candidate.get("strategy_config_patch") if isinstance(candidate.get("strategy_config_patch"), dict) else {}
    if not candidate_patch:
        req_variant = run.get("request", {}).get("strategy_variant") if isinstance(run.get("request"), dict) else None
        if normalize_instance(run.get("instance")) == "stock_options_swing":
            candidate_patch = {"strategy": {"strategy_variant": normalize_swing_strategy_variant(req_variant)}}
        else:
            candidate_patch = {"strategy_variant": normalize_lab_strategy_variant(req_variant)}
    if inst == "stock_options_swing":
        strategy_patch = candidate_patch.get("strategy") if isinstance(candidate_patch.get("strategy"), dict) else {}
        risk_patch = candidate_patch.get("risk") if isinstance(candidate_patch.get("risk"), dict) else {}
        next_config = json.loads(json.dumps(live_config, ensure_ascii=False, default=str))
        strategy_before = next_config.get("strategy") if isinstance(next_config.get("strategy"), dict) else {}
        risk_before = next_config.get("risk") if isinstance(next_config.get("risk"), dict) else {}
        next_config["strategy"] = {**strategy_before, **strategy_patch}
        next_config["risk"] = {**risk_before, **risk_patch}
        return {
            "ok": True,
            "run_id": str(run.get("run_id") or run_id),
            "candidate_id": str(candidate.get("candidate_id") or candidate_id),
            "instance": inst,
            "live_config_path": str(cfg_path),
            "current_strategy_config": {"strategy": strategy_before, "risk": risk_before},
            "candidate_strategy_config": {"strategy": next_config.get("strategy"), "risk": next_config.get("risk")},
            "strategy_config_patch": {"strategy": strategy_patch, "risk": risk_patch},
            "diff": build_patch_diff(live_config, {"strategy": strategy_patch, "risk": risk_patch}),
        }
    effective_patch = compact_strategy_patch(current_sc, candidate_patch)
    next_sc = dict(current_sc)
    next_sc.update(effective_patch)
    return {
        "ok": True,
        "run_id": str(run.get("run_id") or run_id),
        "candidate_id": str(candidate.get("candidate_id") or candidate_id),
        "instance": inst,
        "live_config_path": str(cfg_path),
        "current_strategy_config": current_sc,
        "candidate_strategy_config": next_sc,
        "strategy_config_patch": effective_patch,
        "diff": build_patch_diff(current_sc, effective_patch),
    }


def list_approvals(root: Path | None = None, instance: str | None = None, limit: int = 20) -> dict[str, Any]:
    root = root or default_root()
    rows = _load_approvals(root)
    if instance:
        inst = normalize_instance(instance)
        rows = [x for x in rows if str(x.get("instance") or "") == inst]
    rows.sort(key=lambda x: str(x.get("approved_at") or ""), reverse=True)
    lim = max(1, min(100, int(limit)))
    return {"ok": True, "items": rows[:lim], "returned": min(len(rows), lim)}


def rollback_approval(
    approval_id: str | None = None,
    *,
    root: Path | None = None,
    instance: str = "0dte",
) -> dict[str, Any]:
    root = root or default_root()
    inst = normalize_instance(instance)
    approvals = _load_approvals(root)
    rows = [x for x in approvals if str(x.get("instance") or "") == inst]
    if approval_id:
        aid = str(approval_id or "").strip()
        rows = [x for x in rows if str(x.get("approval_id") or "") == aid]
    rows.sort(key=lambda x: str(x.get("approved_at") or ""), reverse=True)
    if not rows:
        raise AgentStrategyLabError("approval_not_found")
    approval = rows[0]
    previous_config = approval.get("previous_config") if isinstance(approval.get("previous_config"), dict) else None
    previous_strategy_config = (
        approval.get("previous_strategy_config") if isinstance(approval.get("previous_strategy_config"), dict) else None
    )
    cfg_path = _data_dir(root, inst) / "live_worker_config.json"
    if previous_config is not None:
        restored = dict(previous_config)
    else:
        restored = _read_json(cfg_path, {}) or {}
        if not isinstance(restored, dict):
            restored = {}
        restored["strategy_config"] = previous_strategy_config or {}
    _write_json_atomic(cfg_path, restored)
    record = {
        "schema": f"{LAB_SCHEMA_VERSION}.rollback",
        "rollback_id": f"aslr_{uuid4().hex[:16]}",
        "rolled_back_at": _now_iso(),
        "approval_id": approval.get("approval_id"),
        "instance": inst,
        "live_config_path": str(cfg_path),
        "restored_strategy_config": restored.get("strategy_config") if isinstance(restored.get("strategy_config"), dict) else {},
        "worker_started": False,
        "orders_sent": False,
    }
    _write_json_atomic(_data_dir(root, inst) / "agent_strategy_lab_rollback.json", record)
    return {"ok": True, "rollback": record, "config": restored}


def lab_status(root: Path | None = None, instance: str = "0dte") -> dict[str, Any]:
    root = root or default_root()
    inst = normalize_instance(instance)
    runs = list_lab_runs(root, instance=inst, limit=1).get("items") or []
    approval = _read_json(_data_dir(root, inst) / "agent_strategy_lab_approved_draft.json", {}) or {}
    return {
        "ok": True,
        "schema": f"{LAB_SCHEMA_VERSION}.status",
        "instance": inst,
        "generated_at": _now_iso(),
        "data_quality": build_data_quality_report(root, inst, tail_limit=200),
        "last_run": runs[0] if runs else None,
        "last_approval": approval if isinstance(approval, dict) and approval else None,
        "approval_history": list_approvals(root, instance=inst, limit=5).get("items", []),
        "capabilities": {
            "candidate_generators": ["deterministic", "tradingagents"],
            "research_dimensions": ["risk_controls", "leg_gap", "time_window", "combined"],
            "strategy_variants": list(VALID_SWING_STRATEGY_VARIANTS)
            if inst == "stock_options_swing"
            else ["morning_strangle", "morning_double_strangle", "morning_directional"],
            "approval_writes_live_worker_config": True,
            "starts_worker": False,
            "places_orders": False,
        },
    }
