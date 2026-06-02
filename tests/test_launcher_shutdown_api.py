from unittest.mock import patch

from fastapi.testclient import TestClient

from api.main import app


def test_launcher_shutdown_rejects_missing_token(monkeypatch):
    monkeypatch.setenv("MULTITRADING_LAUNCHER_SHUTDOWN_TOKEN", "secret-token")
    client = TestClient(app)

    response = client.post("/internal/launcher/shutdown", json={})

    assert response.status_code == 403


def test_launcher_shutdown_uses_internal_token(monkeypatch):
    monkeypatch.setenv("MULTITRADING_LAUNCHER_SHUTDOWN_TOKEN", "secret-token")
    client = TestClient(app)

    with patch("api.runtime_bridge.setup_stop_all_services") as stop_all:
        stop_all.return_value = {"ok": True, "stopped": {"backend": "scheduled_shutdown"}}
        response = client.post(
            "/internal/launcher/shutdown",
            json={"stop_backend": True},
            headers={"X-MT-Launcher-Token": "secret-token"},
        )

    assert response.status_code == 200
    assert response.json()["launcher_shutdown"] is True
    stop_all.assert_called_once()
    payload = stop_all.call_args.args[0]
    assert payload["stop_backend"] is True
    assert payload["stop_auto_trader"] is True
    assert payload["stop_qqq_0dte_live"] is True
    assert payload["stop_qqq_1dte_live"] is True
    assert payload["stop_stock_options_swing"] is True
    assert stop_all.call_args.kwargs["owner_id"] is None


def test_launcher_shutdown_accepts_runtime_token_file(monkeypatch, tmp_path):
    monkeypatch.delenv("MULTITRADING_LAUNCHER_SHUTDOWN_TOKEN", raising=False)
    monkeypatch.setenv("MULTITRADING_RUNTIME_DIR", str(tmp_path))
    (tmp_path / "launcher_shutdown.token").write_text("file-token\n", encoding="utf-8")
    client = TestClient(app)

    with patch("api.runtime_bridge.setup_stop_all_services") as stop_all:
        stop_all.return_value = {"ok": True, "stopped": {"backend": "scheduled_shutdown"}}
        response = client.post(
            "/internal/launcher/shutdown",
            json={},
            headers={"X-MT-Launcher-Token": "file-token"},
        )

    assert response.status_code == 200
    stop_all.assert_called_once()
