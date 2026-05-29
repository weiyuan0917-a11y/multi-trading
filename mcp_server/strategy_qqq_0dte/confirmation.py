"""突破站稳、简易反转（回踩反应区后收回）。"""
from __future__ import annotations

from typing import Literal, Sequence

try:
    from mcp_server.backtest_engine import Bar
except ImportError:
    from backtest_engine import Bar

from .config import Qqq0dteConfig
from .zones import ReactionZone


def breakout_confirmed(
    bars: Sequence[Bar],
    index: int,
    level: float,
    direction: Literal["up", "down"],
    cfg: Qqq0dteConfig,
) -> bool:
    n = max(2, int(cfg.breakout_hold_bars))
    if index < n:
        return False
    if direction == "up":
        for j in range(index - n + 1, index + 1):
            if float(bars[j].close) <= level:
                return False
        return True
    for j in range(index - n + 1, index + 1):
        if float(bars[j].close) >= level:
            return False
    return True


def reversal_after_zone_touch(
    bars: Sequence[Bar],
    index: int,
    zone: ReactionZone,
    direction: Literal["up", "down"],
    cfg: Qqq0dteConfig,
) -> bool:
    """
    简化：direction=up 表示在反应区下沿附近快速反弹（看多 Put 减仓/Call 思路的辅助）；
    direction=down 表示上沿附近遇阻回落。
    """
    if index < 2:
        return False
    pull = float(cfg.reversal_pullback_pct)
    b0, b1, b2 = bars[index - 2], bars[index - 1], bars[index]
    c0, c1, c2 = float(b0.close), float(b1.close), float(b2.close)
    if direction == "up":
        touched = float(b1.low) <= zone.high * (1 + pull)
        rebound = c2 > c1 and c2 > zone.center
        return touched and rebound
    touched = float(b1.high) >= zone.low * (1 - pull)
    reject = c2 < c1 and c2 < zone.center
    return touched and reject
