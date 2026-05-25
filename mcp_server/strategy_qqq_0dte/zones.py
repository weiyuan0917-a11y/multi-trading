"""关键价位周围的反应区（带状）。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReactionZone:
    center: float
    low: float
    high: float

    def contains(self, price: float) -> bool:
        return self.low <= price <= self.high


def build_zones_from_levels(level_prices: list[float], half_width_pct: float) -> list[ReactionZone]:
    if half_width_pct <= 0:
        half_width_pct = 0.0005
    zones: list[ReactionZone] = []
    for lv in level_prices:
        if lv <= 0:
            continue
        w = abs(float(lv) * half_width_pct)
        lo, hi = lv - w, lv + w
        zones.append(ReactionZone(center=lv, low=lo, high=hi))
    return zones


def find_active_zone(price: float, zones: list[ReactionZone]) -> ReactionZone | None:
    for z in zones:
        if z.contains(price):
            return z
    return None
