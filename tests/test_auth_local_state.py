from api.services import user_auth_service as uas


def test_local_state_reports_missing_users_file(tmp_path, monkeypatch):
    auth_dir = tmp_path / "auth"
    monkeypatch.setattr(uas, "_AUTH_DATA_DIR", str(auth_dir))
    monkeypatch.setattr(uas, "_AUTH_USERS_FILE", str(auth_dir / "users.json"))
    monkeypatch.setattr(uas, "_AUTH_API_KEYS_FILE", str(auth_dir / "api_keys.json"))
    monkeypatch.setattr(uas, "_AUTH_SESSIONS_FILE", str(auth_dir / "sessions.json"))

    svc = uas.UserAuthService()
    state = svc.local_state("alice")

    assert state["ok"] is True
    assert state["users_file_exists"] is False
    assert state["user_count"] == 0
    assert state["username_exists"] is False


def test_me_marks_missing_user_record_for_persisted_session(tmp_path, monkeypatch):
    auth_dir = tmp_path / "auth"
    monkeypatch.setattr(uas, "_AUTH_DATA_DIR", str(auth_dir))
    monkeypatch.setattr(uas, "_AUTH_USERS_FILE", str(auth_dir / "users.json"))
    monkeypatch.setattr(uas, "_AUTH_API_KEYS_FILE", str(auth_dir / "api_keys.json"))
    monkeypatch.setattr(uas, "_AUTH_SESSIONS_FILE", str(auth_dir / "sessions.json"))

    svc = uas.UserAuthService()
    registered = svc.register("alice", "secret1")
    token = registered["token"]
    (auth_dir / "users.json").unlink()

    me = svc.me(token)

    assert me["ok"] is True
    assert me["user"]["username"] == "alice"
    assert me["user_record_missing"] is True
