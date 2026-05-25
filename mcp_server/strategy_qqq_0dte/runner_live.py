"""实盘/定时任务：逐根推送 1m K 线，产出意图；下单前用 live_contract.fetch_and_resolve_0dte_leg 或 POST /strategy/qqq-0dte/resolve-contract 解析 OPRA，再以 oms_adapter.intent_to_legs_resolved 组腿后走 /options/order。"""
from __future__ import annotations

from datetime import date
from typing import Literal, Sequence

from backtest_engine import Bar

from .config import Qqq0dteConfig
from .controller import BarProcessResult, StrategyController
from .oms_adapter import intent_to_legs_template
from .state import OpenPosition


class Qqq0dteLiveSession:
    """
    维护当日以来（或会话以来）的 Bar 列表，每来一根 K 调用策略核心。
    换日时调用 reset_for_new_calendar_day。

    实盘仅推送「当日」K 线时，策略内部看不到前一交易日，会导致昨收 prev_close 缺失。
    `_anchor_bars` 为前一交易日（美东日历）的 K 线，只参与 prepare/定价，不计入 bars_snapshot 根数
    （Worker 仍用 bars_snapshot 长度判断已推送的当日根数）。
    """

    def __init__(self, cfg: Qqq0dteConfig | None = None) -> None:
        self.cfg = cfg or Qqq0dteConfig()
        self._ctl = StrategyController(self.cfg)
        self._bars: list[Bar] = []
        self._anchor_bars: list[Bar] = []
        # 宽跨：Worker 注入 (call_bid, put_bid) 与 (call_last, put_last)；K 线内止盈用 bid、止损用 last。
        self._strangle_live_bids: tuple[float | None, float | None] | None = None
        self._strangle_live_lasts: tuple[float | None, float | None] | None = None
        self._double_strangle_live_bids: dict[str, float | None] | None = None
        self._double_strangle_live_lasts: dict[str, float | None] | None = None
        self._option_live_bid: float | None = None
        self._option_live_last: float | None = None

    def reset(self) -> None:
        self._bars.clear()
        self._anchor_bars.clear()
        self._strangle_live_bids = None
        self._strangle_live_lasts = None
        self._double_strangle_live_bids = None
        self._double_strangle_live_lasts = None
        self._option_live_bid = None
        self._option_live_last = None
        self._ctl.reset()

    def set_anchor_bars(self, bars: Sequence[Bar]) -> None:
        """注入前一交易日等历史 K 线（与当日 push_bar 拼接后交给 StrategyController）。"""
        self._anchor_bars = list(bars)

    def set_strangle_live_quotes(
        self,
        call_bid: float | None,
        put_bid: float | None,
        call_last: float | None,
        put_last: float | None,
    ) -> None:
        """LIVE Worker：注入宽跨两腿 bid（止盈）与 last（止损）；全为 None 则清空。"""
        if call_bid is None and put_bid is None and call_last is None and put_last is None:
            self._strangle_live_bids = None
            self._strangle_live_lasts = None
        else:
            self._strangle_live_bids = (call_bid, put_bid)
            self._strangle_live_lasts = (call_last, put_last)

    def set_double_strangle_live_quotes(
        self,
        bids: dict[str, float | None] | None,
        lasts: dict[str, float | None] | None,
    ) -> None:
        """LIVE Worker: inject bid/last quotes for four double-strangle legs."""
        clean_bids = bids if isinstance(bids, dict) else {}
        clean_lasts = lasts if isinstance(lasts, dict) else {}
        if not clean_bids and not clean_lasts:
            self._double_strangle_live_bids = None
            self._double_strangle_live_lasts = None
            return
        self._double_strangle_live_bids = dict(clean_bids)
        self._double_strangle_live_lasts = dict(clean_lasts)

    def set_option_live_quotes(self, bid: float | None, last: float | None) -> None:
        """LIVE Worker：单腿持仓时注入该腿 OPRA 的 bid / last（K 线内止盈/止损）。"""
        self._option_live_bid = bid
        self._option_live_last = last

    def push_bar(self, bar: Bar) -> BarProcessResult:
        self._bars.append(bar)
        all_bars = self._anchor_bars + self._bars
        self._ctl.prepare(all_bars)
        pos = self._ctl._pos
        self._ctl.strangle_live_leg_bids = None
        self._ctl.strangle_live_leg_lasts = None
        self._ctl.double_strangle_live_leg_bids = None
        self._ctl.double_strangle_live_leg_lasts = None
        self._ctl.option_live_bid = None
        self._ctl.option_live_last = None
        if (
            pos is not None
            and str(getattr(pos, "side", "") or "") == "strangle"
            and (self._strangle_live_bids is not None or self._strangle_live_lasts is not None)
        ):
            self._ctl.strangle_live_leg_bids = self._strangle_live_bids
            self._ctl.strangle_live_leg_lasts = self._strangle_live_lasts
        elif (
            pos is not None
            and str(getattr(pos, "side", "") or "") == "double_strangle"
            and (self._double_strangle_live_bids is not None or self._double_strangle_live_lasts is not None)
        ):
            self._ctl.double_strangle_live_leg_bids = self._double_strangle_live_bids
            self._ctl.double_strangle_live_leg_lasts = self._double_strangle_live_lasts
        elif pos is not None and str(getattr(pos, "side", "") or "") in ("long_call", "long_put"):
            if self._option_live_bid is not None or self._option_live_last is not None:
                self._ctl.option_live_bid = self._option_live_bid
                self._ctl.option_live_last = self._option_live_last
        try:
            return self._ctl.process_bar(len(all_bars) - 1, all_bars)
        finally:
            self._ctl.strangle_live_leg_bids = None
            self._ctl.strangle_live_leg_lasts = None
            self._ctl.double_strangle_live_leg_bids = None
            self._ctl.double_strangle_live_leg_lasts = None
            self._ctl.option_live_bid = None
            self._ctl.option_live_last = None

    def bars_snapshot(self) -> Sequence[Bar]:
        """仅当日已推送的 K 线（不含 anchor），供 Worker 与历史根数对齐。"""
        return list(self._bars)

    def open_position(self) -> OpenPosition | None:
        """返回当前策略持仓（若无则 None）。"""
        return self._ctl._pos

    def clear_open_position(self) -> None:
        """外部已完成平仓后，清空策略内持仓状态避免重复发平仓意图。"""
        self._ctl._pos = None

    def restore_open_position(self, pos: OpenPosition | None) -> None:
        """Restore strategy position when a live close signal did not place an order."""
        self._ctl._pos = pos

    def trades_today_count(self, session_date: date) -> int:
        return int(self._ctl._trades_today[session_date])

    def set_trades_today_count(self, session_date: date, count: int) -> None:
        self._ctl._trades_today[session_date] = max(0, int(count))

    def apply_strangle_leg_closed(self, which: Literal["call", "put"], exit_px: float = 0.0) -> None:
        """宽跨单腿平仓成交后：记录已实现卖出金额，并更新 entry_px 为剩余腿成本。"""
        pos = self._ctl._pos
        if pos is None or pos.side != "strangle":
            return
        if which == "call":
            pos.strangle_realized_exit_px += max(0.0, float(exit_px))
            pos.strangle_call_active = False
            pos.call_entry_px = 0.0
            pos.call_strike = 0.0
        else:
            pos.strangle_realized_exit_px += max(0.0, float(exit_px))
            pos.strangle_put_active = False
            pos.put_entry_px = 0.0
            pos.put_strike = 0.0
        ac = float(pos.call_entry_px) if pos.strangle_call_active else 0.0
        ap = float(pos.put_entry_px) if pos.strangle_put_active else 0.0
        pos.entry_px = float(ac + ap)
        if not pos.strangle_call_active and not pos.strangle_put_active:
            self._ctl._pos = None

    def apply_double_strangle_leg_closed(self, leg_key: str, exit_px: float = 0.0) -> None:
        """Update a four-leg double strangle after one leg was sold or manually closed."""
        pos = self._ctl._pos
        if pos is None or pos.side != "double_strangle":
            return
        legs = getattr(pos, "double_strangle_legs", None)
        if not isinstance(legs, dict):
            return
        leg = legs.get(str(leg_key or ""))
        if not isinstance(leg, dict):
            return
        pos.strangle_realized_exit_px += max(0.0, float(exit_px))
        leg["active"] = False
        leg["entry_px"] = 0.0
        active = {k: v for k, v in legs.items() if isinstance(v, dict) and bool(v.get("active", True))}
        pos.entry_px = float(sum(float(v.get("entry_px") or 0.0) for v in active.values()))
        pos.call_entry_px = float(
            sum(float(v.get("entry_px") or 0.0) for v in active.values() if str(v.get("right") or "") == "call")
        )
        pos.put_entry_px = float(
            sum(float(v.get("entry_px") or 0.0) for v in active.values() if str(v.get("right") or "") == "put")
        )
        if not active:
            self._ctl._pos = None

    @staticmethod
    def legs_placeholder_for_intent(result: BarProcessResult):
        """若有开仓意图，生成待填充 OPRA 的 legs 模板。"""
        if not result.intents:
            return None
        return intent_to_legs_template(result.intents[0], limit_price_per_share=None)
