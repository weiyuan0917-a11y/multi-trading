from api.services.option_short_guard import validate_option_sell_covered


def test_blocks_uncovered_option_sell():
    result = validate_option_sell_covered(
        legs=[{"symbol": "AAPL260619C00200000", "side": "sell", "contracts": 1}],
        positions=[],
    )

    assert result["blocked"] is True
    assert result["reason"] == "option_sell_uncovered"


def test_allows_sell_to_close_with_matching_broker_position():
    result = validate_option_sell_covered(
        legs=[{"symbol": "AAPL260619C00200000", "side": "sell", "contracts": 1}],
        positions=[{"symbol": "AAPL260619C00200000", "quantity": 1}],
    )

    assert result["ok"] is True
    assert result["blocked"] is False


def test_blocks_when_sell_qty_exceeds_broker_position():
    result = validate_option_sell_covered(
        legs=[{"symbol": "AAPL260619C00200000", "side": "sell", "contracts": 2}],
        positions=[{"symbol": "AAPL260619C00200000", "quantity": 1}],
    )

    assert result["blocked"] is True
    assert result["details"][0]["missing_contracts"] == 1


def test_allows_same_order_vertical_spread_cover():
    result = validate_option_sell_covered(
        legs=[
            {"symbol": "AAPL260619C00200000", "side": "buy", "contracts": 1},
            {"symbol": "AAPL260619C00210000", "side": "sell", "contracts": 1},
        ],
        positions=[],
    )

    assert result["ok"] is True
    assert result["blocked"] is False


def test_worker_mode_can_disable_same_order_spread_cover():
    result = validate_option_sell_covered(
        legs=[
            {"symbol": "AAPL260619C00200000", "side": "buy", "contracts": 1},
            {"symbol": "AAPL260619C00210000", "side": "sell", "contracts": 1},
        ],
        positions=[],
        allow_same_order_spread_cover=False,
    )

    assert result["blocked"] is True
