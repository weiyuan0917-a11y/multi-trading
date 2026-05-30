from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Callable


REMOVED_REASON = "auto_trading_removed"


def _removed_payload(**extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": False,
        "enabled": False,
        "disabled": True,
        "reason": REMOVED_REASON,
    }
    out.update(extra)
    return out


def auto_trader_config_path_for_owner(owner_id: str | None = None, root: str | None = None) -> str:
    base = root or os.getenv("MULTITRADING_ROOT") or os.getcwd()
    owner = str(owner_id or "").strip().lower()
    filename = f"auto_trader_config.{owner}.json" if owner else "auto_trader_config.json"
    return os.path.join(base, "api", filename)


def make_feishu_sender(_config_path: str | None = None) -> Callable[[str, str | None], None]:
    def _send(_text: str, _title: str | None = None) -> None:
        return None

    return _send


def load_persisted_signals(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
    return []


def summarize_legacy_unscoped_signals(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    return {"count": 0, "items": []}


def archive_legacy_unscoped_signals(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    return {"archived_signal_ids": [], "count": 0, "reason": REMOVED_REASON}


def prune_persisted_signals(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    return {"removed_signal_ids": [], "count": 0, "reason": REMOVED_REASON}


class AutoTraderService:
    def __init__(self, *args: Any, config_path: str | None = None, **kwargs: Any) -> None:
        self.config_path = config_path
        self._args = args
        self._kwargs = kwargs

    def get_config(self) -> dict[str, Any]:
        return {
            "enabled": False,
            "removed": True,
            "reason": REMOVED_REASON,
            "config_path": self.config_path,
        }

    def update_config(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        cfg = self.get_config()
        if isinstance(payload, dict):
            cfg.update({k: v for k, v in payload.items() if k != "enabled"})
        cfg["enabled"] = False
        cfg["removed"] = True
        return cfg

    def get_status(self) -> dict[str, Any]:
        return _removed_payload(
            running=False,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )

    def stop_scheduler(self) -> None:
        return None

    def list_signals(self, status: str = "all") -> list[dict[str, Any]]:
        return []

    def drop_signals(self, signal_ids: list[str] | None = None) -> int:
        return len(signal_ids or [])

    def list_templates(self) -> list[dict[str, Any]]:
        return []

    def list_config_backups(self) -> list[dict[str, Any]]:
        return []

    def screen_strong_stocks(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    def run_scan_once(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return _removed_payload(items=[], count=0)

    def apply_template(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.get_config()

    def preview_template(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return _removed_payload(diff={}, config=self.get_config())

    def rollback_config(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.get_config()

    def preview_rollback(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return _removed_payload(diff={}, config=self.get_config())

    def confirm_and_execute(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return _removed_payload(action="confirm_and_execute")

    def __getattr__(self, name: str) -> Callable[..., dict[str, Any]]:
        def _disabled(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return _removed_payload(action=name)

        return _disabled
