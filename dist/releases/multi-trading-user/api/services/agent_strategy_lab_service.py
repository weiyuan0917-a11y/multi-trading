from __future__ import annotations

import json
import math
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Callable
from uuid import uuid4

LAB_SCHEMA_VERSION = "agent_strategy_lab.v1"
VALID_INSTANCES = {"0dte": "qqq_0dte", "1dte": "qqq_1dte"}
VALID_LAB_STRATEGY_VARIANTS = {"morning_strangle", "morning_directional"}
VALID_CANDIDATE_GENERATORS = {"deterministic", "tradingagents"}
VALID_RESEARCH_DIMENSIONS = {"risk_controls", "time_window", "combined"}
DEFAULT_VALIDATION_WINDOWS_DAYS = [60, 120, 180]

BacktestRunner = Callable[[dict[str, Any]], dict[str, Any]]
_TASK_LOCK = threading.RLock()
_TASKS: dict[str, dict[str, Any]] = {}
_TASK_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent-strategy-lab")
_TASK_FUTURES: dict[str, Future[Any]] = {}


class AgentStrategyLabError(ValueError):
    pass


def default_root() -> Path:
    return Path(__file__).resolve().parents[2]


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
    if raw not in VALID_INSTANCES:
        raise AgentStrategyLabError("unsupported_instance")
    return raw


def normalize_lab_strategy_variant(value: Any, fallback: str = "morning_strangle") -> str:
    raw = str(value or "").strip().lower()
    if raw in VALID_LAB_STRATEGY_VARIANTS:
        return raw
    fb = str(fallback or "").strip().lower()
    return fb if fb in VALID_LAB_STRATEGY_VARIANTS else "morning_strangle"


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
    try:
        current = _known_strategy_config(current)
    except Exception:
        pass
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
            "strategy_config",
            "已读取策略配置" if strategy_config else "live_worker_config.json 里缺少 strategy_config",
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
    return base


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
        if strategy_variant == "morning_strangle" and abs_change_pct > 0.85:
            action = "skip" if action != "normal_size" else "reduce_size"
            confidence -= 0.12
            reasons.append(f"相对前收波动约 {abs_change_pct:.2f}%，不完全符合早盘宽跨的窄幅假设。")
        elif strategy_variant == "morning_strangle" and abs_change_pct <= 0.35:
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


def validate_candidate(
    candidate: dict[str, Any],
    *,
    instance: str,
    windows_days: list[int],
    kline: str,
    use_server_kline_cache: bool,
    rth_only: bool,
    backtest_runner: BacktestRunner | None = None,
) -> dict[str, Any]:
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
        "instance": normalize_instance(instance),
        "windows_days": list(windows_days),
        "summary": {
            "avg_return_pct": avg_return,
            "min_closed_trades": min_closed,
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
    if progress_callback:
        progress_callback(18, "candidate_generation", "生成候选参数")
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
        candidate_patch = {"strategy_variant": normalize_lab_strategy_variant(run.get("request", {}).get("strategy_variant") if isinstance(run.get("request"), dict) else None)}
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
        candidate_patch = {"strategy_variant": normalize_lab_strategy_variant(run.get("request", {}).get("strategy_variant") if isinstance(run.get("request"), dict) else None)}
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
            "research_dimensions": ["risk_controls", "time_window", "combined"],
            "approval_writes_live_worker_config": True,
            "starts_worker": False,
            "places_orders": False,
        },
    }
