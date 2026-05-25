import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP_DIR = os.path.join(ROOT, "mcp_server")
if MCP_DIR not in sys.path:
    sys.path.insert(0, MCP_DIR)

from api.auto_trader import AutoTraderService


def _make_service(send_results):
    sent_messages = []
    results = list(send_results)

    def send_feishu(text: str) -> bool:
        sent_messages.append(text)
        return results.pop(0) if results else True

    cfg_file = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    cfg_file.close()
    os.unlink(cfg_file.name)

    service = AutoTraderService(
        fetch_bars=lambda *_args, **_kwargs: [],
        quote_last=lambda *_args, **_kwargs: None,
        send_feishu=send_feishu,
        execute_trade=lambda *_args, **_kwargs: {"success": True},
        get_positions=lambda: {"positions": []},
        get_account=lambda: {"net_assets": 0, "buy_power": 0},
        config_path=cfg_file.name,
    )
    return service, sent_messages


class TestAutoTraderObserverHint(unittest.TestCase):
    def _summary(self):
        return {
            "created_signals": 0,
            "strong_count": 0,
            "skipped": {"no_signal": 1, "score_error": 0, "has_active_signal": 0, "exception": 0},
            "strong_stocks": [],
            "invalid_symbol_errors": [],
        }

    def _cfg(self):
        return {
            "observer_mode_enabled": True,
            "observer_no_signal_rounds": 10,
            "auto_execute": True,
            "pair_mode": False,
        }

    @patch("api.notification_preferences.should_send_observer_digest", return_value=True)
    def test_overdue_unsent_hint_retries_after_threshold(self, _pref):
        service, sent_messages = _make_service([True])
        service._consecutive_no_signal_rounds = 17
        service._last_observer_hint_round = 0

        sent = service._push_observer_hint_if_needed(self._summary(), self._cfg())

        self.assertTrue(sent)
        self.assertEqual(18, service._consecutive_no_signal_rounds)
        self.assertEqual(18, service._last_observer_hint_round)
        self.assertEqual(1, len(sent_messages))

    @patch("api.notification_preferences.should_send_observer_digest", return_value=True)
    def test_failed_threshold_send_retries_next_round(self, _pref):
        service, sent_messages = _make_service([False, True])
        service._consecutive_no_signal_rounds = 9

        first_sent = service._push_observer_hint_if_needed(self._summary(), self._cfg())
        second_sent = service._push_observer_hint_if_needed(self._summary(), self._cfg())

        self.assertFalse(first_sent)
        self.assertTrue(second_sent)
        self.assertEqual(11, service._last_observer_hint_round)
        self.assertEqual(2, len(sent_messages))

    @patch("api.notification_preferences.should_send_observer_digest", return_value=True)
    def test_successful_hint_is_rate_limited_by_threshold_rounds(self, _pref):
        service, sent_messages = _make_service([True, True])
        service._consecutive_no_signal_rounds = 9

        self.assertTrue(service._push_observer_hint_if_needed(self._summary(), self._cfg()))
        self.assertFalse(service._push_observer_hint_if_needed(self._summary(), self._cfg()))

        service._consecutive_no_signal_rounds = 19
        self.assertTrue(service._push_observer_hint_if_needed(self._summary(), self._cfg()))
        self.assertEqual(20, service._last_observer_hint_round)
        self.assertEqual(2, len(sent_messages))


if __name__ == "__main__":
    unittest.main()
