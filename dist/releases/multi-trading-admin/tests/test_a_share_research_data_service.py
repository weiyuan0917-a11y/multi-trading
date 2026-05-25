import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from api.services import a_share_research_data_service as svc_mod
from api.services.a_share_research_data_service import AShareResearchDataService


class TestAShareResearchDataService(unittest.TestCase):
    def test_news_report_merges_public_sources_and_historical_reports(self):
        svc = AShareResearchDataService()

        def fake_ak(fn_name, *args, **kwargs):
            if fn_name == "stock_news_em":
                return [
                    {
                        "新闻标题": "永安行2026年一季报净利润为-510.46万元",
                        "新闻内容": "公司发布2026年一季报。",
                        "发布时间": "2026-04-28 09:39:39",
                        "文章来源": "界面新闻",
                        "新闻链接": "https://example.test/news/1",
                    }
                ]
            if fn_name == "stock_zh_a_disclosure_report_cninfo":
                return [
                    {
                        "公告标题": "关于永安行科技股份有限公司向特定对象发行A股股票申请文件的审核问询函的回复",
                        "公告时间": "2026-05-16",
                        "公告链接": "https://example.test/notice/1",
                    }
                ]
            if fn_name == "stock_individual_notice_report":
                return [
                    {
                        "公告标题": "永安行:关于永安行科技股份有限公司向特定对象发行A股股票申请文件的审核问询函的回复",
                        "公告日期": "2026-05-16",
                        "公告类型": "回复问询函公告",
                    }
                ]
            if fn_name == "stock_research_report_em":
                return [
                    {
                        "报告名称": "新股询价报告",
                        "机构": "华鑫证券",
                        "日期": "2017-04-27",
                        "报告PDF链接": "https://example.test/report.pdf",
                    }
                ]
            return []

        with patch.object(svc, "_ak_records", side_effect=fake_ak):
            report = svc.build_news_report("603776.SH", "2026-04-18", "2026-05-18", limit=8)

        self.assertIn("EastMoney news / CNInfo disclosure", report)
        self.assertIn("2026年一季报", report)
        self.assertIn("cninfo_disclosure", report)
        self.assertIn("融资 / 监管风险", report)
        self.assertIn("新股询价报告", report)

    def test_fundamental_snapshot_v2_and_cache_payload(self):
        svc = AShareResearchDataService()

        def fake_ak(fn_name, *args, **kwargs):
            if fn_name == "stock_individual_info_em":
                return [{"item": "股票简称", "value": "永安行"}]
            if fn_name == "stock_financial_analysis_indicator_em":
                return [
                    {
                        "SECUCODE": "603776.SH",
                        "SECURITY_NAME_ABBR": "永安行",
                        "REPORT_DATE": "2026-03-31",
                        "REPORT_DATE_NAME": "2026一季报",
                        "EPSJB": -0.02,
                        "BPS": 12.193288,
                        "TOTALOPERATEREVE": 121451459.29,
                        "PARENTNETPROFIT": -5104561.53,
                        "ROEJQ": -0.149005,
                        "XSMLL": 3.734591,
                        "ZCFZL": 16.532862,
                    }
                ]
            if fn_name == "stock_financial_abstract":
                return [
                    {"选项": "常用指标", "指标": "营业总收入", "20260331": 121451459.29},
                    {"选项": "常用指标", "指标": "归母净利润", "20260331": -5104561.53},
                    {"选项": "常用指标", "指标": "资产负债率", "20260331": 16.532862},
                ]
            if fn_name == "stock_zygc_em":
                return [
                    {
                        "报告日期": "2025-12-31",
                        "分类类型": "按行业分类",
                        "主营构成": "公共自行车和共享出行",
                        "主营收入": 268600607.72,
                        "收入比例": 0.652578,
                        "毛利率": -0.190829,
                    }
                ]
            return []

        with tempfile.TemporaryDirectory() as td:
            with (
                patch.object(svc_mod, "_SNAPSHOT_CACHE_DIR", td),
                patch.object(svc, "_ak_records", side_effect=fake_ak),
                patch.object(svc, "_public_quote", return_value={"last": 21.75, "source": "mootdx"}),
                patch.object(svc, "_public_valuation", return_value={"pe_ttm": -29.35, "pb": 1.78, "total_market_cap": 61.07}),
            ):
                report = svc.build_fundamentals_report("603776.SH")
                snap = svc.build_public_research_snapshot("603776.SH", reason="unit_test")

        self.assertIn("Fundamental snapshot v2", report)
        self.assertIn("2026一季报", report)
        self.assertIn("65.26%", report)
        self.assertEqual("a_share_research_data.v2", snap["data_diagnostics"]["schema"])
        self.assertEqual("2026一季报", snap["fundamental_snapshot_v2"]["latest_period"])


if __name__ == "__main__":
    unittest.main()
