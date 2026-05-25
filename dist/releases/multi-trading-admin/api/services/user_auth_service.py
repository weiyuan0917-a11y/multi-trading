from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_AUTH_DATA_DIR = os.path.join(_ROOT, "data", "auth")
_AUTH_USERS_FILE = os.path.join(_AUTH_DATA_DIR, "users.json")
_AUTH_API_KEYS_FILE = os.path.join(_AUTH_DATA_DIR, "api_keys.json")
_LOCK = threading.RLock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _load_users_file() -> dict[str, Any]:
    if not os.path.isfile(_AUTH_USERS_FILE):
        return {"users": []}
    try:
        with open(_AUTH_USERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("users"), list):
            return data
    except Exception:
        pass
    return {"users": []}


def _save_users_file(data: dict[str, Any]) -> None:
    _ensure_parent_dir(_AUTH_USERS_FILE)
    tmp = _AUTH_USERS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, _AUTH_USERS_FILE)


def _load_api_keys_file() -> dict[str, Any]:
    if not os.path.isfile(_AUTH_API_KEYS_FILE):
        return {"keys": []}
    try:
        with open(_AUTH_API_KEYS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("keys"), list):
            return data
    except Exception:
        pass
    return {"keys": []}


def _save_api_keys_file(data: dict[str, Any]) -> None:
    _ensure_parent_dir(_AUTH_API_KEYS_FILE)
    tmp = _AUTH_API_KEYS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, _AUTH_API_KEYS_FILE)


def _hash_api_key_plaintext(plaintext: str) -> str:
    return hashlib.sha256(str(plaintext or "").encode("utf-8")).hexdigest()


def _normalize_username(username: str) -> str:
    return str(username or "").strip().lower()


def _normalize_plan(plan: Any) -> str:
    raw = str(plan or "").strip().lower()
    if raw == "premium":
        return "premium"
    if raw == "pro":
        return "pro"
    return "free"


def _normalize_role(role: Any) -> str:
    raw = str(role or "").strip().lower()
    if raw in {"admin", "owner"}:
        return raw
    return "user"


def _public_user(row: dict[str, Any] | None, fallback_username: str = "") -> dict[str, Any]:
    source = row if isinstance(row, dict) else {}
    username = _normalize_username(str(source.get("username") or fallback_username))
    plan = _normalize_plan(source.get("plan"))
    role = _normalize_role(source.get("role"))
    is_admin = bool(source.get("is_admin")) or role in {"admin", "owner"}
    return {
        "username": username,
        "plan": plan,
        "role": "admin" if is_admin and role == "user" else role,
        "is_admin": is_admin,
    }


def _hash_password(password: str, salt_raw: bytes | None = None) -> tuple[str, str]:
    salt = salt_raw or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return (
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(dk).decode("ascii"),
    )


def _verify_password(password: str, salt_b64: str, hash_b64: str) -> bool:
    try:
        salt = base64.b64decode(salt_b64.encode("ascii"))
        _, expected_hash = _hash_password(password, salt)
        return hmac.compare_digest(expected_hash, hash_b64)
    except Exception:
        return False


@dataclass
class SessionInfo:
    token: str
    username: str
    created_at: str


