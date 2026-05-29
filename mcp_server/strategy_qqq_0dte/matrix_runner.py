"""多组策略参数网格回测：同一批 K 线重复跑，用于矩阵删选。"""
from __future__ import annotations

from itertools import product
from typing import Any

try:
    from mcp_server.backtest_engine import Bar
except ImportError:
    from backtest_engine import Bar

from .backtest import run_qqq_0dte_backtest
from .config import Qqq0dteConfig


def _serialize_grid_value(x: Any) -> Any:
    if isinstance(x, float):
        return round(x, 8)
    return x


def apply_grid_to_strategy_dict(cfg: dict[str, Any], key: str, value: Any) -> None:
    """
    将网格的一维写入 strategy_config 副本。
    reaction_zone_width_pct：与前端一致，按「标价百分之几」写入，换算为 reaction_zone_half_width_pct。
    *_ui：与策略表单一致，百分数写入后再换算为后端比例字段。
    """
    if key == "reaction_zone_width_pct":
        cfg["reaction_zone_half_width_pct"] = float(value) / 100.0
        return
    if key == "strangle_range_pct_ui":
        cfg["strangle_range_pct"] = max(0.0, float(value) / 100.0)
        return
    if key == "directional_down_pct_ui":
        cfg["directional_down_pct"] = max(0.0, float(value) / 100.0)
        return
    if key == "directional_up_pct_ui":
        cfg["directional_up_pct"] = max(0.0, float(value) / 100.0)
        return
    if key == "strangle_take_profit_return_ui":
        cfg["strangle_take_profit_return"] = max(0.0, float(value) / 100.0)
        return
    if key == "strangle_stop_loss_return_ui":
        cfg["strangle_stop_loss_return"] = max(0.0, float(value) / 100.0)
        return
    if key == "strangle_stop_loss_cooldown_minutes":
        cfg["strangle_stop_loss_cooldown_minutes"] = max(0, int(float(value)))
        return
    if key == "double_strangle_combo_take_profit_pct_ui":
        cfg["double_strangle_combo_take_profit_pct"] = max(0.0, float(value) / 100.0)
        return
    if key == "double_strangle_combo_stop_loss_pct_ui":
        cfg["double_strangle_combo_stop_loss_pct"] = max(0.0, float(value) / 100.0)
        return
    if key == "double_strangle_single_leg_stop_loss_pct_ui":
        cfg["double_strangle_single_leg_stop_loss_pct"] = max(0.0, float(value) / 100.0)
        return
    if key == "double_strangle_call_long_leg_take_profit_pct_ui":
        cfg["double_strangle_call_long_leg_take_profit_pct"] = max(0.0, float(value) / 100.0)
        return
    if key == "double_strangle_call_short_leg_take_profit_pct_ui":
        cfg["double_strangle_call_short_leg_take_profit_pct"] = max(0.0, float(value) / 100.0)
        return
    if key == "double_strangle_put_long_leg_take_profit_pct_ui":
        cfg["double_strangle_put_long_leg_take_profit_pct"] = max(0.0, float(value) / 100.0)
        return
    if key == "double_strangle_put_short_leg_take_profit_pct_ui":
        cfg["double_strangle_put_short_leg_take_profit_pct"] = max(0.0, float(value) / 100.0)
        return
    if key == "directional_take_profit_return_ui":
        cfg["directional_take_profit_return"] = max(0.0, float(value) / 100.0)
        return
    if key == "directional_stop_loss_pct_ui":
        cfg["directional_stop_loss_pct"] = max(0.0, float(value) / 100.0)
        return
    if key == "gamma_hard_stop_loss_pct_ui":
        cfg["gamma_hard_stop_loss_pct"] = max(0.0, float(value) / 100.0)
        return
    if key == "gamma_take_profit_min_return_ui":
        cfg["gamma_take_profit_min_return"] = max(0.0, float(value) / 100.0)
        return
    if key == "gamma_take_profit_max_return_ui":
        cfg["gamma_take_profit_max_return"] = max(0.0, float(value) / 100.0)
        return
    if key == "gamma_vwap_deviation_pct_ui":
        cfg["gamma_vwap_deviation_pct"] = max(0.0, float(value) / 100.0)
        return
    if key == "strategy_variant":
        cfg["strategy_variant"] = str(value).strip()
        return
    if key in (
        "call_strikes_otm",
        "put_strikes_otm",
        "gamma_call_otm_steps",
        "gamma_put_otm_steps",
        "gamma_max_hold_minutes",
        "psychological_levels_max",
        "initial_option_contracts",
        "double_strangle_call_long_strikes_otm",
        "double_strangle_call_short_strikes_otm",
        "double_strangle_put_long_strikes_otm",
        "double_strangle_put_short_strikes_otm",
    ):
        cfg[key] = int(float(value))
        return
    cfg[str(key)] = value


def grid_combination_count(grid: dict[str, list[Any]]) -> int:
    n = 1
    for vals in grid.values():
        ln = len(vals)
        if ln == 0:
            return 0
        n *= ln
    return n


def run_parameter_matrix(
    bars: list[Bar],
    *,
    base_strategy_config: dict[str, Any],
    grid: dict[str, list[Any]],
    symbol: str,
    suppress_logs: bool = True,
) -> list[dict[str, Any]]:
    """对 grid 的笛卡尔积逐组回测；返回轻量行（不含 trades / decision_summary）。"""
    keys = [str(k) for k in grid.keys()]
    axes = [grid[k] for k in keys]
    out: list[dict[str, Any]] = []

    for combo in product(*axes):
        merged: dict[str, Any] = dict(base_strategy_config) if base_strategy_config else {}
        if suppress_logs:
            merged["log_decisions"] = False
        overrides: dict[str, Any] = {}
        for i, k in enumerate(keys):
            v = combo[i]
            overrides[k] = v
            apply_grid_to_strategy_dict(merged, k, v)

        cfg = Qqq0dteConfig.from_dict(merged)
        cfg.symbol = str(symbol).strip().upper()
        r = run_qqq_0dte_backtest(bars, cfg)
        st = r.get("stats") if isinstance(r.get("stats"), dict) else {}

        out.append(
            {
                "grid_params": {k: _serialize_grid_value(overrides[k]) for k in keys},
                "strategy_config": r.get("config") if isinstance(r.get("config"), dict) else {},
                "realized_pnl": r.get("realized_pnl"),
                "open_premium_debit_usd": r.get("open_premium_debit_usd"),
                "return_pct": r.get("return_pct"),
                "total_fee": r.get("total_fee"),
                "bar_count": r.get("bar_count"),
                "open_events": r.get("open_events"),
                "close_events": r.get("close_events"),
                "closed_trades": st.get("closed_trades"),
                "wins": st.get("wins"),
                "losses": st.get("losses"),
                "win_rate_pct": st.get("win_rate_pct"),
            }
        )

    return out
