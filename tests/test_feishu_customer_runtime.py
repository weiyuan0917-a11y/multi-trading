import json
import os

from api.notification_preferences import save_notification_preferences
from api.services import setup_process_control_service as svc


class _FakeProcess:
    def poll(self):
        return None


def test_feishu_bot_uses_backend_worker_in_customer_runtime(tmp_path, monkeypatch):
    root = tmp_path
    backend = root / "Backend.exe"
    backend.write_text("", encoding="utf-8")
    mcp_dir = root / "mcp_server"
    calls = []

    def fake_popen(cmd, cwd, env, **kwargs):
        calls.append({"cmd": cmd, "cwd": cwd, "env": env, "kwargs": kwargs})
        return _FakeProcess()

    monkeypatch.setattr(svc.subprocess, "Popen", fake_popen)

    result = svc.start_services(
        start_feishu_bot=True,
        enable_auto_trader=False,
        enable_qqq_0dte_live=False,
        enable_qqq_1dte_live=False,
        enable_stock_options_swing=False,
        auto_trader=object(),
        start_auto_trader_worker=lambda owner_id=None: "unexpected",
        start_qqq_0dte_live_worker=lambda owner_id=None: "unexpected",
        start_qqq_1dte_live_worker=lambda owner_id=None: "unexpected",
        start_stock_options_swing_worker=lambda owner_id=None: "unexpected",
        managed_processes={},
        root=str(root),
        mcp_dir=str(mcp_dir),
        win_subprocess_silent_kwargs=lambda: {},
        owner_id="davies",
    )

    assert result["started"]["feishu_bot"] == "started"
    assert calls[0]["cmd"] == [str(backend), "--worker=feishu_command_bot"]
    assert calls[0]["cwd"] == str(root)
    assert calls[0]["env"]["MULTITRADING_ROOT"] == str(root)
    assert os.path.isdir(mcp_dir)


def test_notification_preferences_create_customer_mcp_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MULTITRADING_ROOT", str(tmp_path))

    prefs = save_notification_preferences({"bottom_reversal_watch": {"enabled": True, "symbols": ["QQQ.US"]}})

    path = tmp_path / "mcp_server" / "notification_config.json"
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["notification_preferences"]["bottom_reversal_watch"]["enabled"] is True
    assert prefs["bottom_reversal_watch"]["symbols"] == ["QQQ.US"]
