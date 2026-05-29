import os
from pathlib import Path

from config.env_loader import load_project_env

# 通过 env_loader 加载根 .env（可为占位）并合并用户 davies 的 data/user_env 文件。
_ROOT = Path(os.getenv("MULTITRADING_ROOT") or Path(__file__).resolve().parents[1]).resolve()
load_project_env(_ROOT)


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def _get_symbols(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name, "")
    if not raw.strip():
        return default
    out = []
    seen = set()
    for row in raw.replace("\n", ",").split(","):
        sym = row.strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
    return out or default


class LiveSettings:
    def __init__(self):
        # Active broker provider. Keep default as longbridge for now.
        self.BROKER_PROVIDER = os.getenv("BROKER_PROVIDER", "longbridge").strip().lower() or "longbridge"
        self.DEFAULT_ACCOUNT_ID = os.getenv("DEFAULT_ACCOUNT_ID", "default").strip() or "default"

        # LongPort credentials (must come from env).
        self.LONGPORT_APP_KEY = os.getenv("LONGPORT_APP_KEY", "").strip()
        self.LONGPORT_APP_SECRET = os.getenv("LONGPORT_APP_SECRET", "").strip()
        self.LONGPORT_ACCESS_TOKEN = os.getenv("LONGPORT_ACCESS_TOKEN", "").strip()

        # Optional runtime settings.
        self.DEFAULT_SYMBOLS = _get_symbols("DEFAULT_SYMBOLS", ["AAPL.US", "MSFT.US", "TSLA.US"])
        self.TRADE_INTERVAL = _get_int("TRADE_INTERVAL", 3600)
        self.MAX_POSITION_PERCENT = _get_float("MAX_POSITION_PERCENT", 0.2)
        self.STOP_LOSS_PERCENT = _get_float("STOP_LOSS_PERCENT", 0.03)

    def active_broker(self) -> str:
        key = (self.BROKER_PROVIDER or "longbridge").strip().lower()
        if key == "longport":
            return "longbridge"
        return key or "longbridge"

    def get_longbridge_credentials(self) -> tuple[str, str, str]:
        return (
            self.LONGPORT_APP_KEY,
            self.LONGPORT_APP_SECRET,
            self.LONGPORT_ACCESS_TOKEN,
        )

    def missing_broker_fields(self, broker_id: str | None = None) -> list[str]:
        active = (broker_id or self.active_broker()).strip().lower()
        if active == "longbridge":
            return self.missing_longport_fields()
        return [f"Unsupported broker provider: {active}"]

    def assert_broker_configured(self, broker_id: str | None = None) -> None:
        active = (broker_id or self.active_broker()).strip().lower()
        if active == "longbridge":
            self.assert_longport_configured()
            return
        raise ValueError(f"Unsupported broker provider: {active}")

    def missing_longport_fields(self) -> list[str]:
        missing = []
        if not self.LONGPORT_APP_KEY:
            missing.append("LONGPORT_APP_KEY")
        if not self.LONGPORT_APP_SECRET:
            missing.append("LONGPORT_APP_SECRET")
        if not self.LONGPORT_ACCESS_TOKEN:
            missing.append("LONGPORT_ACCESS_TOKEN")
        return missing

    def assert_longport_configured(self) -> None:
        missing = self.missing_longport_fields()
        if missing:
            raise ValueError(
                "Missing required LongPort env vars: "
                + ", ".join(missing)
                + ". 请在 Web 设置页保存密钥，或编辑 data/user_env/<用户名>.env（参见 .env.example）。"
            )


live_settings = LiveSettings()
