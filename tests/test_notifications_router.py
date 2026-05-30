import json
from pathlib import Path
from typing import Any

from api.routers import notifications


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self) -> dict[str, Any]:
        return self._payload


def _write_notification_config(root: Path, data: dict[str, Any], *, bom: bool = False) -> None:
    mcp_dir = root / "mcp_server"
    mcp_dir.mkdir(parents=True, exist_ok=True)
    encoding = "utf-8-sig" if bom else "utf-8"
    (mcp_dir / "notification_config.json").write_text(json.dumps(data, ensure_ascii=False), encoding=encoding)


def test_notifications_status_reads_bom_config_and_counts_app_bot(tmp_path, monkeypatch):
    monkeypatch.setattr(notifications, "MCP_DIR", str(tmp_path / "mcp_server"))
    for key in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_SCHEDULED_CHAT_ID"):
        monkeypatch.delenv(key, raising=False)
    _write_notification_config(
        tmp_path,
        {
            "feishu_app": {"app_id": "app-id", "app_secret": "app-secret", "scheduled_chat_id": "chat-id"},
            "feishu_bots": [{"webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/unit"}],
        },
        bom=True,
    )

    result = notifications.notifications_status()

    assert result["feishu_app_configured"] is True
    assert result["scheduled_chat_id_configured"] is True
    assert result["feishu_webhook_bots_count"] == 1
    assert result["feishu_app_bots_count"] == 1
    assert result["feishu_bots_count"] == 2
    assert result["feishu_push_targets_count"] == 2


def test_notifications_test_feishu_uses_app_chat_without_real_network(tmp_path, monkeypatch):
    monkeypatch.setattr(notifications, "MCP_DIR", str(tmp_path / "mcp_server"))
    for key in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_SCHEDULED_CHAT_ID"):
        monkeypatch.delenv(key, raising=False)
    _write_notification_config(
        tmp_path,
        {"feishu_app": {"app_id": "app-id", "app_secret": "app-secret", "scheduled_chat_id": "chat-id"}},
    )
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, **kwargs):
        calls.append({"url": url, **kwargs})
        if "tenant_access_token" in url:
            return _FakeResponse({"code": 0, "tenant_access_token": "tenant-token"})
        return _FakeResponse({"code": 0, "msg": "ok"})

    monkeypatch.setattr(notifications.requests, "post", fake_post)

    result = notifications.notifications_test_feishu({"message": "hello"})

    assert result["ok"] is True
    assert result["targets"] == [
        {"kind": "app_chat", "ok": True, "stage": "message", "status_code": 200, "code": 0, "message": "ok"}
    ]
    assert calls[0]["json"] == {"app_id": "app-id", "app_secret": "app-secret"}
    assert calls[1]["headers"]["Authorization"] == "Bearer tenant-token"
    assert calls[1]["json"]["receive_id"] == "chat-id"
