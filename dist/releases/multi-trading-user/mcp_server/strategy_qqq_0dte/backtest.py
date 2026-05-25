"""按 1m（或其它周期）K 线回放 StrategyController，汇总 PnL 与费用。"""
from __future__ import annotations

from collections import Counter, deque
from typing import Any, Sequence

from backtest_engine import Bar

from fee_model import estimate_us_option_multi_leg_fee

from .config import Qqq0dteConfig
from .controller import StrategyController, _apply_strangle_leg_closed_to_pos
from .decision_log_summary import label_for_message, summarize_decision_messages
from .session_us import to_ny


def _sum_open_premium_debit_usd(trades: list[dict[str, Any]], contract_multiplier: int) -> float:
    """回测内每次开仓付出的权利金合计（每股价 × 张数 × 合约乘数），不含手续费。"""
    mult = max(1, int(contract_multiplier))
    total = 0.0
    for t in trades:
        if t.get("event") != "open":
            continue
        qty = int(t.get("contracts") or 0)
        if qty <= 0:
            continue
        side = str(t.get("side") or "")
        if side == "strangle":
            cep = float(t.get("call_entry_px") or 0.0)
            pep = float(t.get("put_entry_px") or 0.0)
            total += (cep + pep) * qty * mult
        else:
            ep = float(t.get("entry_px") or 0.0)
            total += ep * qty * mult
    return total


