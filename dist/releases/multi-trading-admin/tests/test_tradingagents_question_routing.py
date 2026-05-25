import unittest

from api.research_data_provider import TradingAgentsClient


class TradingAgentsQuestionRoutingTests(unittest.TestCase):
    def test_freeform_news_question_routes_to_news_only(self) -> None:
        tags = TradingAgentsClient.infer_template_ids_from_question("NVDA 最近有什么新闻催化？")
        self.assertEqual(tags, ["news"])
        analysts = TradingAgentsClient._effective_analysts_for_templates(tags, [])
        self.assertEqual(analysts, ["news"])
        visibility = TradingAgentsClient._report_section_visibility(tags, analysts)
        self.assertTrue(visibility["analyst_news"])
        self.assertFalse(visibility["analyst_market"])
        self.assertFalse(visibility["analyst_fundamentals"])

    def test_freeform_position_question_keeps_answer_focused_on_plan(self) -> None:
        tags = TradingAgentsClient.infer_template_ids_from_question("现在可以买入吗？仓位和止损怎么设？")
        self.assertIn("position", tags)
        self.assertIn("risk", tags)
        analysts = TradingAgentsClient._effective_analysts_for_templates(tags, [])
        self.assertEqual(analysts, ["market"])
        visibility = TradingAgentsClient._report_section_visibility(tags, analysts)
        self.assertTrue(visibility["trading"])
        self.assertTrue(visibility["portfolio"])
        self.assertTrue(visibility["risk"])
        self.assertFalse(visibility["analyst_news"])

    def test_no_tags_means_focused_market_not_full_report(self) -> None:
        analysts = TradingAgentsClient._effective_analysts_for_templates([], [])
        self.assertEqual(analysts, ["market"])
        visibility = TradingAgentsClient._report_section_visibility([], analysts)
        self.assertEqual(visibility["mode"], "selective")
        self.assertTrue(visibility["analyst_market"])
        self.assertFalse(visibility["risk"])

    def test_direction_question_routes_to_market_view(self) -> None:
        tags = TradingAgentsClient.infer_template_ids_from_question("今天看涨还是看跌？")
        self.assertEqual(tags, ["mkt"])
        analysts = TradingAgentsClient._effective_analysts_for_templates(tags, [])
        self.assertEqual(analysts, ["market"])

    def test_cn_public_template_forces_core_analysts_and_sections(self) -> None:
        analysts = TradingAgentsClient._ensure_cn_public_analysts(["market"])
        self.assertEqual(analysts, ["market", "news", "fundamentals"])

        tags = TradingAgentsClient._ensure_cn_public_template_ids(["short"])
        self.assertEqual(tags, ["short", "mkt", "news", "fund"])

        prompt = TradingAgentsClient._a_share_public_agent_prompt(
            symbol="603776.SH",
            request_symbol="603776.SH",
            user_question="永安行怎么看？",
        )
        self.assertIn("Fundamental snapshot v2", prompt)
        self.assertIn("事件摘要", prompt)
        self.assertIn("公司公告", prompt)
        self.assertIn("数据源诊断", prompt)

        contract = TradingAgentsClient._cn_public_tool_contract("get_fundamentals")
        self.assertIn("Fundamental snapshot v2", contract)
        self.assertIn("公司公告", contract)
        self.assertIn("数据源诊断", contract)

    def test_cn_public_stage_reports_merge_v2_context(self) -> None:
        snapshot = {
            "symbol": "603776.SH",
            "stage_reports": {
                "analyst_market": "# 行情",
                "analyst_news": "## 事件摘要\n\n- 公告A",
                "analyst_fundamentals": "## Fundamental snapshot v2\n\n- 最新财报期: 2026一季报",
            },
            "fundamental_snapshot_v2": {"latest_period": "2026一季报"},
            "data_diagnostics": {
                "news_item_count": 5,
                "event_item_count": 2,
                "fundamentals": [{"source": "eastmoney_indicators", "count": 4, "ok": True}],
                "news": [{"source": "cninfo_disclosure", "count": 2, "ok": True}],
            },
        }
        merged = TradingAgentsClient._augment_cn_public_stage_reports({"analyst_news": "TA news"}, snapshot)
        self.assertIn("A股公共行情补强", merged["analyst_market"])
        self.assertIn("A股事件摘要、公告与新闻补强", merged["analyst_news"])
        self.assertIn("Fundamental snapshot v2", merged["analyst_fundamentals"])
        self.assertIn("A股公共数据上下文 v2", merged["cn_public_context"])


if __name__ == "__main__":
    unittest.main()
