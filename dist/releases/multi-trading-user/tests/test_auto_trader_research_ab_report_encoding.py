import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP_DIR = os.path.join(ROOT, "mcp_server")
if MCP_DIR not in sys.path:
    sys.path.insert(0, MCP_DIR)

from api import auto_trader_research as research


class TestAutoTraderResearchABReportEncoding(unittest.TestCase):
    def test_ab_report_markdown_keeps_chinese_literals(self):
        md = research._ab_report_markdown(
            {
                "generated_at": "2026-05-11T09:13:23",
                "summary": {
                    "top5_baseline": ["AAPL"],
                    "top5_with_factor": ["MSFT"],
                    "overlap_count": 0,
                    "entered_symbols": ["MSFT"],
                    "exited_symbols": ["AAPL"],
                    "avg_best_score_baseline": 1.0,
                    "avg_best_score_with_factor": 2.0,
                    "avg_best_score_delta": 1.0,
                    "allocation_turnover": 0.5,
                },
                "items": [],
            }
        )

        self.assertIn("# AutoTrader 因子 A/B 报告（最小版）", md)
        self.assertIn("生成时间：2026-05-11T09:13:23", md)
        self.assertIn("关键权重变化（Top 10）", md)
        self.assertNotIn("å›", md)
        self.assertNotIn("鍥", md)

    def test_ab_report_file_is_utf8_sig_for_windows_viewers(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, ".auto_trader_research.ab_report.md")
            research._write_text(path, "# AutoTrader 因子 A/B 报告（最小版）")

            with open(path, "rb") as f:
                raw = f.read()

        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        self.assertIn("因子".encode("utf-8"), raw)
        self.assertEqual(
            "# AutoTrader 因子 A/B 报告（最小版）",
            raw.decode("utf-8-sig").splitlines()[0],
        )


if __name__ == "__main__":
    unittest.main()
