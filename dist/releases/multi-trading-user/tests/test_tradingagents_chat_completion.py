import json
import os
import sys
import types
import unittest
from unittest.mock import patch

longbridge_mod = types.ModuleType("longbridge")
openapi_mod = types.ModuleType("longbridge.openapi")
dummy = type("_LongbridgeDummy", (), {})
for name in [
    "AdjustType",
    "Config",
    "OrderSide",
    "OrderType",
    "Period",
    "QuoteContext",
    "TimeInForceType",
    "TradeContext",
    "TradeSessions",
]:
    setattr(openapi_mod, name, dummy)
with patch.dict(sys.modules, {"longbridge": longbridge_mod, "longbridge.openapi": openapi_mod}):
    import api.main as main


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps({"choices": [{"message": {"content": "今天偏中性，等待突破确认。"}}]}).encode("utf-8")


class TradingAgentsChatCompletionTests(unittest.TestCase):
    def test_openai_compatible_chat_completion(self) -> None:
        captured = {}

        def fake_urlopen(req, timeout=0):
            captured["url"] = req.full_url
            captured["timeout"] = timeout
            captured["body"] = json.loads(req.data.decode("utf-8"))
            captured["auth"] = req.headers.get("Authorization")
            return _FakeResponse()

        cfg = {
            "provider": "openai",
            "api_key": "test-key",
            "base_url": "https://example.test/v1",
            "model": "gpt-test-mini",
            "timeout": 20,
        }
        with patch.object(main, "_tradingagents_chat_model_config", return_value=cfg), patch.object(main.urllib.request, "urlopen", fake_urlopen):
            answer = main._call_tradingagents_chat_completion(
                symbol="NVDA.US",
                market="us",
                user_question="今天看涨还是看跌？",
                report_markdown="# report",
                action="hold",
                confidence=0.55,
            )

        self.assertEqual(answer, "今天偏中性，等待突破确认。")
        self.assertEqual(captured["url"], "https://example.test/v1/chat/completions")
        self.assertEqual(captured["body"]["model"], "gpt-test-mini")
        self.assertEqual(captured["body"]["messages"][1]["role"], "user")
        self.assertIn("今天看涨还是看跌", captured["body"]["messages"][1]["content"])
        self.assertEqual(captured["auth"], "Bearer test-key")

    def test_missing_key_returns_none(self) -> None:
        cfg = {
            "provider": "openai",
            "api_key": "",
            "base_url": "https://example.test/v1",
            "model": "gpt-test-mini",
            "timeout": 20,
        }
        with patch.object(main, "_tradingagents_chat_model_config", return_value=cfg):
            answer = main._call_tradingagents_chat_completion(
                symbol="NVDA.US",
                market="us",
                user_question="今天看涨还是看跌？",
                report_markdown="# report",
            )
        self.assertIsNone(answer)

    def test_cn_chat_prompt_requires_public_v2_evidence(self) -> None:
        captured = {}

        def fake_urlopen(req, timeout=0):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeResponse()

        cfg = {
            "provider": "openai",
            "api_key": "test-key",
            "base_url": "https://example.test/v1",
            "model": "gpt-test-mini",
            "timeout": 20,
        }
        with patch.object(main, "_tradingagents_chat_model_config", return_value=cfg), patch.object(main.urllib.request, "urlopen", fake_urlopen):
            answer = main._call_tradingagents_chat_completion(
                symbol="603776.SH",
                market="cn",
                user_question="永安行怎么看？",
                report_markdown="# report\n\n## Fundamental snapshot v2\n\n## 事件摘要\n\n## 公司公告\n\n## 数据源诊断",
            )

        self.assertEqual(answer, "今天偏中性，等待突破确认。")
        self.assertIn("Fundamental snapshot v2", captured["body"]["messages"][0]["content"])
        self.assertIn("公司公告", captured["body"]["messages"][0]["content"])

    def test_agent_event_recorder_updates_task_view(self) -> None:
        task_id = "ta_test_events"
        main._TRADINGAGENTS_TASKS[task_id] = {"task_id": task_id, "status": "running"}
        try:
            main._record_tradingagents_agent_event(
                task_id,
                {
                    "kind": "agent_status",
                    "agent": "Market Analyst",
                    "team": "Analyst Team",
                    "status": "in_progress",
                    "ts": "2026-05-12T00:00:00",
                },
            )
            main._record_tradingagents_agent_event(
                task_id,
                {
                    "kind": "report_section",
                    "agent": "Market Analyst",
                    "section": "market_report",
                    "content": "Market report text",
                    "ts": "2026-05-12T00:00:01",
                },
            )
            view = main._tradingagents_task_status_view(main._TRADINGAGENTS_TASKS[task_id])
        finally:
            main._TRADINGAGENTS_TASKS.pop(task_id, None)

        self.assertEqual(view["agent_statuses"]["Market Analyst"]["status"], "in_progress")
        self.assertEqual(view["latest_report_section"]["section"], "market_report")
        self.assertEqual(len(view["agent_events"]), 2)


if __name__ == "__main__":
    unittest.main()
