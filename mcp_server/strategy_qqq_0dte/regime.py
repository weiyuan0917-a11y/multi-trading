"""开盘缺口与日内偏多/偏空状态（简化）。"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .config import Qqq0dteConfig


class GapRegime(str, Enum):
    GAP_UP = "gap_up"
    GAP_DOWN = "gap_down"
    FLAT = "flat"


@dataclass
class OpeningRegime:
    gap: GapRegime
    bias_calls: bool
    bias_puts: bool


def classify_gap(open_price: float, prev_close: float | None, cfg: Qqq0dteConfig) -> GapRegime:
    if prev_close is None or prev_close <= 0:
        return GapRegime.FLAT
    denom_prev_close = max(float(prev_close), 1e-12)
    chg = (float(open_price) - float(prev_close)) / denom_prev_close
    th = float(cfg.gap_threshold_pct)
    if chg > th:
        return GapRegime.GAP_UP
    if chg < -th:
        return GapRegime.GAP_DOWN
    return GapRegime.FLAT


def opening_regime(open_price: float, prev_close: float | None, cfg: Qqq0dteConfig) -> OpeningRegime:
    g = classify_gap(open_price, prev_close, cfg)
    if g == GapRegime.GAP_UP:
        return OpeningRegime(gap=g, bias_calls=True, bias_puts=False)
    if g == GapRegime.GAP_DOWN:
        return OpeningRegime(gap=g, bias_calls=False, bias_puts=True)
    return OpeningRegime(gap=g, bias_calls=True, bias_puts=True)