def run_qqq_0dte_backtest(bars: Sequence[Bar], cfg: Qqq0dteConfig | None = None) -> dict[str, Any]:
    cfg = cfg or Qqq0dteConfig()
    sorted_bars = sorted(list(bars), key=lambda b: b.date)
    ctl = StrategyController(cfg)
    ctl.prepare(sorted_bars)

    mult = max(1, int(cfg.contract_multiplier))
    trades: list[dict[str, Any]] = []
    total_fee = 0.0
    realized_pnl = 0.0
    open_count = 0
    close_count = 0
    log_enabled = bool(cfg.log_decisions)
    msg_counter: Counter[str] = Counter()
    preview_logs: deque[dict[str, Any]] = deque(maxlen=48)

    for i, _bar in enumerate(sorted_bars):
        r = ctl.process_bar(i, sorted_bars)
        # 原始 K 线时间戳（按输入 bars 原样记录）+ 转换后的美东时间，避免把原始墙钟误标为 UTC。
        bar_time_raw = _bar.date.isoformat()
        bar_time_et = to_ny(_bar.date, cfg.assume_bars_timezone).isoformat()
        if log_enabled:
            for log_entry in r.logs:
                m = str(log_entry.message or "")
                msg_counter[m] += 1
                preview_logs.append(
                    {
                        "bar_index": int(log_entry.bar_index),
                        "as_of": str(log_entry.as_of),
                        "message": m,
                        "label_zh": label_for_message(m),
                    }
                )
        if r.entry_snapshot:
            es = r.entry_snapshot
            qty = int(es["contracts"])
            if str(es.get("side") or "") == "strangle":
                cep = float(es["call_entry_px"])
                pep = float(es["put_entry_px"])
                fee_buy = float(
                    estimate_us_option_multi_leg_fee(
                        [
                            {"side": "buy", "contracts": qty, "price": cep, "symbol": "SYNTH.OPT"},
                            {"side": "buy", "contracts": qty, "price": pep, "symbol": "SYNTH.OPT"},
                        ]
                    )["total_fee"]
                )
            else:
                ep = float(es["entry_px"])
                fee_buy = float(
                    estimate_us_option_multi_leg_fee(
                        [{"side": "buy", "contracts": qty, "price": ep, "symbol": "SYNTH.OPT"}]
                    )["total_fee"]
                )
            total_fee += fee_buy
            open_count += 1
            trades.append(
                {
                    "event": "open",
                    "bar_index": i,
                    "bar_time_raw": bar_time_raw,
                    "bar_time_et": bar_time_et,
                    # 兼容旧前端字段
                    "bar_time_utc": bar_time_raw,
                    "bar_time_local": bar_time_et,
                    "fee": round(fee_buy, 4),
                    **es,
                }
            )
        if r.close_position and r.exit_snapshot:
            xs = r.exit_snapshot
            qty = int(xs["contracts"])
            if str(xs.get("side") or "") == "strangle":
                c0 = float(xs["call_entry_px"])
                p0 = float(xs["put_entry_px"])
                cx = float(xs["call_exit_px"])
                px = float(xs["put_exit_px"])
                cc = bool(xs.get("close_call", True))
                cp = bool(xs.get("close_put", True))
                fee_legs: list[dict[str, Any]] = []
                gross = 0.0
                if cc and c0 > 0:
                    gross += (cx - c0) * qty * mult
                    fee_legs.append({"side": "sell", "contracts": qty, "price": max(0.0, cx), "symbol": "SYNTH.OPT"})
                if cp and p0 > 0:
                    gross += (px - p0) * qty * mult
                    fee_legs.append({"side": "sell", "contracts": qty, "price": max(0.0, px), "symbol": "SYNTH.OPT"})
                fee_sell = float(estimate_us_option_multi_leg_fee(fee_legs)["total_fee"]) if fee_legs else 0.0
            else:
                ep = float(xs["entry_px"])
                xp = float(xs["exit_px"])
                gross = (xp - ep) * qty * mult
                fee_sell = float(
                    estimate_us_option_multi_leg_fee(
                        [{"side": "sell", "contracts": qty, "price": xp, "symbol": "SYNTH.OPT"}]
                    )["total_fee"]
                )
            total_fee += fee_sell
            net = gross - fee_sell
            realized_pnl += net
            close_count += 1
            trades.append(
                {
                    "event": "close",
                    "bar_index": i,
                    "bar_time_raw": bar_time_raw,
                    "bar_time_et": bar_time_et,
                    # 兼容旧前端字段
                    "bar_time_utc": bar_time_raw,
                    "bar_time_local": bar_time_et,
                    "gross_pnl": round(gross, 4),
                    "fee": round(fee_sell, 4),
                    "net_pnl": round(net, 4),
                    "reason": r.close_reason,
                    **xs,
                }
            )
            if str(xs.get("side") or "") == "strangle":
                partial_leg = str(xs.get("strangle_partial_leg") or "none")
                pos = ctl._pos
                if pos is not None and partial_leg in {"call", "put"}:
                    exit_px = float(xs.get("call_exit_px" if partial_leg == "call" else "put_exit_px") or 0.0)
                    _apply_strangle_leg_closed_to_pos(pos, partial_leg, exit_px)

    closed_round_trips = close_count
    wins = sum(1 for t in trades if t.get("event") == "close" and float(t.get("net_pnl", 0)) > 0)
    losses = sum(1 for t in trades if t.get("event") == "close" and float(t.get("net_pnl", 0)) <= 0)

    decision_summary = summarize_decision_messages(
        msg_counter,
        bar_count=len(sorted_bars),
        log_decisions_enabled=log_enabled,
        preview_tail=list(preview_logs),
    )

    premium_debit = _sum_open_premium_debit_usd(trades, cfg.contract_multiplier)
    premium_debit_r = round(premium_debit, 4)
    return_pct = (
        round(float(realized_pnl) / premium_debit * 100.0, 4) if premium_debit > 0 else None
    )

    return {
        "symbol": cfg.symbol,
        "bar_count": len(sorted_bars),
        "open_events": open_count,
        "close_events": close_count,
        "realized_pnl": round(realized_pnl, 4),
        "open_premium_debit_usd": premium_debit_r,
        "return_pct": return_pct,
        "total_fee": round(total_fee, 4),
        "trades": trades,
        "stats": {
            "closed_trades": closed_round_trips,
            "wins": wins,
            "losses": losses,
            "win_rate_pct": round((wins / closed_round_trips * 100.0) if closed_round_trips else 0.0, 2),
        },
        "config": cfg.to_dict(),
        "decision_summary": decision_summary,
        "disclaimer": "合成 BS 理论价回测，非真实期权成交；参数见 config。",
    }
