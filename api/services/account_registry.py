from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote, unquote

from api.brokers import BrokerCredentials, get_broker_adapter
from api.brokers import service_layer as broker_service
from api.services.broker_client_service import BROKER_CONNECT_BREAKER_SECONDS
from config.live_settings import live_settings

_ROOT = os.path.abspath(
    os.getenv("MULTITRADING_ROOT")
    or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
_ACCOUNTS_DATA_DIR = os.path.join(_ROOT, "data", "accounts")
BROKER_CONNECT_IN_PROGRESS_SECONDS = max(2.0, float(os.getenv("BROKER_CONNECT_IN_PROGRESS_SECONDS", "8")))


def _close_context(ctx: Any) -> None:
    if ctx is None:
        return
    close_fn = getattr(ctx, "close", None)
    if callable(close_fn):
        try:
            close_fn()
        except Exception:
            pass


def _unbind_and_close_contexts(rec: "AccountRecord") -> None:
    broker_service.unbind_contexts(rec.quote_ctx, rec.trade_ctx)
    _close_context(rec.quote_ctx)
    _close_context(rec.trade_ctx)


@dataclass
class AccountRecord:
    owner_id: str
    account_id: str
    broker_provider: str
    credentials: BrokerCredentials
    is_default: bool = False
    status: str = "registered"
    last_error: Optional[str] = None
    last_init_at: Optional[str] = None
    connect_breaker_until_ts: float = 0.0
    last_reset_ts: float = 0.0
    manual_disconnected: bool = False
    quote_ctx: Optional[Any] = None
    trade_ctx: Optional[Any] = None
    lock: threading.RLock = field(default_factory=threading.RLock)
    connect_lock: threading.Lock = field(default_factory=threading.Lock)


class AccountRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._accounts: dict[str, dict[str, AccountRecord]] = {}
        self._default_account_ids: dict[str, str] = {}
        self._load_all_from_disk()

    @staticmethod
    def _normalize_owner_id(owner_id: str | None = None) -> str:
        v = str(owner_id or "").strip().lower()
        return v or "__system__"

    @staticmethod
    def _owner_file_name(owner_id: str) -> str:
        return f"{quote(owner_id, safe='')}.json"

    def _owner_file_path(self, owner_id: str) -> str:
        return os.path.join(_ACCOUNTS_DATA_DIR, self._owner_file_name(owner_id))

    @staticmethod
    def _safe_status(status: str | None) -> str:
        s = str(status or "").strip()
        return s or "registered"

    def _record_to_dict(self, rec: AccountRecord) -> dict[str, Any]:
        return {
            "owner_id": rec.owner_id,
            "account_id": rec.account_id,
            "broker_provider": rec.broker_provider,
            "credentials": {
                "app_key": rec.credentials.app_key,
                "app_secret": rec.credentials.app_secret,
                "access_token": rec.credentials.access_token,
                "extras": dict(rec.credentials.extras or {}),
            },
            "is_default": bool(rec.is_default),
            "status": self._safe_status(rec.status),
            "last_error": rec.last_error,
            "last_init_at": rec.last_init_at,
            "connect_breaker_until_ts": float(rec.connect_breaker_until_ts or 0.0),
            "last_reset_ts": float(rec.last_reset_ts or 0.0),
            "manual_disconnected": bool(rec.manual_disconnected),
        }

    def _persist_owner(self, owner_id: str) -> None:
        owner = self._normalize_owner_id(owner_id)
        with self._lock:
            owner_accounts = self._accounts.get(owner) or {}
            default_id = self._default_account_ids.get(owner)
            payload = {
                "owner_id": owner,
                "default_account_id": default_id,
                "accounts": [self._record_to_dict(rec) for rec in owner_accounts.values()],
            }
        os.makedirs(_ACCOUNTS_DATA_DIR, exist_ok=True)
        path = self._owner_file_path(owner)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)

    def _load_owner_from_disk_locked(self, owner_id: str) -> None:
        owner = self._normalize_owner_id(owner_id)
        if owner in self._accounts:
            return
        path = self._owner_file_path(owner)
        if not os.path.isfile(path):
            self._accounts[owner] = {}
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            self._accounts[owner] = {}
            return
        rows = data.get("accounts") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            self._accounts[owner] = {}
            return
        loaded: dict[str, AccountRecord] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            aid = str(row.get("account_id", "")).strip()
            provider = str(row.get("broker_provider", "")).strip().lower()
            creds = row.get("credentials") if isinstance(row.get("credentials"), dict) else {}
            if not aid or not provider:
                continue
            try:
                get_broker_adapter(provider)
            except Exception:
                continue
            rec = AccountRecord(
                owner_id=owner,
                account_id=aid,
                broker_provider=provider,
                credentials=BrokerCredentials(
                    app_key=str(creds.get("app_key", "")),
                    app_secret=str(creds.get("app_secret", "")),
                    access_token=str(creds.get("access_token", "")),
                    extras=dict(creds.get("extras") if isinstance(creds.get("extras"), dict) else {}),
                ),
                is_default=bool(row.get("is_default", False)),
                status=self._safe_status(str(row.get("status", "registered"))),
                last_error=(str(row.get("last_error")) if row.get("last_error") is not None else None),
                last_init_at=(str(row.get("last_init_at")) if row.get("last_init_at") is not None else None),
                connect_breaker_until_ts=float(row.get("connect_breaker_until_ts", 0.0) or 0.0),
                last_reset_ts=float(row.get("last_reset_ts", 0.0) or 0.0),
                manual_disconnected=bool(row.get("manual_disconnected", False)),
                quote_ctx=None,
                trade_ctx=None,
            )
            loaded[aid] = rec
        self._accounts[owner] = loaded
        did = str((data.get("default_account_id") if isinstance(data, dict) else "") or "").strip()
        if did and did in loaded:
            self._default_account_ids[owner] = did
            for aid, rec in loaded.items():
                rec.is_default = aid == did
        elif loaded:
            first_id = next(iter(loaded.keys()))
            self._default_account_ids[owner] = first_id
            for aid, rec in loaded.items():
                rec.is_default = aid == first_id

    def _load_all_from_disk(self) -> None:
        with self._lock:
            os.makedirs(_ACCOUNTS_DATA_DIR, exist_ok=True)
            for name in os.listdir(_ACCOUNTS_DATA_DIR):
                if not name.endswith(".json"):
                    continue
                owner = unquote(name[:-5])
                if not owner:
                    continue
                self._load_owner_from_disk_locked(owner)

    def register_account(
        self,
        *,
        owner_id: str | None = None,
        account_id: str,
        broker_provider: str,
        credentials: BrokerCredentials,
        is_default: bool = False,
        overwrite: bool = False,
    ) -> AccountRecord:
        aid = str(account_id or "").strip()
        if not aid:
            raise ValueError("account_id_required")
        provider = str(broker_provider or "").strip().lower()
        get_broker_adapter(provider)
        owner = self._normalize_owner_id(owner_id)
        with self._lock:
            self._load_owner_from_disk_locked(owner)
            owner_accounts = self._accounts.setdefault(owner, {})
            if aid in owner_accounts and not overwrite:
                raise ValueError(f"account_exists: {aid}")
            prev = owner_accounts.get(aid)
            credentials_changed = bool(prev) and (
                str(getattr(prev, "broker_provider", "") or "").strip().lower() != provider
                or getattr(prev, "credentials", None) != credentials
            )
            if prev is not None and credentials_changed:
                with prev.lock:
                    _unbind_and_close_contexts(prev)
                    prev.quote_ctx = None
                    prev.trade_ctx = None
            rec = AccountRecord(
                owner_id=owner,
                account_id=aid,
                broker_provider=provider,
                credentials=credentials,
                is_default=bool(is_default) or (self._default_account_ids.get(owner) is None),
                status=("registered" if credentials_changed else (prev.status if prev else "registered")),
                last_error=(None if credentials_changed else (prev.last_error if prev else None)),
                last_init_at=(None if credentials_changed else (prev.last_init_at if prev else None)),
                connect_breaker_until_ts=(0.0 if credentials_changed else (prev.connect_breaker_until_ts if prev else 0.0)),
                last_reset_ts=(time.time() if credentials_changed else (prev.last_reset_ts if prev else 0.0)),
                manual_disconnected=(prev.manual_disconnected if prev else False),
                quote_ctx=(None if credentials_changed else (prev.quote_ctx if prev else None)),
                trade_ctx=(None if credentials_changed else (prev.trade_ctx if prev else None)),
            )
            owner_accounts[aid] = rec
            if rec.is_default:
                self._default_account_ids[owner] = aid
                for k, v in owner_accounts.items():
                    if k != aid:
                        v.is_default = False
            elif self._default_account_ids.get(owner) is None:
                self._default_account_ids[owner] = aid
                rec.is_default = True
        self._persist_owner(owner)
        return rec

    def ensure_default_account(self, owner_id: str | None = None) -> AccountRecord:
        owner = self._normalize_owner_id(owner_id)
        with self._lock:
            self._load_owner_from_disk_locked(owner)
            owner_accounts = self._accounts.get(owner) or {}
            default_id = self._default_account_ids.get(owner)
            if default_id and default_id in owner_accounts:
                return owner_accounts[default_id]
        if owner != "__system__":
            raise ValueError("default_account_not_set")
        account_id = str(getattr(live_settings, "DEFAULT_ACCOUNT_ID", "default")).strip() or "default"
        app_key, app_secret, access_token = live_settings.get_longbridge_credentials()
        rec = self.register_account(
            owner_id=owner,
            account_id=account_id,
            broker_provider=live_settings.active_broker(),
            credentials=BrokerCredentials(
                app_key=app_key,
                app_secret=app_secret,
                access_token=access_token,
            ),
            is_default=True,
            overwrite=True,
        )
        return rec

    def get_default_account_id(self, owner_id: str | None = None) -> str:
        rec = self.ensure_default_account(owner_id=owner_id)
        return rec.account_id

    def _resolve_account_id(self, account_id: str | None, owner_id: str | None = None) -> tuple[str, str]:
        owner = self._normalize_owner_id(owner_id)
        aid = str(account_id or "").strip()
        if aid:
            return owner, aid
        return owner, self.get_default_account_id(owner_id=owner)

    def get_account_record(self, account_id: str | None = None, owner_id: str | None = None) -> AccountRecord:
        owner, aid = self._resolve_account_id(account_id, owner_id=owner_id)
        with self._lock:
            self._load_owner_from_disk_locked(owner)
            rec = (self._accounts.get(owner) or {}).get(aid)
            if rec is None:
                raise ValueError(f"account_not_found: {aid}")
            return rec

    def list_accounts(self, owner_id: str | None = None) -> list[dict[str, Any]]:
        owner = self._normalize_owner_id(owner_id)
        with self._lock:
            self._load_owner_from_disk_locked(owner)
            out: list[dict[str, Any]] = []
            for rec in (self._accounts.get(owner) or {}).values():
                out.append(
                    {
                        "owner_id": rec.owner_id,
                        "account_id": rec.account_id,
                        "broker_provider": rec.broker_provider,
                        "is_default": bool(rec.is_default),
                        "status": rec.status,
                        "quote_ready": rec.quote_ctx is not None,
                        "trade_ready": rec.trade_ctx is not None,
                        "last_error": rec.last_error,
                        "last_init_at": rec.last_init_at,
                        "manual_disconnected": bool(rec.manual_disconnected),
                    }
                )
            return out

    def ensure_contexts(self, account_id: str | None = None, owner_id: str | None = None) -> tuple[Any, Any, str]:
        rec = self.get_account_record(account_id, owner_id=owner_id)
        with rec.lock:
            if rec.manual_disconnected:
                rec.last_error = "account_manual_disconnected"
                rec.status = "disconnected"
                self._persist_owner(rec.owner_id)
                raise ValueError(f"account_disconnected_manual_connect_required: {rec.account_id}")
            if rec.quote_ctx is not None and rec.trade_ctx is not None:
                broker_service.bind_contexts_to_broker(rec.quote_ctx, rec.trade_ctx, rec.broker_provider)
                return rec.quote_ctx, rec.trade_ctx, rec.account_id
            now = time.time()
            if now < float(rec.connect_breaker_until_ts or 0.0):
                rec.last_error = "broker_connect_breaker_open"
                rec.status = "error"
                raise RuntimeError("broker_connect_breaker_open")

        if not rec.connect_lock.acquire(blocking=False):
            raise RuntimeError("broker_connect_in_progress")
        try:
            with rec.lock:
                if rec.manual_disconnected:
                    rec.last_error = "account_manual_disconnected"
                    rec.status = "disconnected"
                    self._persist_owner(rec.owner_id)
                    raise ValueError(f"account_disconnected_manual_connect_required: {rec.account_id}")
                if rec.quote_ctx is not None and rec.trade_ctx is not None:
                    broker_service.bind_contexts_to_broker(rec.quote_ctx, rec.trade_ctx, rec.broker_provider)
                    return rec.quote_ctx, rec.trade_ctx, rec.account_id
                now = time.time()
                if now < float(rec.connect_breaker_until_ts or 0.0):
                    rec.last_error = "broker_connect_breaker_open"
                    rec.status = "error"
                    raise RuntimeError("broker_connect_breaker_open")
                adapter = get_broker_adapter(rec.broker_provider)
                rec.status = "connecting"
                rec.last_error = None
                rec.connect_breaker_until_ts = now + BROKER_CONNECT_IN_PROGRESS_SECONDS
                self._persist_owner(rec.owner_id)
            try:
                contexts = adapter.create_contexts(rec.credentials)
                with rec.lock:
                    rec.quote_ctx = contexts.quote
                    rec.trade_ctx = contexts.trade
                    broker_service.bind_contexts_to_broker(rec.quote_ctx, rec.trade_ctx, rec.broker_provider)
                    rec.last_error = None
                    rec.last_init_at = datetime.now().isoformat()
                    rec.connect_breaker_until_ts = 0.0
                    rec.status = "ready"
                    self._persist_owner(rec.owner_id)
                    return rec.quote_ctx, rec.trade_ctx, rec.account_id
            except Exception as e:
                with rec.lock:
                    rec.last_error = str(e)
                    rec.status = "error"
                    if adapter.is_connect_error(e):
                        rec.connect_breaker_until_ts = time.time() + BROKER_CONNECT_BREAKER_SECONDS
                    else:
                        rec.connect_breaker_until_ts = 0.0
                    self._persist_owner(rec.owner_id)
                raise
        finally:
            rec.connect_lock.release()

    def mark_broker_connect_error(self, err: Exception | str, account_id: str | None = None, owner_id: str | None = None) -> None:
        rec = self.get_account_record(account_id, owner_id=owner_id)
        now = time.time()
        with rec.lock:
            adapter = get_broker_adapter(rec.broker_provider)
            _unbind_and_close_contexts(rec)
            rec.quote_ctx = None
            rec.trade_ctx = None
            rec.status = "error"
            rec.manual_disconnected = False
            rec.last_error = str(err)
            rec.connect_breaker_until_ts = now + BROKER_CONNECT_BREAKER_SECONDS if adapter.is_connect_error(err) else 0.0
            rec.last_reset_ts = now
            self._persist_owner(rec.owner_id)

    def connect_account(self, account_id: str, owner_id: str | None = None) -> tuple[Any, Any, AccountRecord]:
        rec = self.get_account_record(account_id, owner_id=owner_id)
        owner = self._normalize_owner_id(owner_id)
        with self._lock:
            self._load_owner_from_disk_locked(owner)
            owner_accounts = self._accounts.get(owner) or {}
            self._default_account_ids[owner] = rec.account_id
            for aid, other in owner_accounts.items():
                other.is_default = aid == rec.account_id
                if aid == rec.account_id:
                    continue
                with other.lock:
                    # 多账户场景下同一时刻仅允许一个账户保持连接态：
                    # 当用户连接当前账户时，其他账户统一切到手动断连态。
                    _unbind_and_close_contexts(other)
                    other.quote_ctx = None
                    other.trade_ctx = None
                    other.status = "disconnected"
                    other.manual_disconnected = True
                    other.last_error = None
                    other.last_reset_ts = time.time()
            self._persist_owner(owner)
        with rec.lock:
            rec.is_default = True
            rec.manual_disconnected = False
            if rec.status == "disconnected":
                rec.status = "registered"
                rec.last_error = None
            self._persist_owner(rec.owner_id)
        qctx, tctx, _ = self.ensure_contexts(account_id, owner_id=owner_id)
        return qctx, tctx, rec

    def disconnect_account(self, account_id: str, owner_id: str | None = None) -> AccountRecord:
        rec = self.get_account_record(account_id, owner_id=owner_id)
        with rec.lock:
            _unbind_and_close_contexts(rec)
            rec.quote_ctx = None
            rec.trade_ctx = None
            rec.status = "disconnected"
            rec.manual_disconnected = True
            rec.last_error = None
            rec.last_reset_ts = time.time()
            self._persist_owner(rec.owner_id)
        return rec

    def delete_account(self, account_id: str, owner_id: str | None = None) -> AccountRecord:
        owner, aid = self._resolve_account_id(account_id, owner_id=owner_id)
        with self._lock:
            self._load_owner_from_disk_locked(owner)
            owner_accounts = self._accounts.setdefault(owner, {})
            rec = owner_accounts.get(aid)
            if rec is None:
                raise ValueError(f"account_not_found: {aid}")
            with rec.lock:
                _unbind_and_close_contexts(rec)
                rec.quote_ctx = None
                rec.trade_ctx = None
                rec.status = "deleted"
                rec.manual_disconnected = True
                rec.last_error = None
                rec.last_reset_ts = time.time()
            del owner_accounts[aid]

            current_default = self._default_account_ids.get(owner)
            if owner_accounts:
                next_default = current_default if current_default in owner_accounts else next(iter(owner_accounts.keys()))
                self._default_account_ids[owner] = next_default
                for key, other in owner_accounts.items():
                    other.is_default = key == next_default
            else:
                self._default_account_ids.pop(owner, None)
        self._persist_owner(owner)
        return rec

    def has_connected_account(self, owner_id: str | None = None) -> bool:
        owner = self._normalize_owner_id(owner_id)
        with self._lock:
            self._load_owner_from_disk_locked(owner)
            owner_accounts = self._accounts.get(owner) or {}
            for rec in owner_accounts.values():
                if bool(rec.manual_disconnected):
                    continue
                status = str(rec.status or "").strip().lower()
                if status != "disconnected":
                    return True
            return False

    def reset_contexts(self, account_id: str | None = None, owner_id: str | None = None) -> None:
        owner = self._normalize_owner_id(owner_id)
        if account_id is None:
            with self._lock:
                ids = list((self._accounts.get(owner) or {}).keys())
            for aid in ids:
                self.reset_contexts(aid, owner_id=owner)
            return
        rec = self.get_account_record(account_id, owner_id=owner)
        with rec.lock:
            _unbind_and_close_contexts(rec)
            rec.quote_ctx = None
            rec.trade_ctx = None
            rec.status = "registered"
            rec.manual_disconnected = False
            rec.last_error = None
            rec.connect_breaker_until_ts = 0.0
            rec.last_reset_ts = time.time()
            self._persist_owner(rec.owner_id)


_ACCOUNT_REGISTRY = AccountRegistry()


def get_account_registry() -> AccountRegistry:
    _ACCOUNT_REGISTRY.ensure_default_account()
    return _ACCOUNT_REGISTRY
