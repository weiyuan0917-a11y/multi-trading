import math
import os
import sys
import unittest
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP_DIR = os.path.join(ROOT, "mcp_server")
if MCP_DIR not in sys.path:
    sys.path.insert(0, MCP_DIR)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from mcp_server.backtest_engine import Bar
from mcp_server.synthetic_option_pricing import (
    black_scholes_european,
    build_synthetic_option_path,
    periods_per_year_for_kline,
    synthetic_vertical_spread_path,
    year_fraction_calendar,
)


class TestSyntheticOptionPricing(unittest.TestCase):
    def test_black_scholes_put_call_parity(self) -> None:
        s, k, t, r, q, sig = 100.0, 100.0, 1.0, 0.05, 0.02, 0.25
        c = black_scholes_european(s, k, t, r, q, sig, "C")
        p = black_scholes_european(s, k, t, r, q, sig, "P")
        lhs = c - p
        rhs = s * math.exp(-q * t) - k * math.exp(-r * t)
        self.assertAlmostEqual(lhs, rhs, places=10)

    def test_at_expiry_intrinsic(self) -> None:
        c = black_scholes_european(110.0, 100.0, 0.0, 0.05, 0.0, 0.2, "call")
        self.assertAlmostEqual(c, 10.0, places=6)
        p = black_scholes_european(90.0, 100.0, 0.0, 0.05, 0.0, 0.2, "put")
        self.assertAlmostEqual(p, 10.0, places=6)

    def test_year_fraction_non_negative(self) -> None:
        a = datetime(2025, 1, 1, 10, 0, 0)
        b = datetime(2025, 1, 2, 10, 0, 0)
        self.assertGreater(year_fraction_calendar(a, b), 0)
        self.assertEqual(year_fraction_calendar(b, a), 0.0)

    def test_periods_per_year_kline(self) -> None:
        self.assertEqual(periods_per_year_for_kline("1d"), 252.0)
        self.assertGreater(periods_per_year_for_kline("1m"), periods_per_year_for_kline("1d"))

    def test_build_path_same_length_as_bars(self) -> None:
        bars: list[Bar] = []
        start = date(2025, 1, 1)
        for i in range(40):
            px = 100.0 + i * 0.1
            bars.append(
                Bar(
                    date=datetime.combine(start + timedelta(days=i), datetime.min.time()),
                    open=px,
                    high=px * 1.01,
                    low=px * 0.99,
                    close=px,
                    volume=1.0,
                )
            )
        exp = datetime.combine(start + timedelta(days=200), datetime.min.time())
        path = build_synthetic_option_path(
            bars,
            strike=100.0,
            expiry=exp,
            right="C",
            vol_window=10,
            periods_per_year=252.0,
        )
        self.assertEqual(len(path), len(bars))
        self.assertGreater(path[-1].theoretical, 0.0)

    def test_vertical_spread_matches_singles(self) -> None:
        bars: list[Bar] = []
        start = date(2025, 3, 1)
        for i in range(30):
            px = 440.0 + math.sin(i * 0.2) * 2.0
            bars.append(
                Bar(
                    date=datetime.combine(start + timedelta(days=i), datetime.min.time()),
                    open=px,
                    high=px + 0.5,
                    low=px - 0.5,
                    close=px,
                    volume=1.0,
                )
            )
        exp = datetime.combine(start + timedelta(days=60), datetime.min.time())
        rows = synthetic_vertical_spread_path(
            bars,
            long_strike=438.0,
            short_strike=442.0,
            expiry=exp,
            right="C",
            vol_window=8,
            periods_per_year=252.0,
        )
        a = build_synthetic_option_path(bars, strike=438.0, expiry=exp, right="C", vol_window=8, periods_per_year=252.0)
        b = build_synthetic_option_path(bars, strike=442.0, expiry=exp, right="C", vol_window=8, periods_per_year=252.0)
        for r, x, y in zip(rows, a, b):
            self.assertAlmostEqual(r["theoretical_spread_per_share"], x.theoretical - y.theoretical, places=5)


if __name__ == "__main__":
    unittest.main()
