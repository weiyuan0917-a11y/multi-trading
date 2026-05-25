import importlib


def test_feishu_billing_orders_command_lists_pending(monkeypatch):
    bot = importlib.import_module("mcp_server.feishu_command_bot")

    def fake_http(path, method="GET", payload=None, params=None):
        assert path == "/billing/manual-orders"
        assert method == "GET"
        assert params["status"] == "pending"
        return True, {
            "rows": [
                {
                    "orderNo": "MT20260524ABCDEF",
                    "status": "pending",
                    "ownerId": "davies",
                    "plan": "premium",
                    "billingCycle": "month",
                    "amount": 199,
                    "currency": "CNY",
                }
            ]
        }

    monkeypatch.setattr(bot, "_billing_command_allowed", lambda **_kwargs: True)
    monkeypatch.setattr(bot, "_billing_http_json", fake_http)

    result = bot.dispatch_command("付款订单 pending", chat_id="chat", sender_id="user")

    assert "MT20260524ABCDEF" in result
    assert "davies" in result
    assert "CNY 199" in result


def test_feishu_confirm_payment_issues_license(monkeypatch):
    bot = importlib.import_module("mcp_server.feishu_command_bot")
    calls = []

    def fake_http(path, method="GET", payload=None, params=None):
        calls.append((path, method, payload, params))
        if path == "/billing/manual-orders":
            return True, {
                "rows": [
                    {
                        "id": "order-id-1",
                        "orderNo": "MT20260524ABCDEF",
                        "status": "pending",
                        "ownerId": "davies",
                        "plan": "premium",
                        "billingCycle": "month",
                        "amount": 199,
                        "currency": "CNY",
                    }
                ]
            }
        if path == "/billing/manual-order-admin":
            assert method == "POST"
            assert payload["action"] == "confirm"
            assert payload["orderId"] == "order-id-1"
            assert payload["paymentReference"] == "WX123"
            return True, {
                "ok": True,
                "deliveryId": "delivery-id-1",
                "currentPeriodEnd": 1790000000000,
                "emailStatus": "sent",
                "order": {
                    "orderNo": "MT20260524ABCDEF",
                    "ownerId": "davies",
                    "plan": "premium",
                    "billingCycle": "month",
                    "amount": 199,
                    "currency": "CNY",
                },
            }
        raise AssertionError(path)

    monkeypatch.setattr(bot, "_billing_command_allowed", lambda **_kwargs: True)
    monkeypatch.setattr(bot, "_billing_http_json", fake_http)

    result = bot.dispatch_command("确认收款 MT20260524ABCDEF WX123", chat_id="chat", sender_id="user")

    assert "已确认收款并发证" in result
    assert "delivery-id-1" in result
    assert "邮件状态：sent" in result
    assert [call[0] for call in calls] == ["/billing/manual-orders", "/billing/manual-order-admin"]


def test_feishu_billing_command_acl_blocks(monkeypatch):
    bot = importlib.import_module("mcp_server.feishu_command_bot")
    monkeypatch.setattr(bot, "_billing_command_allowed", lambda **_kwargs: False)

    result = bot.dispatch_command("确认收款 MT20260524ABCDEF WX123", chat_id="chat", sender_id="user")

    assert "付款订单指令未授权" in result
