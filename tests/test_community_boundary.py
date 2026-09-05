from fastapi.testclient import TestClient

from api.main import app
from mcp_server.community_mcp_server import list_community_tools


def test_community_mcp_tool_list_excludes_real_execution() -> None:
    forbidden = ("broker", "live", "cancel", "credential", "license")
    tools = list_community_tools()
    assert "paper_submit_order" in tools
    assert "submit_" + "stock_order" not in tools
    assert all(not any(marker in name for marker in forbidden) for name in tools)


def test_paper_order_is_simulated() -> None:
    client = TestClient(app)
    response = client.post(
        "/paper/orders",
        json={
            "symbol": "AAPL", "asset_class": "stock", "side": "buy", "quantity": 1,
            "reference_price": 100, "strategy": "unit-test", "execution_mode": "paper",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "simulated_fill"


def test_rehearsal_is_not_a_live_mode() -> None:
    client = TestClient(app)
    response = client.post(
        "/research/execution-intents",
        json={
            "symbol": "QQQ", "asset_class": "option", "side": "buy", "quantity": 1,
            "reference_price": 10, "strategy": "research", "execution_mode": "rehearsal",
        },
    )
    assert response.status_code == 200
    assert response.json()["execution_mode"] == "rehearsal"
