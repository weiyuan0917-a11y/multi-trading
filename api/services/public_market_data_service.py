from __future__ import annotations

import csv
import importlib.util
import io
import json
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote as url_quote

import requests


ROOT = os.path.abspath(
    os.getenv("MULTITRADING_ROOT")
    or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
KLINE_CACHE_DIR = os.path.join(ROOT, "data", "klines")
LAST_GOOD_CACHE_DIR = os.path.join(ROOT, "data", "market_cache", "public_quotes")

SCHEMA = "public_market_data.v1"
DEFAULT_PROVIDER_ORDER = "polygon,twelvedata,tencent_hk,tencent_index,mootdx,eastmoney,akshare,cn_local_cache,yahoo,stooq,hk_local_cache,us_local_cache"
PROVIDER_IDS = (
    "polygon",
    "twelvedata",
    "tencent_hk",
    "tencent_index",
    "mootdx",
    "eastmoney",
    "yahoo",
    "akshare",
    "stooq",
    "cn_local_cache",
    "hk_local_cache",
    "us_local_cache",
)

YAHOO_ALIASES = {
    "SPX.US": "^GSPC",
    "GSPC.US": "^GSPC",
    "DJI.US": "^DJI",
    "IXIC.US": "^IXIC",
    "NDX.US": "^NDX",
    "RUT.US": "^RUT",
    "VIX.US": "^VIX",
    "HSI.HK": "^HSI",
    "HSTECH.HK": "^HSTECH",
    "HSCEI.HK": "^HSCE",
    "000001.SH": "000001.SS",
    "399001.SZ": "399001.SZ",
    "399006.SZ": "399006.SZ",
}

STOOQ_ALIASES = {
    "SPY.US": "spy.us",
    "QQQ.US": "qqq.us",
    "DIA.US": "dia.us",
    "IWM.US": "iwm.us",
    "XLK.US": "xlk.us",
    "XLF.US": "xlf.us",
    "XLE.US": "xle.us",
    "XLV.US": "xlv.us",
}

EASTMONEY_INDEX_ALIASES = {
    "000001.SH": "1.000001",
    "399001.SZ": "0.399001",
    "399006.SZ": "0.399006",
    "HSI.HK": "100.HSI",
    "HSTECH.HK": "124.HSTECH",
    "HSCEI.HK": "100.HSCEI",
}

EASTMONEY_US_ALIASES = {
    "SPY.US": "107.SPY",
    "QQQ.US": "105.QQQ",
    "DIA.US": "107.DIA",
    "IWM.US": "107.IWM",
}

SOURCE_LABELS = {
    "polygon": "Polygon.io market data",
    "twelvedata": "Twelve Data market data",
    "tencent_hk": "Tencent HK public",
    "tencent_index": "Tencent A-share index",
    "mootdx": "mootdx / Tongdaxin public",
    "eastmoney": "EastMoney public",
    "yahoo": "Yahoo Finance public",
    "akshare": "AkShare public",
    "stooq": "Stooq public",
    "cn_local_cache": "Local CN cache",
    "hk_local_cache": "Local HK last-good cache",
    "us_local_cache": "Local US last-good cache",
}


def _env_bool(key: str, default: str = "1") -> bool:
    return str(os.getenv(key, default)).strip().lower() in {"1", "true", "yes", "on"}


def _safe_float(v: Any, default: float | None = None) -> float | None:
    if v is None:
        return default
    try:
        if isinstance(v, str):
            v = v.replace(",", "").replace("%", "").strip()
            if not v or v in {"-", "--", "nan", "None"}:
                return default
        out = float(v)
        if math.isfinite(out):
            return out
    except Exception:
        pass
    return default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(float(v))
    except Exception:
        return default


def _pick(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row:
            return row.get(key)
    return None


def _records_from_frame(df: Any) -> list[dict[str, Any]]:
    if df is None:
        return []
    if hasattr(df, "to_dict"):
        try:
            return [x for x in df.to_dict(orient="records") if isinstance(x, dict)]
        except Exception:
            return []
    if isinstance(df, list):
        return [x for x in df if isinstance(x, dict)]
    return []


def _iso_from_timestamp(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_symbol(symbol: str) -> str:
    raw = str(symbol or "").strip().upper().replace("_", ".")
    if not raw:
        return ""
    if raw.startswith(("SH.", "SZ.", "BJ.")):
        market, code = raw.split(".", 1)
        return f"{code.zfill(6)}.{market}"
    if raw.endswith(".SS"):
        return f"{raw[:-3]}.SH"
    if raw.endswith(".SH") or raw.endswith(".SZ") or raw.endswith(".BJ"):
        code, market = raw.split(".", 1)
        if code.isdigit():
            return f"{code.zfill(6)}.{market}"
    if "." not in raw and raw.isdigit() and len(raw) == 6:
        market = "SH" if raw.startswith(("6", "9")) else "BJ" if raw.startswith(("4", "8")) else "SZ"
        return f"{raw}.{market}"
    return raw


def _split_symbols(symbols: str | list[str]) -> list[str]:
    if isinstance(symbols, str):
        raw_items = symbols.replace(";", ",").split(",")
    else:
        raw_items = [str(x or "") for x in symbols]
    out: list[str] = []
    for item in raw_items:
        sym = canonical_symbol(item)
        if sym and sym not in out:
            out.append(sym)
    return out


def _is_cn(symbol: str) -> bool:
    return canonical_symbol(symbol).endswith((".SH", ".SZ", ".BJ"))


def _is_hk(symbol: str) -> bool:
    return canonical_symbol(symbol).endswith(".HK")


def _is_us(symbol: str) -> bool:
    sym = canonical_symbol(symbol)
    return sym.endswith(".US") or "." not in sym or sym.startswith("^")


def _symbol_code(symbol: str) -> str:
    return canonical_symbol(symbol).split(".", 1)[0]


def _yahoo_symbol(symbol: str) -> str:
    sym = canonical_symbol(symbol)
    if sym in YAHOO_ALIASES:
        return YAHOO_ALIASES[sym]
    if sym.startswith("^"):
        return sym
    if sym.endswith(".US"):
        return sym[:-3]
    if sym.endswith(".HK"):
        code = sym[:-3]
        if code.isdigit():
            return f"{int(code):04d}.HK"
        return YAHOO_ALIASES.get(sym, sym)
    if sym.endswith(".SH"):
        return f"{_symbol_code(sym)}.SS"
    if sym.endswith(".SZ"):
        return f"{_symbol_code(sym)}.SZ"
    if sym.endswith(".BJ"):
        return f"{_symbol_code(sym)}.BJ"
    return sym


def _stooq_symbol(symbol: str) -> str | None:
    sym = canonical_symbol(symbol)
    if sym in STOOQ_ALIASES:
        return STOOQ_ALIASES[sym]
    if sym.endswith(".US"):
        return sym.lower()
    return None


def _eastmoney_secids(symbol: str) -> list[str]:
    sym = canonical_symbol(symbol)
    out: list[str] = []

    def add(secid: str | None) -> None:
        if secid and "." in secid and secid not in out:
            out.append(secid)

    if sym in EASTMONEY_INDEX_ALIASES:
        add(EASTMONEY_INDEX_ALIASES[sym])
        return out
    if sym in EASTMONEY_US_ALIASES:
        add(EASTMONEY_US_ALIASES[sym])
    if sym.endswith(".SH"):
        add(f"1.{_symbol_code(sym)}")
        return out
    if sym.endswith(".SZ") or sym.endswith(".BJ"):
        add(f"0.{_symbol_code(sym)}")
        return out
    if sym.endswith(".HK"):
        code = _symbol_code(sym)
        if code.isdigit():
            padded = f"{int(code):05d}"
            for market_id in ("100", "116", "124"):
                add(f"{market_id}.{padded}")
            add(f"100.{code.upper()}")
            add(f"124.{code.upper()}")
        return out
    if sym.endswith(".US") or ("." not in sym and sym and not sym.isdigit() and not sym.startswith("^")):
        code = sym[:-3] if sym.endswith(".US") else sym
        if code:
            # EastMoney splits US instruments across several market ids
            # (NASDAQ/NYSE/AMEX/ETF). Probe all common ids for a robust
            # mainland-network fallback.
            for market_id in ("105", "106", "107"):
                add(f"{market_id}.{code}")
    return out


def _eastmoney_secid(symbol: str) -> str | None:
    secids = _eastmoney_secids(symbol)
    return secids[0] if secids else None


def _range_for_days(days: int) -> str:
    d = max(1, int(days or 5))
    if d <= 5:
        return "5d"
    if d <= 30:
        return "1mo"
    if d <= 90:
        return "3mo"
    if d <= 180:
        return "6mo"
    if d <= 365:
        return "1y"
    if d <= 730:
        return "2y"
    if d <= 1825:
        return "5y"
    return "10y"


def _yahoo_interval(period: str) -> str:
    p = str(period or "1d").strip().lower()
    if p in {"1w", "week", "weekly", "w"}:
        return "1wk"
    if p in {"1mo", "1mth", "month", "monthly", "m"}:
        return "1mo"
    return "1d"


def _eastmoney_klt(period: str) -> str | None:
    p = str(period or "1d").strip().lower()
    mapping = {
        "1m": "1",
        "5m": "5",
        "15m": "15",
        "30m": "30",
        "1h": "60",
        "60m": "60",
        "1d": "101",
        "day": "101",
        "d": "101",
        "1w": "102",
        "week": "102",
        "weekly": "102",
        "1mo": "103",
        "month": "103",
        "monthly": "103",
    }
    return mapping.get(p)


def _stooq_interval(period: str) -> str:
    p = str(period or "1d").strip().lower()
    if p in {"1w", "week", "weekly", "w"}:
        return "w"
    if p in {"1mo", "1mth", "month", "monthly", "m"}:
        return "m"
    return "d"


def _provider_api_key(provider: str) -> str:
    if provider == "polygon":
        return str(os.getenv("POLYGON_API_KEY", "") or "").strip()
    if provider == "twelvedata":
        return str(os.getenv("TWELVE_DATA_API_KEY", os.getenv("TWELVEDATA_API_KEY", "")) or "").strip()
    return ""


def _us_code(symbol: str) -> str:
    sym = canonical_symbol(symbol)
    return sym[:-3] if sym.endswith(".US") else sym.lstrip("^")


def _cache_symbol_filename(symbol: str) -> str:
    sym = canonical_symbol(symbol).replace("/", "_").replace("\\", "_").replace(":", "_")
    return f"{sym}.json"


def _change_pct(last: float | None, prev: float | None) -> float | None:
    if last is None or prev is None or prev == 0:
        return None
    return round((last - prev) / prev * 100.0, 2)


def _quote_from_bars(symbol: str, rows: list[dict[str, Any]], source: str) -> dict[str, Any] | None:
    if not rows:
        return None
    ordered = [x for x in rows if _safe_float(x.get("close")) is not None]
    if not ordered:
        return None
    last_bar = ordered[-1]
    prev_bar = ordered[-2] if len(ordered) >= 2 else None
    last = _safe_float(last_bar.get("close"))
    prev = _safe_float(prev_bar.get("close")) if prev_bar else _safe_float(last_bar.get("prev_close"))
    if last is None:
        return None
    return {
        "symbol": canonical_symbol(symbol),
        "name": "",
        "last": last,
        "prev_close": prev,
        "change_pct": _change_pct(last, prev),
        "open": _safe_float(last_bar.get("open"), last),
        "high": _safe_float(last_bar.get("high"), last),
        "low": _safe_float(last_bar.get("low"), last),
        "volume": _safe_float(last_bar.get("volume"), 0.0),
        "as_of": str(last_bar.get("date") or _now_iso()),
        "source": source,
        "source_label": SOURCE_LABELS.get(source, source),
        "price_type": f"{SOURCE_LABELS.get(source, source)} snapshot",
        "realtime": False,
        "cache": source == "cn_local_cache",
    }


class PublicMarketDataService:
    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, Any]] = {}
        self._cache_lock = threading.Lock()

    def provider_status(self) -> dict[str, Any]:
        order = self._provider_order()
        providers = [
            {
                "id": "polygon",
                "name": "Polygon.io",
                "enabled": self._provider_enabled("polygon"),
                "configured": bool(_provider_api_key("polygon")),
                "installed": True,
                "status_text": "available" if self._provider_enabled("polygon") else "disabled",
                "setup_hint": "Set POLYGON_API_KEY for non-broker US real-time/delayed market data.",
                "role": "US quote provider without broker binding; entitlement depends on the Polygon plan.",
                "capabilities": ["quote", "us_market", "stocks", "etfs"],
                "priority": order.index("polygon") if "polygon" in order else 999,
            },
            {
                "id": "twelvedata",
                "name": "Twelve Data",
                "enabled": self._provider_enabled("twelvedata"),
                "configured": bool(_provider_api_key("twelvedata")),
                "installed": True,
                "status_text": "available" if self._provider_enabled("twelvedata") else "disabled",
                "setup_hint": "Set TWELVE_DATA_API_KEY for non-broker US quote fallback.",
                "role": "US quote provider without broker binding; entitlement depends on the Twelve Data plan.",
                "capabilities": ["quote", "us_market", "stocks", "etfs"],
                "priority": order.index("twelvedata") if "twelvedata" in order else 999,
            },
            {
                "id": "mootdx",
                "name": "mootdx / Tongdaxin public",
                "enabled": self._provider_enabled("mootdx"),
                "configured": importlib.util.find_spec("mootdx") is not None,
                "installed": importlib.util.find_spec("mootdx") is not None,
                "status_text": self._provider_status_text("mootdx", importlib.util.find_spec("mootdx") is not None),
                "setup_hint": "Install mootdx in the backend Python environment for A-share public quote/K-line fallback.",
                "role": "A-share quote/K-line fallback for mainland networks.",
                "capabilities": ["quote", "klines", "cn_market"],
                "priority": order.index("mootdx") if "mootdx" in order else 999,
            },
            {
                "id": "tencent_hk",
                "name": "Tencent HK public",
                "enabled": self._provider_enabled("tencent_hk"),
                "configured": True,
                "installed": True,
                "status_text": "available" if self._provider_enabled("tencent_hk") else "disabled",
                "setup_hint": "No broker account or API key required; uses Tencent public HK quote endpoints.",
                "role": "HK index and quote fallback for mainland networks.",
                "capabilities": ["quote", "hk_market", "indices"],
                "priority": order.index("tencent_hk") if "tencent_hk" in order else 999,
            },
            {
                "id": "tencent_index",
                "name": "Tencent A-share index",
                "enabled": self._provider_enabled("tencent_index"),
                "configured": True,
                "installed": True,
                "status_text": "available" if self._provider_enabled("tencent_index") else "disabled",
                "setup_hint": "No broker account or API key required; fixes A-share index code conflicts such as 000001.SH.",
                "role": "A-share core index quote fallback for mainland networks.",
                "capabilities": ["quote", "cn_market", "indices"],
                "priority": order.index("tencent_index") if "tencent_index" in order else 999,
            },
            {
                "id": "eastmoney",
                "name": "EastMoney public quote",
                "enabled": self._provider_enabled("eastmoney"),
                "configured": True,
                "installed": True,
                "status_text": "available" if self._provider_enabled("eastmoney") else "disabled",
                "setup_hint": "No broker account or API key required; uses EastMoney public quote endpoints.",
                "role": "A/H/US snapshot fallback for dashboard and market views.",
                "capabilities": ["quote", "klines", "indices", "etfs", "stocks"],
                "priority": order.index("eastmoney") if "eastmoney" in order else 999,
            },
            {
                "id": "yahoo",
                "name": "Yahoo Finance public chart",
                "enabled": self._provider_enabled("yahoo"),
                "configured": True,
                "installed": True,
                "status_text": "available" if self._provider_enabled("yahoo") else "disabled",
                "setup_hint": "No broker account or API key required; uses public HTTP chart data.",
                "role": "US/HK/CN/global quotes and daily bars, suitable for delayed research views.",
                "capabilities": ["quote", "klines", "indices", "etfs", "stocks"],
                "priority": order.index("yahoo") if "yahoo" in order else 999,
            },
            {
                "id": "akshare",
                "name": "AkShare",
                "enabled": self._provider_enabled("akshare"),
                "configured": importlib.util.find_spec("akshare") is not None,
                "installed": importlib.util.find_spec("akshare") is not None,
                "status_text": self._provider_status_text("akshare", importlib.util.find_spec("akshare") is not None),
                "setup_hint": "Install akshare in the backend Python environment for broader A/H share coverage.",
                "role": "A/H share public quote enhancement.",
                "capabilities": ["quote", "cn_market", "hk_market"],
                "priority": order.index("akshare") if "akshare" in order else 999,
            },
            {
                "id": "stooq",
                "name": "Stooq",
                "enabled": self._provider_enabled("stooq"),
                "configured": True,
                "installed": True,
                "status_text": "available" if self._provider_enabled("stooq") else "disabled",
                "setup_hint": "No broker account or API key required; daily public CSV data.",
                "role": "US ETF and index history fallback.",
                "capabilities": ["klines", "quote_from_history"],
                "priority": order.index("stooq") if "stooq" in order else 999,
            },
            {
                "id": "cn_local_cache",
                "name": "Local CN K-line cache",
                "enabled": self._provider_enabled("cn_local_cache"),
                "configured": os.path.isdir(KLINE_CACHE_DIR),
                "installed": True,
                "status_text": "available" if os.path.isdir(KLINE_CACHE_DIR) else "cache_missing",
                "setup_hint": "No external service required; populated by existing backtest/K-line cache jobs.",
                "role": "Offline fallback for A-share daily bars.",
                "capabilities": ["klines", "quote_from_history"],
                "priority": order.index("cn_local_cache") if "cn_local_cache" in order else 999,
            },
            {
                "id": "us_local_cache",
                "name": "Local US last-good cache",
                "enabled": self._provider_enabled("us_local_cache"),
                "configured": os.path.isdir(LAST_GOOD_CACHE_DIR),
                "installed": True,
                "status_text": "available" if os.path.isdir(LAST_GOOD_CACHE_DIR) else "cache_missing",
                "setup_hint": "Populated automatically after any successful US public quote.",
                "role": "Offline last-good fallback for dashboard and market views.",
                "capabilities": ["quote_from_cache", "us_market"],
                "priority": order.index("us_local_cache") if "us_local_cache" in order else 999,
            },
            {
                "id": "hk_local_cache",
                "name": "Local HK last-good cache",
                "enabled": self._provider_enabled("hk_local_cache"),
                "configured": os.path.isdir(LAST_GOOD_CACHE_DIR),
                "installed": True,
                "status_text": "available" if os.path.isdir(LAST_GOOD_CACHE_DIR) else "cache_missing",
                "setup_hint": "Populated automatically after any successful HK public quote.",
                "role": "Offline last-good fallback for HK indices and shares.",
                "capabilities": ["quote_from_cache", "hk_market"],
                "priority": order.index("hk_local_cache") if "hk_local_cache" in order else 999,
            },
        ]
        providers.sort(key=lambda x: int(x["priority"]))
        return {
            "ok": True,
            "schema": SCHEMA,
            "provider_order": order,
            "providers": providers,
            "notes": [
                "Public market data is best-effort, delayed, and not suitable as an execution-grade feed.",
                "Longbridge or another broker/data vendor can still be used when configured.",
            ],
        }

    @staticmethod
    def _provider_status_text(provider: str, installed: bool) -> str:
        if not _env_bool(f"PUBLIC_MARKET_{provider.upper()}_ENABLED", "1"):
            return "disabled"
        return "available" if installed else "not_installed"

    def quote(self, symbols: str | list[str], source: str = "auto") -> dict[str, Any]:
        syms = _split_symbols(symbols)
        if not syms:
            return {"ok": False, "schema": SCHEMA, "source": source, "items": [], "errors": [{"error": "symbols_required"}]}

        items_by_symbol: dict[str, dict[str, Any]] = {}
        errors: list[dict[str, Any]] = []
        providers = self._providers_for(source)
        for provider in providers:
            if not self._provider_enabled(provider):
                continue
            batch_syms = [sym for sym in syms if sym not in items_by_symbol]
            if not batch_syms:
                break
            try:
                batch_items = self._batch_quote_by_provider(provider, batch_syms)
            except Exception as exc:
                batch_items = []
                errors.append({"provider": provider, "error": str(exc)})
            for item in batch_items:
                if isinstance(item, dict):
                    sym = canonical_symbol(str(item.get("symbol") or ""))
                    if sym and sym not in items_by_symbol:
                        items_by_symbol[sym] = item
                        self._save_last_good_quote(item)

        remaining = [sym for sym in syms if sym not in items_by_symbol]
        if not remaining:
            items = [items_by_symbol[sym] for sym in syms if sym in items_by_symbol]
            return {
                "ok": bool(items),
                "schema": SCHEMA,
                "source": source,
                "items": items,
                "errors": errors,
                "data_status": self._data_status(items),
            }

        max_workers = max(1, min(8, len(syms)))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(self._quote_one, sym, source): sym for sym in remaining}
            for future in as_completed(futures):
                sym = futures[future]
                try:
                    item, item_errors = future.result()
                except Exception as exc:
                    item = None
                    item_errors = [{"symbol": sym, "error": str(exc)}]
                if item:
                    items_by_symbol[sym] = item
                    self._save_last_good_quote(item)
                errors.extend(item_errors)

        items = [items_by_symbol[sym] for sym in syms if sym in items_by_symbol]
        return {
            "ok": bool(items),
            "schema": SCHEMA,
            "source": source,
            "items": items,
            "errors": errors,
            "data_status": self._data_status(items),
        }

    def klines(
        self,
        symbol: str,
        period: str = "1d",
        days: int = 180,
        limit: int = 0,
        source: str = "auto",
    ) -> dict[str, Any]:
        sym = canonical_symbol(symbol)
        days_i = max(1, min(3650, int(days or 180)))
        lim = max(0, min(5000, int(limit or 0)))
        errors: list[dict[str, Any]] = []
        tried: list[str] = []
        for provider in self._providers_for(source):
            if not self._provider_enabled(provider):
                continue
            tried.append(provider)
            try:
                rows = self._klines_by_provider(provider, sym, period=period, days=days_i, limit=lim)
            except Exception as exc:
                rows = []
                errors.append({"provider": provider, "error": str(exc)})
            if rows:
                if lim > 0:
                    rows = rows[-lim:]
                return {
                    "ok": True,
                    "schema": SCHEMA,
                    "symbol": sym,
                    "period": period,
                    "days": days_i,
                    "source": provider,
                    "items": rows,
                    "bar_count": len(rows),
                    "tried": tried,
                    "errors": errors,
                    "data_status": self._data_status(rows, provider=provider),
                }
        return {
            "ok": False,
            "schema": SCHEMA,
            "symbol": sym,
            "period": period,
            "days": days_i,
            "source": source,
            "items": [],
            "bar_count": 0,
            "tried": tried,
            "errors": errors or [{"symbol": sym, "error": "klines_unavailable"}],
            "data_status": self._data_status([], provider=source),
        }

    def market_snap(self, symbols: list[tuple[str, str]], source: str = "auto") -> list[dict[str, Any]]:
        syms = [canonical_symbol(sym) for sym, _ in symbols]
        resp = self.quote(syms, source=source)
        items = {canonical_symbol(x.get("symbol", "")): x for x in resp.get("items", []) if isinstance(x, dict)}
        out: list[dict[str, Any]] = []
        for sym_raw, name in symbols:
            sym = canonical_symbol(sym_raw)
            item = items.get(sym)
            if not item:
                continue
            row = dict(item)
            row["symbol"] = sym
            row["name"] = name or row.get("name") or sym
            out.append(row)
        return out

    def source_label_from_items(self, items: list[dict[str, Any]]) -> str:
        labels: list[str] = []
        for item in items:
            label = str(item.get("source_label") or SOURCE_LABELS.get(str(item.get("source") or ""), "")).strip()
            if label and label not in labels:
                labels.append(label)
        return ", ".join(labels) if labels else "Unknown"

    def _provider_order(self) -> list[str]:
        raw = os.getenv("PUBLIC_MARKET_DATA_PROVIDER_ORDER", DEFAULT_PROVIDER_ORDER)
        out: list[str] = []
        aliases = {
            "polygonio": "polygon",
            "polygon_io": "polygon",
            "twelve": "twelvedata",
            "twelve_data": "twelvedata",
            "twelve-data": "twelvedata",
            "tencent": "tencent_hk",
            "qq": "tencent_hk",
            "tencenthk": "tencent_hk",
            "tencent_cn": "tencent_index",
            "tencent_index_cn": "tencent_index",
            "em": "eastmoney",
            "east_money": "eastmoney",
            "tdx": "mootdx",
            "tongdaxin": "mootdx",
            "yfinance": "yahoo",
            "yahoo_finance": "yahoo",
            "cn_cache": "cn_local_cache",
            "local_cache": "cn_local_cache",
            "hk_cache": "hk_local_cache",
            "us_cache": "us_local_cache",
            "last_good": "us_local_cache",
            "last_good_cache": "us_local_cache",
        }
        for item in str(raw or "").split(","):
            provider = aliases.get(item.strip().lower(), item.strip().lower())
            if provider in PROVIDER_IDS and provider not in out:
                out.append(provider)
        for item in str(DEFAULT_PROVIDER_ORDER or "").split(","):
            provider = aliases.get(item.strip().lower(), item.strip().lower())
            if provider in PROVIDER_IDS and provider not in out:
                out.append(provider)
        front = [p for p in ("polygon", "twelvedata", "tencent_hk", "tencent_index") if p in out]
        out = front + [p for p in out if p not in front]
        return out or list(PROVIDER_IDS)

    def _providers_for(self, source: str) -> list[str]:
        src = str(source or "auto").strip().lower()
        aliases = {
            "polygonio": "polygon",
            "polygon_io": "polygon",
            "twelve": "twelvedata",
            "twelve_data": "twelvedata",
            "twelve-data": "twelvedata",
            "tencent": "tencent_hk",
            "qq": "tencent_hk",
            "tencenthk": "tencent_hk",
            "tencent_cn": "tencent_index",
            "tencent_index_cn": "tencent_index",
            "em": "eastmoney",
            "east_money": "eastmoney",
            "tdx": "mootdx",
            "tongdaxin": "mootdx",
            "yfinance": "yahoo",
            "yahoo_finance": "yahoo",
            "cn_cache": "cn_local_cache",
            "local_cache": "cn_local_cache",
            "hk_cache": "hk_local_cache",
            "us_cache": "us_local_cache",
            "last_good": "us_local_cache",
            "last_good_cache": "us_local_cache",
        }
        src = aliases.get(src, src)
        if src and src != "auto":
            return [src] if src in PROVIDER_IDS else []
        return self._provider_order()

    @staticmethod
    def _provider_enabled(provider: str) -> bool:
        env_map = {
            "polygon": "PUBLIC_MARKET_POLYGON_ENABLED",
            "twelvedata": "PUBLIC_MARKET_TWELVEDATA_ENABLED",
            "tencent_hk": "PUBLIC_MARKET_TENCENT_HK_ENABLED",
            "tencent_index": "PUBLIC_MARKET_TENCENT_INDEX_ENABLED",
            "mootdx": "PUBLIC_MARKET_MOOTDX_ENABLED",
            "eastmoney": "PUBLIC_MARKET_EASTMONEY_ENABLED",
            "yahoo": "PUBLIC_MARKET_YAHOO_ENABLED",
            "akshare": "PUBLIC_MARKET_AKSHARE_ENABLED",
            "stooq": "PUBLIC_MARKET_STOOQ_ENABLED",
            "cn_local_cache": "PUBLIC_MARKET_CN_LOCAL_CACHE_ENABLED",
            "hk_local_cache": "PUBLIC_MARKET_HK_LOCAL_CACHE_ENABLED",
            "us_local_cache": "PUBLIC_MARKET_US_LOCAL_CACHE_ENABLED",
        }
        if provider == "mootdx" and importlib.util.find_spec("mootdx") is None:
            return False
        if provider in {"polygon", "twelvedata"} and not _provider_api_key(provider):
            return False
        return _env_bool(env_map.get(provider, f"PUBLIC_MARKET_{provider.upper()}_ENABLED"), "1")

    @staticmethod
    def _timeout_seconds() -> float:
        return max(0.5, min(10.0, _safe_float(os.getenv("PUBLIC_MARKET_DATA_TIMEOUT_SECONDS"), 2.5) or 2.5))

    def _quote_one(self, symbol: str, source: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        errors: list[dict[str, Any]] = []
        for provider in self._providers_for(source):
            if not self._provider_enabled(provider):
                continue
            try:
                item = self._quote_by_provider(provider, symbol)
            except Exception as exc:
                item = None
                errors.append({"symbol": symbol, "provider": provider, "error": str(exc)})
            if item:
                return item, errors
        if not errors:
            errors.append({"symbol": symbol, "error": "quote_unavailable"})
        return None, errors

    def _batch_quote_by_provider(self, provider: str, symbols: list[str]) -> list[dict[str, Any]]:
        if provider == "eastmoney":
            return self._eastmoney_quotes(symbols)
        return []

    def _eastmoney_rows(self, symbols: list[str]) -> list[dict[str, Any]]:
        groups: dict[str, list[str]] = {}
        for sym in symbols:
            for secid in _eastmoney_secids(sym):
                if not secid or "." not in secid:
                    continue
                market = secid.split(".", 1)[0]
                groups.setdefault(market, [])
                if secid not in groups[market]:
                    groups[market].append(secid)
        if not groups:
            return []
        all_rows: list[dict[str, Any]] = []
        for secids in groups.values():
            cache_key = f"eastmoney:{','.join(secids)}"
            cached = self._cache_get(cache_key)
            if cached is not None:
                all_rows.extend(cached)
                continue
            url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
            params = {
                "fltt": "2",
                "invt": "2",
                "fields": "f12,f14,f2,f3,f4,f5,f6,f15,f16,f17,f18",
                "secids": ",".join(secids),
            }
            headers = {"User-Agent": "Mozilla/5.0 public-market-data/1.0", "Referer": "https://quote.eastmoney.com/"}
            resp = requests.get(url, params=params, headers=headers, timeout=self._timeout_seconds())
            resp.raise_for_status()
            data = resp.json()
            payload = data.get("data") if isinstance(data, dict) else None
            rows = payload.get("diff") if isinstance(payload, dict) else None
            parsed = [x for x in rows if isinstance(x, dict)] if isinstance(rows, list) else []
            self._cache_set(cache_key, parsed, 10.0)
            all_rows.extend(parsed)
        return all_rows

    def _eastmoney_quotes(self, symbols: list[str]) -> list[dict[str, Any]]:
        syms = [canonical_symbol(sym) for sym in symbols]
        code_to_symbol: dict[str, str] = {}
        for sym in syms:
            for secid in _eastmoney_secids(sym):
                if not secid or "." not in secid:
                    continue
                code = secid.split(".", 1)[1].upper()
                code_to_symbol[code] = sym
                if code.isdigit():
                    code_to_symbol[code.lstrip("0") or code] = sym
                    code_to_symbol[code.zfill(5)] = sym
        rows = self._eastmoney_rows(syms)
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            code = str(row.get("f12") or "").strip().upper()
            sym = code_to_symbol.get(code) or code_to_symbol.get(code.lstrip("0") or code) or code_to_symbol.get(code.zfill(5))
            if not sym or sym in seen:
                continue
            item = self._eastmoney_quote_from_row(sym, row)
            if item:
                out.append(item)
                seen.add(sym)
        return out

    def _eastmoney_quote(self, symbol: str) -> dict[str, Any] | None:
        items = self._eastmoney_quotes([symbol])
        return items[0] if items else None

    @staticmethod
    def _eastmoney_quote_from_row(symbol: str, row: dict[str, Any]) -> dict[str, Any] | None:
        last = _safe_float(row.get("f2"))
        if last is None:
            return None
        prev = _safe_float(row.get("f18"))
        return {
            "symbol": canonical_symbol(symbol),
            "name": str(row.get("f14") or ""),
            "last": last,
            "prev_close": prev,
            "change_pct": _safe_float(row.get("f3"), _change_pct(last, prev)),
            "open": _safe_float(row.get("f17"), last),
            "high": _safe_float(row.get("f15"), last),
            "low": _safe_float(row.get("f16"), last),
            "volume": _safe_float(row.get("f5"), 0.0),
            "amount": _safe_float(row.get("f6"), None),
            "as_of": _now_iso(),
            "source": "eastmoney",
            "source_label": SOURCE_LABELS["eastmoney"],
            "price_type": "EastMoney public",
            "realtime": False,
            "cache": False,
        }

    def _eastmoney_klines(self, symbol: str, period: str, days: int, limit: int = 0) -> list[dict[str, Any]]:
        klt = _eastmoney_klt(period)
        secids = _eastmoney_secids(symbol)
        if not secids or not klt:
            return []
        days_i = max(1, min(3650, int(days or 180)))
        limit_i = max(0, min(5000, int(limit or 0)))
        end = date.today()
        begin = end.replace(year=end.year - 10).strftime("%Y%m%d") if days_i > 1825 else "0"
        lmt = limit_i or max(260, min(5000, int(days_i * 2.5)))
        for secid in secids:
            cache_key = f"eastmoney:kline:{secid}:{klt}:{days_i}:{lmt}:{begin}"
            cached = self._cache_get(cache_key)
            if cached is not None:
                if cached:
                    return cached
                continue
            url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
            params = {
                "secid": secid,
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57",
                "klt": klt,
                "fqt": "1",
                "beg": begin,
                "end": "20500101",
                "lmt": str(lmt),
            }
            headers = {"User-Agent": "Mozilla/5.0 public-market-data/1.0", "Referer": "https://quote.eastmoney.com/"}
            resp = requests.get(url, params=params, headers=headers, timeout=self._timeout_seconds())
            resp.raise_for_status()
            data = resp.json()
            payload = data.get("data") if isinstance(data, dict) else None
            raw_rows = payload.get("klines") if isinstance(payload, dict) else None
            if not isinstance(raw_rows, list):
                self._cache_set(cache_key, [], 30.0)
                continue
            out: list[dict[str, Any]] = []
            for raw in raw_rows:
                parts = str(raw or "").split(",")
                if len(parts) < 6:
                    continue
                close = _safe_float(parts[2])
                if close is None:
                    continue
                out.append(
                    {
                        "symbol": canonical_symbol(symbol),
                        "date": parts[0],
                        "open": _safe_float(parts[1], close),
                        "close": close,
                        "high": _safe_float(parts[3], close),
                        "low": _safe_float(parts[4], close),
                        "volume": _safe_float(parts[5], 0.0),
                        "amount": _safe_float(parts[6] if len(parts) > 6 else None, None),
                        "source": "eastmoney",
                    }
                )
            if days_i > 0:
                cutoff = date.today().toordinal() - days_i - 8
                filtered: list[dict[str, Any]] = []
                for row in out:
                    try:
                        if date.fromisoformat(str(row.get("date") or "")[:10]).toordinal() >= cutoff:
                            filtered.append(row)
                    except Exception:
                        filtered.append(row)
                out = filtered or out
            if limit_i > 0:
                out = out[-limit_i:]
            self._cache_set(cache_key, out, 60.0 if out else 30.0)
            if out:
                return out
        return []

    def _quote_by_provider(self, provider: str, symbol: str) -> dict[str, Any] | None:
        if provider == "polygon":
            return self._polygon_quote(symbol)
        if provider == "twelvedata":
            return self._twelvedata_quote(symbol)
        if provider == "tencent_hk":
            return self._tencent_hk_quote(symbol)
        if provider == "tencent_index":
            return self._cn_service_quote(symbol, source="tencent_index")
        if provider == "mootdx":
            return self._cn_service_quote(symbol, source="mootdx")
        if provider == "eastmoney":
            return self._eastmoney_quote(symbol)
        if provider == "yahoo":
            return self._yahoo_quote(symbol)
        if provider == "akshare":
            return self._akshare_quote(symbol)
        if provider == "stooq":
            return self._stooq_quote(symbol)
        if provider == "cn_local_cache":
            return self._cn_cache_quote(symbol)
        if provider == "hk_local_cache":
            return self._hk_cache_quote(symbol)
        if provider == "us_local_cache":
            return self._us_cache_quote(symbol)
        return None

    def _klines_by_provider(self, provider: str, symbol: str, period: str, days: int, limit: int) -> list[dict[str, Any]]:
        if provider == "mootdx" and _is_cn(symbol):
            return self._cn_service_klines(symbol, period=period, days=days, limit=limit, source="mootdx")
        if provider == "eastmoney":
            return self._eastmoney_klines(symbol, period=period, days=days, limit=limit)
        if provider == "yahoo":
            return self._yahoo_klines(symbol, period=period, days=days)
        if provider == "stooq":
            return self._stooq_klines(symbol, period=period, days=days)
        if provider == "akshare":
            if _is_cn(symbol):
                return self._cn_service_klines(symbol, period=period, days=days, limit=limit, source="akshare")
            return self._akshare_klines(symbol, period=period, days=days, limit=limit)
        if provider == "cn_local_cache" and _is_cn(symbol):
            return self._cn_service_klines(symbol, period=period, days=days, limit=limit, source="local_cache")
        return []

    @staticmethod
    def _last_good_path(symbol: str) -> str:
        return os.path.join(LAST_GOOD_CACHE_DIR, _cache_symbol_filename(symbol))

    def _save_last_good_quote(self, item: dict[str, Any]) -> None:
        if not isinstance(item, dict):
            return
        sym = canonical_symbol(str(item.get("symbol") or ""))
        if not sym or not (_is_us(sym) or _is_hk(sym)):
            return
        if str(item.get("source") or "") in {"us_local_cache", "hk_local_cache"}:
            return
        if _safe_float(item.get("last")) is None:
            return
        try:
            os.makedirs(LAST_GOOD_CACHE_DIR, exist_ok=True)
            payload = dict(item)
            payload["symbol"] = sym
            payload["cached_at"] = _now_iso()
            with open(self._last_good_path(sym), "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, default=str)
                f.write("\n")
        except Exception:
            pass

    def _market_cache_quote(self, symbol: str, *, expected_market: str, source: str, label: str) -> dict[str, Any] | None:
        sym = canonical_symbol(symbol)
        if expected_market == "us" and not _is_us(sym):
            return None
        if expected_market == "hk" and not _is_hk(sym):
            return None
        try:
            with open(self._last_good_path(sym), "r", encoding="utf-8") as f:
                item = json.load(f)
        except Exception:
            return None
        if not isinstance(item, dict):
            return None
        if _safe_float(item.get("last")) is None:
            return None
        out = dict(item)
        out["symbol"] = sym
        out["source"] = source
        out["source_label"] = label
        out["price_type"] = "Local last-good cache"
        out["cache"] = True
        out["realtime"] = False
        out["stale"] = True
        return out

    def _us_cache_quote(self, symbol: str) -> dict[str, Any] | None:
        return self._market_cache_quote(
            symbol,
            expected_market="us",
            source="us_local_cache",
            label=SOURCE_LABELS["us_local_cache"],
        )

    def _hk_cache_quote(self, symbol: str) -> dict[str, Any] | None:
        return self._market_cache_quote(
            symbol,
            expected_market="hk",
            source="hk_local_cache",
            label=SOURCE_LABELS["hk_local_cache"],
        )

    def _polygon_quote(self, symbol: str) -> dict[str, Any] | None:
        sym = canonical_symbol(symbol)
        if not _is_us(sym):
            return None
        api_key = _provider_api_key("polygon")
        if not api_key:
            return None
        code = _us_code(sym)
        cache_key = f"polygon:quote:{code}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        headers = {"User-Agent": "Mozilla/5.0 public-market-data/1.0"}
        last = prev = None
        as_of = _now_iso()
        try:
            resp = requests.get(
                f"https://api.polygon.io/v2/last/trade/{url_quote(code, safe='')}",
                params={"apiKey": api_key},
                headers=headers,
                timeout=self._timeout_seconds(),
            )
            resp.raise_for_status()
            data = resp.json()
            result = data.get("results") if isinstance(data, dict) else None
            if isinstance(result, dict):
                last = _safe_float(result.get("p") or result.get("price"))
                ts = result.get("t")
                if ts:
                    try:
                        as_of = datetime.fromtimestamp(float(ts) / 1_000_000_000.0, tz=timezone.utc).isoformat()
                    except Exception:
                        pass
        except Exception:
            last = None
        try:
            prev_resp = requests.get(
                f"https://api.polygon.io/v2/aggs/ticker/{url_quote(code, safe='')}/prev",
                params={"adjusted": "true", "apiKey": api_key},
                headers=headers,
                timeout=self._timeout_seconds(),
            )
            prev_resp.raise_for_status()
            prev_data = prev_resp.json()
            results = prev_data.get("results") if isinstance(prev_data, dict) else None
            if isinstance(results, list) and results:
                prev = _safe_float(results[0].get("c"))
                if last is None:
                    last = prev
        except Exception:
            prev = None
        if last is None:
            return None
        item = {
            "symbol": sym,
            "name": "",
            "last": last,
            "prev_close": prev,
            "change_pct": _change_pct(last, prev),
            "as_of": as_of,
            "source": "polygon",
            "source_label": SOURCE_LABELS["polygon"],
            "price_type": "Polygon market data",
            "realtime": True,
            "delayed_or_best_effort": True,
            "cache": False,
        }
        return self._cache_set(cache_key, item, 5.0)

    def _twelvedata_quote(self, symbol: str) -> dict[str, Any] | None:
        sym = canonical_symbol(symbol)
        if not _is_us(sym):
            return None
        api_key = _provider_api_key("twelvedata")
        if not api_key:
            return None
        code = _us_code(sym)
        cache_key = f"twelvedata:quote:{code}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        resp = requests.get(
            "https://api.twelvedata.com/quote",
            params={"symbol": code, "apikey": api_key},
            headers={"User-Agent": "Mozilla/5.0 public-market-data/1.0"},
            timeout=self._timeout_seconds(),
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict) or data.get("status") == "error":
            return None
        last = _safe_float(data.get("close") or data.get("price"))
        prev = _safe_float(data.get("previous_close"))
        if last is None:
            return None
        item = {
            "symbol": sym,
            "name": str(data.get("name") or ""),
            "last": last,
            "prev_close": prev,
            "change_pct": _safe_float(data.get("percent_change"), _change_pct(last, prev)),
            "open": _safe_float(data.get("open"), last),
            "high": _safe_float(data.get("high"), last),
            "low": _safe_float(data.get("low"), last),
            "volume": _safe_float(data.get("volume"), 0.0),
            "as_of": str(data.get("datetime") or _now_iso()),
            "source": "twelvedata",
            "source_label": SOURCE_LABELS["twelvedata"],
            "price_type": "Twelve Data market data",
            "realtime": True,
            "delayed_or_best_effort": True,
            "cache": False,
        }
        return self._cache_set(cache_key, item, 5.0)

    def _tencent_hk_quote(self, symbol: str) -> dict[str, Any] | None:
        sym = canonical_symbol(symbol)
        if not _is_hk(sym):
            return None
        code = _symbol_code(sym).upper()
        tencent_code = f"hk{code}"
        cache_key = f"tencent_hk:quote:{tencent_code}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        resp = requests.get(
            "https://qt.gtimg.cn/q=" + url_quote(tencent_code, safe=","),
            headers={"User-Agent": "Mozilla/5.0 public-market-data/1.0", "Referer": "https://gu.qq.com/"},
            timeout=self._timeout_seconds(),
        )
        resp.raise_for_status()
        text = resp.content.decode("gbk", errors="ignore")
        marker = f'v_{tencent_code}="'
        if marker not in text:
            return None
        payload = text.split(marker, 1)[1].split('";', 1)[0]
        parts = payload.split("~")
        if len(parts) < 35:
            return None
        last = _safe_float(parts[3])
        prev = _safe_float(parts[4])
        if last is None:
            return None
        item = {
            "symbol": sym,
            "name": parts[1] or sym,
            "last": last,
            "prev_close": prev,
            "change_pct": _safe_float(parts[32], _change_pct(last, prev)),
            "open": _safe_float(parts[5], last),
            "high": _safe_float(parts[33], last),
            "low": _safe_float(parts[34], last),
            "volume": _safe_float(parts[36] if len(parts) > 36 else None, 0.0),
            "amount": _safe_float(parts[37] if len(parts) > 37 else None, None),
            "as_of": parts[30] if len(parts) > 30 and parts[30] else _now_iso(),
            "source": "tencent_hk",
            "source_label": SOURCE_LABELS["tencent_hk"],
            "price_type": "Tencent HK public",
            "realtime": True,
            "delayed_or_best_effort": True,
            "cache": False,
        }
        return self._cache_set(cache_key, item, 5.0)

    def _cache_get(self, key: str) -> Any:
        now = time.monotonic()
        with self._cache_lock:
            item = self._cache.get(key)
            if not item:
                return None
            expire, value = item
            if expire <= now:
                self._cache.pop(key, None)
                return None
            return value

    def _cache_set(self, key: str, value: Any, ttl_seconds: float) -> Any:
        with self._cache_lock:
            self._cache[key] = (time.monotonic() + max(1.0, ttl_seconds), value)
        return value

    def _fetch_yahoo_result(self, symbol: str, *, days: int = 5, interval: str = "1d") -> dict[str, Any] | None:
        yahoo = _yahoo_symbol(symbol)
        range_name = _range_for_days(days)
        cache_key = f"yahoo:{yahoo}:{range_name}:{interval}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{url_quote(yahoo, safe='')}"
        params = {
            "range": range_name,
            "interval": interval,
            "includePrePost": "true",
            "events": "div,splits",
        }
        headers = {"User-Agent": "Mozilla/5.0 public-market-data/1.0"}
        resp = requests.get(url, params=params, headers=headers, timeout=self._timeout_seconds())
        resp.raise_for_status()
        data = resp.json()
        chart = data.get("chart") if isinstance(data, dict) else None
        if not isinstance(chart, dict) or chart.get("error"):
            return None
        results = chart.get("result")
        if not isinstance(results, list) or not results:
            return None
        return self._cache_set(cache_key, results[0], 20.0)

    def _bars_from_yahoo_result(self, symbol: str, result: dict[str, Any]) -> list[dict[str, Any]]:
        timestamps = result.get("timestamp")
        if not isinstance(timestamps, list) or not timestamps:
            return []
        indicators = result.get("indicators") if isinstance(result.get("indicators"), dict) else {}
        quotes = indicators.get("quote") if isinstance(indicators, dict) else None
        if not isinstance(quotes, list) or not quotes:
            return []
        quote = quotes[0]
        if not isinstance(quote, dict):
            return []
        opens = quote.get("open") if isinstance(quote.get("open"), list) else []
        highs = quote.get("high") if isinstance(quote.get("high"), list) else []
        lows = quote.get("low") if isinstance(quote.get("low"), list) else []
        closes = quote.get("close") if isinstance(quote.get("close"), list) else []
        volumes = quote.get("volume") if isinstance(quote.get("volume"), list) else []
        out: list[dict[str, Any]] = []
        for idx, ts in enumerate(timestamps):
            close = _safe_float(closes[idx] if idx < len(closes) else None)
            if close is None:
                continue
            out.append(
                {
                    "symbol": canonical_symbol(symbol),
                    "date": _iso_from_timestamp(ts),
                    "open": _safe_float(opens[idx] if idx < len(opens) else None, close),
                    "high": _safe_float(highs[idx] if idx < len(highs) else None, close),
                    "low": _safe_float(lows[idx] if idx < len(lows) else None, close),
                    "close": close,
                    "volume": _safe_float(volumes[idx] if idx < len(volumes) else None, 0.0),
                    "source": "yahoo",
                }
            )
        return out

    def _yahoo_klines(self, symbol: str, period: str, days: int) -> list[dict[str, Any]]:
        result = self._fetch_yahoo_result(symbol, days=days, interval=_yahoo_interval(period))
        if not result:
            return []
        return self._bars_from_yahoo_result(symbol, result)

    def _yahoo_quote(self, symbol: str) -> dict[str, Any] | None:
        result = self._fetch_yahoo_result(symbol, days=7, interval="1d")
        if not result:
            return None
        meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
        rows = self._bars_from_yahoo_result(symbol, result)
        item = _quote_from_bars(symbol, rows, "yahoo")
        if not item:
            return None
        last = _safe_float(meta.get("regularMarketPrice"), item.get("last"))
        prev = _safe_float(meta.get("chartPreviousClose"), _safe_float(meta.get("previousClose"), item.get("prev_close")))
        item.update(
            {
                "last": last,
                "prev_close": prev,
                "change_pct": _change_pct(last, prev),
                "open": _safe_float(meta.get("regularMarketDayOpen"), item.get("open")),
                "high": _safe_float(meta.get("regularMarketDayHigh"), item.get("high")),
                "low": _safe_float(meta.get("regularMarketDayLow"), item.get("low")),
                "as_of": _iso_from_timestamp(meta.get("regularMarketTime")) if meta.get("regularMarketTime") else item.get("as_of"),
                "source": "yahoo",
                "source_label": SOURCE_LABELS["yahoo"],
                "price_type": "Yahoo delayed/public",
                "realtime": False,
                "cache": False,
            }
        )
        return item

    def _stooq_klines(self, symbol: str, period: str, days: int) -> list[dict[str, Any]]:
        stooq = _stooq_symbol(symbol)
        if not stooq:
            return []
        interval = _stooq_interval(period)
        cache_key = f"stooq:{stooq}:{interval}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            rows = cached
        else:
            url = "https://stooq.com/q/d/l/"
            resp = requests.get(url, params={"s": stooq, "i": interval}, timeout=self._timeout_seconds())
            resp.raise_for_status()
            text = str(resp.text or "")
            if "No data" in text or not text.strip():
                return []
            rows = []
            for row in csv.DictReader(io.StringIO(text)):
                close = _safe_float(row.get("Close"))
                if close is None:
                    continue
                rows.append(
                    {
                        "symbol": canonical_symbol(symbol),
                        "date": str(row.get("Date") or ""),
                        "open": _safe_float(row.get("Open"), close),
                        "high": _safe_float(row.get("High"), close),
                        "low": _safe_float(row.get("Low"), close),
                        "close": close,
                        "volume": _safe_float(row.get("Volume"), 0.0),
                        "source": "stooq",
                    }
                )
            rows = self._cache_set(cache_key, rows, 300.0)
        return list(rows)[-max(2, min(5000, int(days or 180) * 2)) :]

    def _stooq_quote(self, symbol: str) -> dict[str, Any] | None:
        return _quote_from_bars(symbol, self._stooq_klines(symbol, period="1d", days=10), "stooq")

    def _akshare_records(self, fn_name: str) -> list[dict[str, Any]]:
        cache_key = f"akshare:{fn_name}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        import akshare as ak  # type: ignore

        fn = getattr(ak, fn_name, None)
        if not callable(fn):
            return []
        rows = _records_from_frame(fn())
        return self._cache_set(cache_key, rows, 30.0)

    def _akshare_quote(self, symbol: str) -> dict[str, Any] | None:
        if importlib.util.find_spec("akshare") is None:
            return None
        functions: list[str]
        if _is_hk(symbol):
            functions = ["stock_hk_index_spot_em", "stock_hk_spot_em", "stock_hk_spot"]
        elif _is_cn(symbol):
            functions = ["stock_zh_index_spot_em", "index_zh_a_spot_em", "stock_zh_a_spot_em"]
        elif _is_us(symbol):
            functions = ["stock_us_spot_em"]
        else:
            functions = []
        for fn_name in functions:
            try:
                rows = self._akshare_records(fn_name)
            except Exception:
                rows = []
            item = self._akshare_quote_from_rows(symbol, rows)
            if item:
                return item
        return None

    @staticmethod
    def _akshare_period(period: str) -> str:
        p = str(period or "1d").strip().lower()
        if p in {"1w", "week", "weekly", "w"}:
            return "weekly"
        if p in {"1mo", "1mth", "month", "monthly", "m"}:
            return "monthly"
        return "daily"

    @staticmethod
    def _akshare_start_end(days: int) -> tuple[str, str]:
        end_d = date.today()
        start_d = end_d - timedelta(days=max(30, min(3650, int(days or 180))) * 2)
        return start_d.strftime("%Y%m%d"), end_d.strftime("%Y%m%d")

    @staticmethod
    def _akshare_hist_rows_to_klines(symbol: str, rows: list[dict[str, Any]], source: str = "akshare") -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for row in rows:
            dt_raw = _pick(row, "date", "日期", "trade_date", "day", "datetime", "time")
            dt = str(dt_raw or "").strip()
            if not dt:
                continue
            close = _safe_float(_pick(row, "close", "收盘", "最新价", "现价", "Close"))
            if close is None:
                continue
            out.append(
                {
                    "symbol": canonical_symbol(symbol),
                    "date": dt[:10],
                    "open": _safe_float(_pick(row, "open", "开盘", "Open"), close),
                    "high": _safe_float(_pick(row, "high", "最高", "High"), close),
                    "low": _safe_float(_pick(row, "low", "最低", "Low"), close),
                    "close": close,
                    "volume": _safe_float(_pick(row, "volume", "成交量", "vol", "Volume"), 0.0),
                    "amount": _safe_float(_pick(row, "amount", "成交额", "turnover", "Amount"), None),
                    "source": source,
                }
            )
        out.sort(key=lambda x: str(x.get("date") or ""))
        return out

    @staticmethod
    def _akshare_call(fn: Any, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not callable(fn):
            return []
        for kwargs in candidates:
            try:
                rows = _records_from_frame(fn(**kwargs))
            except TypeError:
                reduced = {k: v for k, v in kwargs.items() if k in {"symbol", "period", "adjust"}}
                try:
                    rows = _records_from_frame(fn(**reduced))
                except Exception:
                    rows = []
            except Exception:
                rows = []
            if rows:
                return rows
        return []

    def _akshare_klines(self, symbol: str, period: str, days: int, limit: int) -> list[dict[str, Any]]:
        if importlib.util.find_spec("akshare") is None:
            return []
        import akshare as ak  # type: ignore

        sym = canonical_symbol(symbol)
        start_date, end_date = self._akshare_start_end(days)
        ak_period = self._akshare_period(period)
        candidates: list[tuple[str, list[dict[str, Any]]]] = []
        if _is_hk(sym):
            code = _symbol_code(sym)
            if code.isdigit():
                code = f"{int(code):05d}"
            candidates = [
                (
                    "stock_hk_hist",
                    [
                        {
                            "symbol": code,
                            "period": ak_period,
                            "start_date": start_date,
                            "end_date": end_date,
                            "adjust": "qfq",
                        },
                        {"symbol": code, "period": ak_period, "start_date": start_date, "end_date": end_date},
                        {"symbol": code},
                    ],
                ),
                ("stock_hk_daily", [{"symbol": code, "adjust": "qfq"}, {"symbol": code}]),
            ]
        elif _is_us(sym):
            code = _symbol_code(sym).upper()
            us_symbols = [code]
            for secid in _eastmoney_secids(sym):
                sec_code = secid.split(".", 1)[1].upper()
                if sec_code not in us_symbols:
                    us_symbols.append(sec_code)
                if secid not in us_symbols:
                    us_symbols.append(secid)
            candidates = []
            for code_candidate in us_symbols:
                candidates.append(
                    (
                        "stock_us_hist",
                        [
                            {
                                "symbol": code_candidate,
                                "period": ak_period,
                                "start_date": start_date,
                                "end_date": end_date,
                                "adjust": "qfq",
                            },
                            {
                                "symbol": code_candidate,
                                "period": ak_period,
                                "start_date": start_date,
                                "end_date": end_date,
                            },
                            {"symbol": code_candidate},
                        ],
                    )
                )
                candidates.append(("stock_us_daily", [{"symbol": code_candidate, "adjust": "qfq"}, {"symbol": code_candidate}]))
        else:
            return []

        cache_key = f"akshare:kline:{sym}:{ak_period}:{days}:{limit}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        for fn_name, kwargs_list in candidates:
            fn = getattr(ak, fn_name, None)
            rows = self._akshare_call(fn, kwargs_list)
            out = self._akshare_hist_rows_to_klines(sym, rows)
            if days > 0:
                cutoff = date.today().toordinal() - max(1, int(days or 180)) - 8
                filtered: list[dict[str, Any]] = []
                for row in out:
                    try:
                        if date.fromisoformat(str(row.get("date") or "")[:10]).toordinal() >= cutoff:
                            filtered.append(row)
                    except Exception:
                        filtered.append(row)
                out = filtered or out
            if limit > 0:
                out = out[-limit:]
            if out:
                return self._cache_set(cache_key, out, 120.0)
        return self._cache_set(cache_key, [], 30.0)

    def _akshare_quote_from_rows(self, symbol: str, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        sym = canonical_symbol(symbol)
        target_code = _symbol_code(sym).upper()
        aliases = {
            target_code,
            target_code.lstrip("0") or target_code,
            target_code.zfill(4),
            target_code.zfill(5),
            target_code.zfill(6),
            sym,
            _yahoo_symbol(sym).upper(),
        }
        for row in rows:
            row_code = str(_pick(row, "代码", "code", "symbol", "证券代码") or "").strip().upper()
            row_name = str(_pick(row, "名称", "name", "证券简称", "股票简称") or "").strip()
            row_code_cmp = row_code.replace(".", "").replace("_", "")
            alias_hit = row_code in aliases or row_code.lstrip("0") in aliases or row_code_cmp in aliases
            if not alias_hit and target_code not in row_code and target_code not in row_name.upper():
                continue
            last = _safe_float(_pick(row, "最新价", "最新", "现价", "close", "收盘"))
            if last is None:
                continue
            prev = _safe_float(_pick(row, "昨收", "prev_close", "previous_close"))
            return {
                "symbol": sym,
                "name": row_name,
                "last": last,
                "prev_close": prev,
                "change_pct": _safe_float(_pick(row, "涨跌幅", "change_pct", "pct_chg")),
                "open": _safe_float(_pick(row, "今开", "开盘", "open")),
                "high": _safe_float(_pick(row, "最高", "high")),
                "low": _safe_float(_pick(row, "最低", "low")),
                "volume": _safe_float(_pick(row, "成交量", "volume", "vol")),
                "amount": _safe_float(_pick(row, "成交额", "amount")),
                "as_of": _now_iso(),
                "source": "akshare",
                "source_label": SOURCE_LABELS["akshare"],
                "price_type": "AkShare public",
                "realtime": False,
                "cache": False,
            }
        return None

    def _cn_cache_quote(self, symbol: str) -> dict[str, Any] | None:
        if not _is_cn(symbol):
            return None
        try:
            from api.services.cn_market_data_service import get_cn_market_data_service

            resp = get_cn_market_data_service().quote(symbols=[symbol], source="local_cache")
        except Exception:
            return None
        items = resp.get("items") if isinstance(resp, dict) else None
        if not isinstance(items, list) or not items:
            return None
        item = dict(items[0])
        item["source"] = "cn_local_cache"
        item["source_label"] = SOURCE_LABELS["cn_local_cache"]
        item["price_type"] = "Local cache"
        return item

    @staticmethod
    def _cn_service_quote(symbol: str, source: str) -> dict[str, Any] | None:
        if not _is_cn(symbol):
            return None
        try:
            from api.services.cn_market_data_service import get_cn_market_data_service

            resp = get_cn_market_data_service().quote(symbols=[symbol], source=source)
        except Exception:
            return None
        items = resp.get("items") if isinstance(resp, dict) else None
        if not isinstance(items, list) or not items:
            return None
        item = dict(items[0])
        item["symbol"] = canonical_symbol(symbol)
        item["source"] = source
        item["source_label"] = SOURCE_LABELS.get(source, source)
        item["price_type"] = SOURCE_LABELS.get(source, source)
        return item

    @staticmethod
    def _cn_service_klines(symbol: str, period: str, days: int, limit: int, source: str) -> list[dict[str, Any]]:
        try:
            from api.services.cn_market_data_service import get_cn_market_data_service

            resp = get_cn_market_data_service().klines(
                symbol=symbol,
                period=period,
                adjust="qfq",
                days=days,
                limit=limit,
                source=source,
            )
        except Exception:
            return []
        items = resp.get("items") if isinstance(resp, dict) else None
        if not isinstance(items, list):
            return []
        out: list[dict[str, Any]] = []
        for row in items:
            if isinstance(row, dict):
                item = dict(row)
                item["symbol"] = canonical_symbol(symbol)
                item["source"] = "cn_local_cache" if source == "local_cache" else source
                out.append(item)
        return out

    def _data_status(self, items: list[dict[str, Any]], provider: str = "auto") -> dict[str, Any]:
        sources = []
        for item in items:
            source = str(item.get("source") or "").strip()
            if source and source not in sources:
                sources.append(source)
        if provider != "auto" and provider in PROVIDER_IDS and provider not in sources:
            sources.append(provider)
        labels = [SOURCE_LABELS.get(src, src) for src in sources]
        return {
            "mode": "public",
            "sources": sources,
            "source_label": ", ".join(labels) if labels else "Unknown",
            "realtime": any(bool(x.get("realtime")) for x in items),
            "delayed_or_best_effort": True,
            "broker_required": False,
        }


_PUBLIC_MARKET_DATA_SERVICE: PublicMarketDataService | None = None
_PUBLIC_MARKET_DATA_SERVICE_LOCK = threading.Lock()


def get_public_market_data_service() -> PublicMarketDataService:
    global _PUBLIC_MARKET_DATA_SERVICE
    with _PUBLIC_MARKET_DATA_SERVICE_LOCK:
        if _PUBLIC_MARKET_DATA_SERVICE is None:
            _PUBLIC_MARKET_DATA_SERVICE = PublicMarketDataService()
        return _PUBLIC_MARKET_DATA_SERVICE
