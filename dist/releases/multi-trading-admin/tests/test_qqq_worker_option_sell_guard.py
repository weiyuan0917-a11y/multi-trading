from unittest.mock import patch

from api import qqq_0dte_live_worker as worker


def test_worker_preflight_blocks_manual_closed_sell_leg():
    with patch.object(worker, "_api_get_option_positions_checked", return_value=([], None)):
        ok, detail = worker._preflight_sell_legs_against_broker_positions(
            [{"symbol": "AAPL260619C00200000", "side": "sell", "contracts": 1, "price": 1.2}],
            raw={},
        )

    assert ok is False
    assert detail["error"] == "manual_close_detected_or_position_insufficient"
    assert detail["details"][0]["broker_quantity"] == 0


def test_worker_preflight_allows_matching_broker_position():
    positions = [{"symbol": "AAPL260619C00200000", "quantity": 1}]
    with patch.object(worker, "_api_get_option_positions_checked", return_value=(positions, None)):
        ok, detail = worker._preflight_sell_legs_against_broker_positions(
            [{"symbol": "AAPL260619C00200000", "side": "sell", "contracts": 1, "price": 1.2}],
            raw={},
        )

    assert ok is True
    assert detail["positions_checked"] is True
