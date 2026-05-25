import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP_DIR = os.path.join(ROOT, "mcp_server")
if MCP_DIR not in sys.path:
    sys.path.insert(0, MCP_DIR)

from mcp_server.strategy_qqq_0dte.matrix_runner import apply_grid_to_strategy_dict


class TestQqq0dteMatrixGrid(unittest.TestCase):
    def test_reaction_zone_width_pct_maps_half_width(self) -> None:
        cfg: dict = {}
        apply_grid_to_strategy_dict(cfg, "reaction_zone_width_pct", 0.1)
        self.assertAlmostEqual(cfg["reaction_zone_half_width_pct"], 0.001)

    def test_strategy_variant_string(self) -> None:
        cfg: dict = {}
        apply_grid_to_strategy_dict(cfg, "strategy_variant", "morning_strangle")
        self.assertEqual(cfg["strategy_variant"], "morning_strangle")

    def test_strikes_otm_int(self) -> None:
        cfg: dict = {}
        apply_grid_to_strategy_dict(cfg, "call_strikes_otm", 1.0)
        self.assertEqual(cfg["call_strikes_otm"], 1)

    def test_ui_percent_fields(self) -> None:
        cfg: dict = {}
        apply_grid_to_strategy_dict(cfg, "strangle_range_pct_ui", 0.3)
        self.assertAlmostEqual(cfg["strangle_range_pct"], 0.003)
        apply_grid_to_strategy_dict(cfg, "directional_down_pct_ui", 1.0)
        self.assertAlmostEqual(cfg["directional_down_pct"], 0.01)
        apply_grid_to_strategy_dict(cfg, "directional_up_pct_ui", 1.2)
        self.assertAlmostEqual(cfg["directional_up_pct"], 0.012)
        apply_grid_to_strategy_dict(cfg, "strangle_take_profit_return_ui", 100)
        self.assertAlmostEqual(cfg["strangle_take_profit_return"], 1.0)
        apply_grid_to_strategy_dict(cfg, "directional_take_profit_return_ui", 80)
        self.assertAlmostEqual(cfg["directional_take_profit_return"], 0.8)
        apply_grid_to_strategy_dict(cfg, "directional_stop_loss_pct_ui", 35)
        self.assertAlmostEqual(cfg["directional_stop_loss_pct"], 0.35)


if __name__ == "__main__":
    unittest.main()
