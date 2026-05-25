"""回测决策日志汇总：统计各 message 出现次数，供「无成交原因」诊断。"""
from __future__ import annotations

from collections import Counter
from typing import Any

# 与 controller.log("...") 的 message 一致
REASON_LABELS_ZH: dict[str, str] = {
    "skip_not_rth": "非美股常规交易时段（RTH）",
    "hold_position": "持仓中（仅记录盯市，本根未平仓）",
    "exit": "平仓",
    "skip_no_trade_opening": "开盘后禁止交易窗口内",
    "skip_max_trades_day": "已达当日最大交易次数",
    "no_reaction_zone": "现价不在任何反应区（关键位带状区间）内",
    "no_volume_spike": "未满足成交量突增（相对回看均值×倍数）",
    "restricted_opening_period": "开盘限制期内（距开盘未满 restricted_opening_minutes）",
    "skip_past_new_trade_cutoff": "已过美东新开仓截止时间（仅禁止新开仓，平仓仍执行）",
    "enter_call": "开仓：买入 Call",
    "enter_put": "开仓：买入 Put",
    "no_directional_signal": "在反应区内且有成交量，但未满足突破/反转确认",
    "hold_strangle": "持仓：早盘宽跨（组合盯市）",
    "enter_strangle": "开仓：早盘宽跨（Call+Put）",
    "skip_strangle_entry_window": "不在早盘宽跨开仓时间窗（美东绝对时刻）",
    "skip_strangle_past_force_close": "已过美东宽跨强平时刻（禁止新开仓）",
    "skip_strangle_range": "相对前收涨跌幅超出宽跨阈值",
    "skip_strangle_no_prev_close": "缺少前收，无法判断涨跌幅",
    "strangle_mode_unexpected_position": "宽跨模式下存在非宽跨持仓（已跳过）",
    "reaction_zone_unexpected_position": "反应区模式下存在宽跨持仓（已跳过）",
    "hold_directional": "持仓：早盘方向单（盯市）",
    "enter_directional_call": "开仓：早盘方向单（跌超阈→Call）",
    "enter_directional_put": "开仓：早盘方向单（涨超阈→Put）",
    "skip_directional_entry_window": "不在早盘方向单开仓时间窗",
    "skip_directional_past_force_close": "已过美东方向单强平时刻（与宽跨共用，禁止新开仓）",
    "skip_directional_no_prev_close": "缺少前收（方向单）",
    "skip_directional_threshold": "涨跌幅未达方向单阈值（Call 需跌够深，Put 需涨够高）",
    "directional_mode_unexpected_strangle": "方向单模式下存在宽跨持仓（已跳过）",
    "directional_mode_unexpected_position": "方向单模式下持仓类型异常（已跳过）",
    "hold_gamma_pro": "持仓：Gamma Pro（盯市）",
    "enter_gamma_pro": "开仓：Gamma Pro",
    "skip_gamma_pro_entry_window": "不在 Gamma Pro 开仓时间窗",
    "skip_gamma_pro_midday_pause": "Gamma Pro 午间暂停窗口",
    "skip_gamma_pro_no_entry_signal": "Gamma Pro：未命中突破/假突破/午后续航信号",
    "skip_gamma_pro_leader_gate": "Gamma Pro：龙头确认未通过",
    "gamma_pro_unexpected_position": "Gamma Pro 模式下持仓类型异常（已跳过）",
}

# 通常与「为何没开新仓」相关的 message（持仓中除外）
ENTRY_BLOCKER_MESSAGES: frozenset[str] = frozenset(
    {
        "skip_not_rth",
        "skip_no_trade_opening",
        "skip_max_trades_day",
        "no_reaction_zone",
        "no_volume_spike",
        "restricted_opening_period",
        "skip_past_new_trade_cutoff",
        "no_directional_signal",
        "skip_strangle_entry_window",
        "skip_strangle_past_force_close",
        "skip_strangle_range",
        "skip_strangle_no_prev_close",
        "skip_directional_entry_window",
        "skip_directional_past_force_close",
        "skip_directional_no_prev_close",
        "skip_directional_threshold",
        "skip_gamma_pro_entry_window",
        "skip_gamma_pro_midday_pause",
        "skip_gamma_pro_no_entry_signal",
        "skip_gamma_pro_leader_gate",
    }
)


def label_for_message(message: str) -> str:
    return REASON_LABELS_ZH.get(message, message)


def summarize_decision_messages(
    message_counter: Counter[str],
    *,
    bar_count: int,
    log_decisions_enabled: bool,
    preview_tail: list[dict[str, Any]],
) -> dict[str, Any]:
    total_logs = int(sum(message_counter.values()))
    rows: list[dict[str, Any]] = []
    for msg, cnt in message_counter.most_common():
        pct_logs = round(100.0 * cnt / total_logs, 2) if total_logs else 0.0
        pct_bars = round(100.0 * cnt / bar_count, 2) if bar_count else 0.0
        rows.append(
            {
                "message": msg,
                "label_zh": label_for_message(msg),
                "count": cnt,
                "pct_of_logs": pct_logs,
                "pct_of_bars": pct_bars,
                "is_entry_blocker": msg in ENTRY_BLOCKER_MESSAGES,
            }
        )

    blocker_total = sum(message_counter[m] for m in ENTRY_BLOCKER_MESSAGES)
    blocker_pct_logs = round(100.0 * blocker_total / total_logs, 2) if total_logs else 0.0

    return {
        "log_decisions_enabled": log_decisions_enabled,
        "total_log_lines": total_logs,
        "bar_count": bar_count,
        "by_message": rows,
        "entry_blocker": {
            "total_hits": blocker_total,
            "pct_of_logs": blocker_pct_logs,
            "hint": "上述「开仓闸门」类占比越高，越说明信号被挡在成交量/反应区/时段等条件之前。",
        },
        "preview_tail": preview_tail,
    }
