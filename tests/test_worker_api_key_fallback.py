import json

from api import qqq_0dte_live_worker as qqq_worker
from api import stock_options_swing_worker as swing_worker


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def test_qqq_worker_migrates_matching_legacy_api_key(tmp_path, monkeypatch):
    owner_cfg = tmp_path / "data" / "owners" / "davies" / "qqq_0dte" / "live_worker_config.json"
    legacy_cfg = tmp_path / "data" / "qqq_0dte" / "live_worker_config.json"
    _write_json(owner_cfg, {"enabled": True})
    _write_json(legacy_cfg, {"api_key": "legacy-key"})

    monkeypatch.setattr(qqq_worker, "ROOT", str(tmp_path))
    monkeypatch.setattr(qqq_worker, "_WORKER_INSTANCE", "0dte")
    monkeypatch.setattr(qqq_worker, "_API_LOCAL_OWNER", "davies")
    monkeypatch.setenv("QQQ_LIVE_API_KEY", "")
    monkeypatch.setenv("QQQ_0DTE_LIVE_API_KEY", "")
    monkeypatch.setattr(
        qqq_worker,
        "_api_key_owner_matches",
        lambda key, owner: key == "legacy-key" and owner == "davies",
    )

    assert qqq_worker._resolve_api_key({}, str(owner_cfg)) == "legacy-key"
    migrated = json.loads(owner_cfg.read_text(encoding="utf-8"))
    assert migrated["api_key"] == "legacy-key"
    assert migrated["api_key_migrated_from_legacy_config"].replace("/", "\\") == "data\\qqq_0dte\\live_worker_config.json"


def test_qqq_worker_rejects_legacy_api_key_for_other_owner(tmp_path, monkeypatch):
    owner_cfg = tmp_path / "data" / "owners" / "davies" / "qqq_0dte" / "live_worker_config.json"
    legacy_cfg = tmp_path / "data" / "qqq_0dte" / "live_worker_config.json"
    _write_json(owner_cfg, {"enabled": True})
    _write_json(legacy_cfg, {"api_key": "other-owner-key"})

    monkeypatch.setattr(qqq_worker, "ROOT", str(tmp_path))
    monkeypatch.setattr(qqq_worker, "_WORKER_INSTANCE", "0dte")
    monkeypatch.setattr(qqq_worker, "_API_LOCAL_OWNER", "davies")
    monkeypatch.setenv("QQQ_LIVE_API_KEY", "")
    monkeypatch.setenv("QQQ_0DTE_LIVE_API_KEY", "")
    monkeypatch.setattr(qqq_worker, "_api_key_owner_matches", lambda _key, _owner: False)

    assert qqq_worker._resolve_api_key({}, str(owner_cfg)) == ""
    migrated = json.loads(owner_cfg.read_text(encoding="utf-8"))
    assert "api_key" not in migrated


def test_stock_options_swing_migrates_matching_qqq_1dte_key(tmp_path, monkeypatch):
    owner_cfg = tmp_path / "data" / "owners" / "davies" / "stock_options_swing" / "live_worker_config.json"
    legacy_cfg = tmp_path / "data" / "qqq_1dte" / "live_worker_config.json"
    _write_json(owner_cfg, {"enabled": True})
    _write_json(legacy_cfg, {"api_key": "legacy-key"})

    monkeypatch.setattr(swing_worker, "ROOT", str(tmp_path))
    monkeypatch.setattr(swing_worker, "_API_LOCAL_OWNER", "davies")
    monkeypatch.setenv("STOCK_OPTIONS_SWING_API_KEY", "")
    monkeypatch.setenv("QQQ_LIVE_API_KEY", "")
    monkeypatch.setattr(
        swing_worker,
        "_api_key_owner_matches",
        lambda key, owner: key == "legacy-key" and owner == "davies",
    )

    assert swing_worker._resolve_api_key({}, str(owner_cfg)) == "legacy-key"
    migrated = json.loads(owner_cfg.read_text(encoding="utf-8"))
    assert migrated["api_key"] == "legacy-key"
    assert migrated["api_key_migrated_from_legacy_config"].replace("/", "\\") == "data\\qqq_1dte\\live_worker_config.json"
