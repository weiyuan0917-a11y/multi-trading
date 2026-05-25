"""按标的价格与步长选择近 ATM 行权价（链上真实合约代码需在 OMS 层解析）。"""
from __future__ import annotations


def select_strike(spot: float, right: str, *, strike_step: float, otm_steps: int = 0) -> float:
    step = max(0.01, float(strike_step))
    r = str(right).lower()
    denom_step = max(step, 1e-12)
    base = round(float(spot) / denom_step) * step
    otm = int(otm_steps)
    if r in ("call", "c"):
        return round(base + otm * step, 4)
    return round(base - otm * step, 4)