class UserAuthService:
    """
    轻量认证服务（本地 users.json + 内存 session）。
    说明：session 进程重启会失效，用户需重新登录。
    """

    def __init__(self) -> None:
        self._sessions: dict[str, SessionInfo] = {}
        self._sessions_lock = threading.RLock()

    def register(self, username: str, password: str) -> dict[str, Any]:
        name = _normalize_username(username)
        if not name:
            raise ValueError("username_required")
        if len(password or "") < 6:
            raise ValueError("password_too_short")
        with _LOCK:
            data = _load_users_file()
            users = data.get("users") if isinstance(data.get("users"), list) else []
            if any(_normalize_username(str(u.get("username", ""))) == name for u in users if isinstance(u, dict)):
                raise ValueError("username_already_exists")
            salt_b64, pwd_hash_b64 = _hash_password(password)
            users.append(
                {
                    "username": name,
                    "password_salt": salt_b64,
                    "password_hash": pwd_hash_b64,
                    "plan": "free",
                    "role": "user",
                    "is_admin": False,
                    "created_at": _now_iso(),
                }
            )
            data["users"] = users
            _save_users_file(data)
        return self.login(name, password)

    def login(self, username: str, password: str) -> dict[str, Any]:
        name = _normalize_username(username)
        with _LOCK:
            users = _load_users_file().get("users") or []
            row = next(
                (
                    u
                    for u in users
                    if isinstance(u, dict) and _normalize_username(str(u.get("username", ""))) == name
                ),
                None,
            )
        if not isinstance(row, dict):
            raise ValueError("invalid_username_or_password")
        if not _verify_password(password, str(row.get("password_salt", "")), str(row.get("password_hash", ""))):
            raise ValueError("invalid_username_or_password")
        token = secrets.token_urlsafe(32)
        info = SessionInfo(token=token, username=name, created_at=_now_iso())
        with self._sessions_lock:
            self._sessions[token] = info
        return {
            "ok": True,
            "token": token,
            "user": _public_user(row, name),
            "session_created_at": info.created_at,
        }

    def me(self, token: str) -> dict[str, Any]:
        tk = str(token or "").strip()
        if not tk:
            raise ValueError("unauthorized")
        with self._sessions_lock:
            info = self._sessions.get(tk)
        if info is None:
            raise ValueError("unauthorized")
        with _LOCK:
            users = _load_users_file().get("users") or []
            row = next(
                (
                    u
                    for u in users
                    if isinstance(u, dict) and _normalize_username(str(u.get("username", ""))) == info.username
                ),
                None,
            )
        return {"ok": True, "user": _public_user(row, info.username), "session_created_at": info.created_at}

    def public_user(self, username: str) -> dict[str, Any]:
        name = _normalize_username(username)
        if not name:
            raise ValueError("unauthorized")
        with _LOCK:
            users = _load_users_file().get("users") or []
            row = next(
                (
                    u
                    for u in users
                    if isinstance(u, dict) and _normalize_username(str(u.get("username", ""))) == name
                ),
                None,
            )
        return _public_user(row, name)

    def logout(self, token: str) -> None:
        tk = str(token or "").strip()
        if not tk:
            return
        with self._sessions_lock:
            self._sessions.pop(tk, None)

    def verify_api_key(self, raw_key: str) -> str:
        """
        校验 X-Api-Key；命中则更新 last_used_at 并返回 username（小写）。
        明文 Key 仅创建时出现一次；此处只比对 SHA-256 hex。
        """
        plain = str(raw_key or "").strip()
        if not plain:
            raise ValueError("unauthorized")
        want = _hash_api_key_plaintext(plain)
        with _LOCK:
            data = _load_api_keys_file()
            keys = data.get("keys") if isinstance(data.get("keys"), list) else []
            hit_idx: int | None = None
            hit_username = ""
            for i, row in enumerate(keys):
                if not isinstance(row, dict):
                    continue
                if row.get("revoked_at"):
                    continue
                sh = str(row.get("secret_hash") or "")
                if sh and hmac.compare_digest(sh, want):
                    hit_idx = i
                    hit_username = _normalize_username(str(row.get("username", "")))
                    break
            if hit_idx is None or not hit_username:
                raise ValueError("unauthorized")
            keys[hit_idx]["last_used_at"] = _now_iso()
            data["keys"] = keys
            _save_api_keys_file(data)
        return hit_username

    def create_api_key(self, username: str, name: str) -> dict[str, Any]:
        """创建 API Key；返回明文 api_key 仅此一次。"""
        owner = _normalize_username(username)
        if not owner:
            raise ValueError("username_required")
        label = str(name or "").strip() or "default"
        key_id = f"k_{secrets.token_hex(8)}"
        plaintext = f"mt_live_{secrets.token_urlsafe(32)}"
        secret_hash = _hash_api_key_plaintext(plaintext)
        key_prefix = plaintext[:16]
        created = _now_iso()
        row = {
            "id": key_id,
            "username": owner,
            "secret_hash": secret_hash,
            "key_prefix": key_prefix,
            "name": label,
            "created_at": created,
            "revoked_at": None,
            "last_used_at": None,
        }
        with _LOCK:
            data = _load_api_keys_file()
            keys = data.get("keys") if isinstance(data.get("keys"), list) else []
            keys.append(row)
            data["keys"] = keys
            _save_api_keys_file(data)
        return {
            "ok": True,
            "id": key_id,
            "api_key": plaintext,
            "key_prefix": key_prefix,
            "name": label,
            "created_at": created,
        }

    def list_api_keys(self, username: str) -> list[dict[str, Any]]:
        owner = _normalize_username(username)
        out: list[dict[str, Any]] = []
        with _LOCK:
            data = _load_api_keys_file()
            keys = data.get("keys") if isinstance(data.get("keys"), list) else []
            for row in keys:
                if not isinstance(row, dict):
                    continue
                if _normalize_username(str(row.get("username", ""))) != owner:
                    continue
                out.append(
                    {
                        "id": str(row.get("id", "")),
                        "key_prefix": str(row.get("key_prefix", "")),
                        "name": str(row.get("name", "")),
                        "created_at": str(row.get("created_at", "")),
                        "revoked_at": row.get("revoked_at"),
                        "last_used_at": row.get("last_used_at"),
                    }
                )
        return out

    def revoke_api_key(self, username: str, key_id: str) -> bool:
        owner = _normalize_username(username)
        kid = str(key_id or "").strip()
        if not kid:
            return False
        with _LOCK:
            data = _load_api_keys_file()
            keys = data.get("keys") if isinstance(data.get("keys"), list) else []
            changed = False
            for row in keys:
                if not isinstance(row, dict):
                    continue
                if str(row.get("id", "")) != kid:
                    continue
                if _normalize_username(str(row.get("username", ""))) != owner:
                    return False
                row["revoked_at"] = _now_iso()
                changed = True
                break
            if not changed:
                return False
            data["keys"] = keys
            _save_api_keys_file(data)
        return True

    def purge_revoked_api_key(self, username: str, key_id: str) -> str:
        """从列表中永久移除一条已吊销的 Key。返回 deleted | not_found | not_revoked。"""
        owner = _normalize_username(username)
        kid = str(key_id or "").strip()
        if not kid:
            return "not_found"
        with _LOCK:
            data = _load_api_keys_file()
            keys = data.get("keys") if isinstance(data.get("keys"), list) else []
            idx: int | None = None
            for i, row in enumerate(keys):
                if not isinstance(row, dict):
                    continue
                if str(row.get("id", "")) != kid:
                    continue
                if _normalize_username(str(row.get("username", ""))) != owner:
                    return "not_found"
                if not row.get("revoked_at"):
                    return "not_revoked"
                idx = i
                break
            if idx is None:
                return "not_found"
            keys.pop(idx)
            data["keys"] = keys
            _save_api_keys_file(data)
        return "deleted"


_AUTH_SERVICE = UserAuthService()


def get_user_auth_service() -> UserAuthService:
    return _AUTH_SERVICE

