"""分钟成交量突增检测。"""
from __future__ import annotations

from typing import Sequence

from backtest_engine import Bar

from .config import Qqq0dteConfig


def volume_spike_at(
    bars: Sequence[Bar],
    index: int,
    cfg: Qqq0dteConfig,
) -> bool:
    if index < 1:
        return False
    lb = max(2, int(cfg.volume_lookback_bars))
    start = max(0, index - lb)
    hist = [max(1.0, float(bars[j].volume)) for j in range(start, index)]
    if not hist:
        return False
    avg = sum(hist) / max(len(hist), 1)
    cur = max(1.0, float(bars[index].volume))
    return cur >= avg * float(cfg.volume_spike_multiplier)
