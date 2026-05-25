from __future__ import annotations

import glob
import importlib.util
import json
import math
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KLINE_CACHE_DIR = os.path.join(ROOT, "data", "klines")

SCHEMA = "cn_market_data.v1"
DEFAULT_PROVIDER_ORDER = "tencent_index,mootdx,local_cache,akshare,tushare,baostock"
PROVIDER_IDS = ("tencent_index", "mootdx", "local_cache", "akshare", "tushare", "baostock")
CN_INDEX_SYMBOLS = {"000001.SH", "399001.SZ", "399006.SZ"}
MOOTDX_FALLBACK_SERVERS: tuple[tuple[str, int], ...] = (
    ("110.41.147.114", 7709),
    ("8.129.13.54", 7709),
    ("120.24.149.49", 7709),
    ("47.113.94.204", 7709),
    ("124.70.176.52", 7709),
    ("47.100.236.28", 7709),
)


def _env_bool(key: str, default: str = "1") -> bool:
    return str(os.getenv(key, default)).strip().lower() in {"1", "true", "yes", "on"}


def _safe_float(v: Any, default: float | None = None) -> float | None:
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


def _change_pct(last: float | None, prev: float | None) -> float | None:
    if last is None or prev is None or prev == 0:
        return None
    try:
        return round((float(last) - float(prev)) / float(prev) * 100.0, 2)
    except Exception:
        return None


def normalize_cn_symbol(symbol: str) -> str:
    raw = str(symbol or "").strip().upper()
    if not raw:
        return ""
    raw = raw.replace("_", ".")
    if raw.startswith(("SH.", "SZ.", "BJ.")):
        market, code = raw.split(".", 1)
        return f"{code}.{market}"
    if "." in raw:
        code, market = raw.split(".", 1)
        market = market.upper()
        if market in {"SH", "SZ", "BJ"}:
            return f"{code.zfill(6)}.{market}"
        return raw
    code = "".join(ch for ch in raw if ch.isdigit())
    if not code:
        return raw
    code = code.zfill(6)
    if code.startswith(("6", "9")):
        market = "SH"
    elif code.startswith(("4", "8")):
        market = "BJ"
    else:
        market = "SZ"
    return f"{code}.{market}"


def _symbol_code(symbol: str) -> str:
    return normalize_cn_symbol(symbol).split(".", 1)[0]


def _baostock_code(symbol: str) -> str:
    sym = normalize_cn_symbol(symbol)
    code, market = sym.split(".", 1)
    return f"{market.lower()}.{code}"


def _tushare_code(symbol: str) -> str:
    return normalize_cn_symbol(symbol)


def _mootdx_market(symbol: str) -> int:
    sym = normalize_cn_symbol(symbol)
    market = sym.split(".", 1)[1] if "." in sym else ""
    return 1 if market == "SH" else 0


def _mootdx_period_category(period: str) -> int:
    p = str(period or "1d").strip().lower()
    mapping = {
        "1d": 9,
        "day": 9,
        "daily": 9,
        "d": 9,
        "days": 4,
        "1w": 5,
        "week": 5,
        "weekly": 5,
        "w": 5,
        "1mo": 6,
        "1mth": 6,
        "month": 6,
        "monthly": 6,
        "m": 6,
        "1m": 7,
        "1min": 7,
        "5m": 8,
        "5min": 8,
        "15m": 9,
        "15min": 9,
        "30m": 10,
        "30min": 10,
        "1h": 11,
        "60m": 11,
        "60min": 11,
    }
    return mapping.get(p, 4)


def _tencent_code(symbol: str) -> str:
    sym = normalize_cn_symbol(symbol)
    code, market = sym.split(".", 1)
    prefix = "sh" if market == "SH" else "bj" if market == "BJ" else "sz"
    return f"{prefix}{code}"


def _is_cn_index(symbol: str) -> bool:
    return normalize_cn_symbol(symbol) in CN_INDEX_SYMBOLS


def _cache_stem(symbol: str) -> str:
    return normalize_cn_symbol(symbol).replace(".", "_")


def _parse_dt(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    s = str(v or "").strip()
    if not s:
        return datetime.min
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    return datetime.min


def _parse_tencent_timestamp(v: Any) -> str | None:
    s = str(v or "").strip()
    if not s:
        return None
    for fmt in ("%Y%m%d%H%M%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).isoformat()
        except Exception:
            continue
    return s


def _period_for_akshare(period: str) -> str:
    p = str(period or "1d").strip().lower()
    if p in {"1d", "day", "daily", "d"}:
        return "daily"
    if p in {"1w", "week", "weekly", "w"}:
        return "weekly"
    if p in {"1mo", "1mth", "month", "monthly", "m"}:
        return "monthly"
    return "daily"


def _frequency_for_baostock(period: str) -> str:
    p = str(period or "1d").strip().lower()
    if p in {"1w", "week", "weekly", "w"}:
        return "w"
    if p in {"1mo", "1mth", "month", "monthly", "m"}:
        return "m"
    return "d"


def _adjust_for_baostock(adjust: str) -> str:
    # BaoStock: 1 = post-adjust, 2 = pre-adjust, 3 = raw.
    a = str(adjust or "").strip().lower()
    if a in {"qfq", "forward", "pre"}:
        return "2"
    if a in {"hfq", "backward", "post"}:
        return "1"
    return "3"


def _adjust_for_akshare(adjust: str) -> str:
    a = str(adjust or "").strip().lower()
    if a in {"qfq", "hfq"}:
        return a
    return ""


def _records_from_frame(df: Any) -> list[dict[str, Any]]:
    if df is None:
        return []
    if hasattr(df, "to_dict"):
        try:
            rows = df.to_dict(orient="records")
            return [x for x in rows if isinstance(x, dict)]
        except Exception:
            return []
    if isinstance(df, list):
        return [x for x in df if isinstance(x, dict)]
    return []


def _records_from_frame_with_index(df: Any) -> list[dict[str, Any]]:
    if df is None:
        return []
    if hasattr(df, "reset_index"):
        try:
            rows = df.reset_index().to_dict(orient="records")
            return [x for x in rows if isinstance(x, dict)]
        except Exception:
            return _records_from_frame(df)
    return _records_from_frame(df)


def _pick(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row:
            return row.get(key)
    return None


def _normalize_kline_row(row: dict[str, Any], *, symbol: str, source: str) -> dict[str, Any] | None:
    dt_raw = _pick(row, "date", "日期", "trade_date", "day", "datetime", "time")
    dt = _parse_dt(dt_raw)
    if dt == datetime.min:
        dt = _parse_dt(row.get("datetime") or row.get("date") or row.get("index"))
    if dt == datetime.min:
        return None
    open_v = _safe_float(_pick(row, "open", "开盘", "open_price"))
    high_v = _safe_float(_pick(row, "high", "最高", "high_price"))
    low_v = _safe_float(_pick(row, "low", "最低", "low_price"))
    close_v = _safe_float(_pick(row, "close", "收盘", "close_price"))
    if close_v is None:
        return None
    return {
        "symbol": normalize_cn_symbol(symbol),
        "date": dt.isoformat(),
        "open": open_v if open_v is not None else close_v,
        "high": high_v if high_v is not None else close_v,
        "low": low_v if low_v is not None else close_v,
        "close": close_v,
        "volume": _safe_float(_pick(row, "volume", "成交量", "vol"), 0.0),
        "amount": _safe_float(_pick(row, "amount", "成交额", "turnover"), None),
        "source": source,
    }


def _quote_from_kline(symbol: str, items: list[dict[str, Any]], source: str) -> dict[str, Any] | None:
    if not items:
        return None
    rows = sorted(items, key=lambda x: _parse_dt(x.get("date")))
    last = rows[-1]
    prev = rows[-2] if len(rows) >= 2 else None
    last_close = _safe_float(last.get("close"))
    if last_close is None:
        return None
    prev_close = _safe_float(prev.get("close")) if prev else None
    change_pct = None
    if prev_close and prev_close != 0:
        change_pct = round((last_close - prev_close) / prev_close * 100.0, 4)
    return {
        "symbol": normalize_cn_symbol(symbol),
        "name": "",
        "last": last_close,
        "change_pct": change_pct,
        "open": last.get("open"),
        "high": last.get("high"),
        "low": last.get("low"),
        "prev_close": prev_close,
        "volume": last.get("volume"),
        "amount": last.get("amount"),
        "as_of": last.get("date"),
        "source": source,
        "realtime": False,
        "cache": True,
    }


class CnMarketDataService:
    def provider_status(self) -> dict[str, Any]:
        order = self._provider_order()
        providers = [
            {
                "id": "tencent_index",
                "name": "Tencent A 股指数",
                "enabled": _env_bool("CN_MARKET_TENCENT_INDEX_ENABLED", "1"),
                "configured": True,
                "installed": True,
                "status_text": "可用" if _env_bool("CN_MARKET_TENCENT_INDEX_ENABLED", "1") else "已禁用",
                "setup_hint": "无需 API Key；使用 Tencent 公共指数快照，修正 000001.SH 等指数代码冲突。",
                "role": "A 股核心指数实时快照兜底",
                "capabilities": ["quote", "indices"],
                "priority": order.index("tencent_index") if "tencent_index" in order else 999,
            },
            {
                "id": "mootdx",
                "name": "mootdx / Tongdaxin public",
                "enabled": _env_bool("CN_MARKET_MOOTDX_ENABLED", "1"),
                "configured": importlib.util.find_spec("mootdx") is not None,
                "installed": importlib.util.find_spec("mootdx") is not None,
                "status_text": self._provider_status_text(
                    enabled=_env_bool("CN_MARKET_MOOTDX_ENABLED", "1"),
                    installed=importlib.util.find_spec("mootdx") is not None,
                    token_required=False,
                    token_present=True,
                ),
                "setup_hint": "Install mootdx in the backend Python environment. No broker account or API key is required.",
                "role": "Mainland-network A-share quote/K-line provider; useful when AkShare public endpoints are unstable.",
                "capabilities": ["quote", "klines", "minute_klines"],
                "priority": order.index("mootdx") if "mootdx" in order else 999,
            },
            {
                "id": "local_cache",
                "name": "本地 K 线缓存",
                "enabled": True,
                "configured": os.path.isdir(KLINE_CACHE_DIR),
                "installed": True,
                "status_text": "可用" if os.path.isdir(KLINE_CACHE_DIR) else "缓存目录缺失",
                "setup_hint": "无需配置；由回测中心或历史 K 线缓存自动生成。",
                "role": "默认兜底，适合回测和离线演示",
                "capabilities": ["klines", "quote_from_cache", "universe_from_cache"],
                "priority": order.index("local_cache") if "local_cache" in order else 999,
            },
            {
                "id": "akshare",
                "name": "AkShare",
                "enabled": _env_bool("CN_MARKET_AKSHARE_ENABLED", "1"),
                "configured": importlib.util.find_spec("akshare") is not None,
                "installed": importlib.util.find_spec("akshare") is not None,
                "status_text": self._provider_status_text(
                    enabled=_env_bool("CN_MARKET_AKSHARE_ENABLED", "1"),
                    installed=importlib.util.find_spec("akshare") is not None,
                    token_required=False,
                    token_present=True,
                ),
                "setup_hint": "无需 API Key；在后端 Python 环境安装 akshare 后重启后端即可。",
                "role": "免费广覆盖，适合 A 股快照、K 线、指数、板块等增强",
                "capabilities": ["quote", "klines", "indices", "sectors"],
                "priority": order.index("akshare") if "akshare" in order else 999,
            },
            {
                "id": "tushare",
                "name": "Tushare Pro",
                "enabled": _env_bool("CN_MARKET_TUSHARE_ENABLED", "1"),
                "configured": bool(os.getenv("TUSHARE_TOKEN")) and importlib.util.find_spec("tushare") is not None,
                "installed": importlib.util.find_spec("tushare") is not None,
                "status_text": self._provider_status_text(
                    enabled=_env_bool("CN_MARKET_TUSHARE_ENABLED", "1"),
                    installed=importlib.util.find_spec("tushare") is not None,
                    token_required=True,
                    token_present=bool(os.getenv("TUSHARE_TOKEN")),
                ),
                "setup_hint": "需要安装 tushare，并在 Setup 页填写 TUSHARE_TOKEN，保存后重启后端。",
                "role": "系统化研究数据，适合财务、复权、交易日历和因子",
                "capabilities": ["klines", "fundamentals", "calendar", "factors"],
                "priority": order.index("tushare") if "tushare" in order else 999,
            },
            {
                "id": "baostock",
                "name": "BaoStock",
                "enabled": _env_bool("CN_MARKET_BAOSTOCK_ENABLED", "1"),
                "configured": importlib.util.find_spec("baostock") is not None,
                "installed": importlib.util.find_spec("baostock") is not None,
                "status_text": self._provider_status_text(
                    enabled=_env_bool("CN_MARKET_BAOSTOCK_ENABLED", "1"),
                    installed=importlib.util.find_spec("baostock") is not None,
                    token_required=False,
                    token_present=True,
                ),
                "setup_hint": "无需 API Key；在后端 Python 环境安装 baostock 后重启后端即可。",
                "role": "免费历史行情兜底，适合日线/周线/月线回测",
                "capabilities": ["klines"],
                "priority": order.index("baostock") if "baostock" in order else 999,
            },
        ]
        providers.sort(key=lambda x: int(x["priority"]))
        return {
            "ok": True,
            "schema": SCHEMA,
            "market": "cn",
            "provider_order": order,
            "providers": providers,
            "valuation_providers": [
                {
                    "id": "tencent",
                    "name": "Tencent public valuation",
                    "enabled": _env_bool("CN_MARKET_TENCENT_ENABLED", "1"),
                    "configured": True,
                    "installed": True,
                    "status_text": "available" if _env_bool("CN_MARKET_TENCENT_ENABLED", "1") else "disabled",
                    "setup_hint": "No broker account, API key, or Python package is required.",
                    "role": "A-share PE/PB/market-cap supplement for research and TradingAgents fundamentals.",
                    "capabilities": ["valuation", "quote_snapshot"],
                    "priority": 0,
                }
            ],
            "notes": [
                "默认不把免费数据源标记为实盘级数据。",
                "如需优先联网数据，可设置 CN_MARKET_DATA_PROVIDER_ORDER=mootdx,local_cache,akshare,tushare,baostock。",
            ],
        }

    @staticmethod
    def _provider_status_text(*, enabled: bool, installed: bool, token_required: bool, token_present: bool) -> str:
        if not enabled:
            return "已停用"
        if not installed:
            return "未安装"
        if token_required and not token_present:
            return "缺 Token"
        return "可用"

    def quote(self, symbols: str | list[str], source: str = "auto") -> dict[str, Any]:
        syms = self._symbols(symbols)
        items: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for sym in syms:
            item = self._quote_one(sym, source=source)
            if item:
                items.append(item)
            else:
                errors.append({"symbol": sym, "error": "quote_unavailable"})
        return {
            "ok": bool(items),
            "schema": SCHEMA,
            "market": "cn",
            "source": source,
            "items": items,
            "errors": errors,
            "data_status": self._data_status(items),
        }

    def klines(
        self,
        symbol: str,
        period: str = "1d",
        adjust: str = "qfq",
        days: int = 180,
        limit: int = 0,
        source: str = "auto",
    ) -> dict[str, Any]:
        sym = normalize_cn_symbol(symbol)
        lim = max(0, min(5000, int(limit or 0)))
        days_i = max(1, min(3650, int(days or 180)))
        tried: list[str] = []
        errors: list[dict[str, str]] = []
        for provider in self._providers_for(source):
            tried.append(provider)
            try:
                rows = self._klines_by_provider(provider, sym, period=period, adjust=adjust, days=days_i, limit=lim)
            except Exception as exc:
                rows = []
                errors.append({"provider": provider, "error": str(exc)})
            if rows:
                rows = sorted(rows, key=lambda x: _parse_dt(x.get("date")))
                if lim > 0:
                    rows = rows[-lim:]
                return {
                    "ok": True,
                    "schema": SCHEMA,
                    "market": "cn",
                    "symbol": sym,
                    "period": period,
                    "adjust": adjust,
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
            "market": "cn",
            "symbol": sym,
            "period": period,
            "adjust": adjust,
            "source": source,
            "items": [],
            "bar_count": 0,
            "tried": tried,
            "errors": errors or [{"provider": source, "error": "klines_unavailable"}],
            "data_status": {"realtime": False, "cache": False, "stale": True},
        }

    def universe(self, market: str = "cn") -> dict[str, Any]:
        symbols = set(self._cache_symbols())
        try:
            from api.auto_trader import AutoTraderService

            cfg = AutoTraderService().get_config()
            uni = cfg.get("universe") if isinstance(cfg, dict) else {}
            if isinstance(uni, dict):
                for s in uni.get("cn", []) or []:
                    sym = normalize_cn_symbol(str(s))
                    if sym:
                        symbols.add(sym)
        except Exception:
            pass
        items = [{"symbol": s, "market": "cn"} for s in sorted(symbols)]
        return {"ok": True, "schema": SCHEMA, "market": market, "source": "local_config_cache", "items": items, "count": len(items)}

    def valuation(self, symbol: str, source: str = "auto") -> dict[str, Any]:
        sym = normalize_cn_symbol(symbol)
        src = str(source or "auto").strip().lower()
        providers = ["tencent"] if src in {"", "auto", "tencent"} else [src]
        tried: list[str] = []
        errors: list[dict[str, str]] = []
        for provider in providers:
            tried.append(provider)
            if provider != "tencent":
                errors.append({"provider": provider, "error": "unsupported_valuation_provider"})
                continue
            if not _env_bool("CN_MARKET_TENCENT_ENABLED", "1"):
                errors.append({"provider": provider, "error": "provider_disabled"})
                continue
            try:
                item = self._tencent_valuation(sym)
            except Exception as exc:
                item = None
                errors.append({"provider": provider, "error": str(exc)})
            if item:
                return {
                    "ok": True,
                    "schema": SCHEMA,
                    "market": "cn",
                    "symbol": sym,
                    "source": provider,
                    "item": item,
                    "tried": tried,
                    "errors": errors,
                    "data_status": {"source": provider, "realtime": False, "cache": False, "stale": False},
                }
        return {
            "ok": False,
            "schema": SCHEMA,
            "market": "cn",
            "symbol": sym,
            "source": source,
            "item": None,
            "tried": tried,
            "errors": errors or [{"provider": source, "error": "valuation_unavailable"}],
            "data_status": {"source": source, "realtime": False, "cache": False, "stale": True},
        }

    def _provider_order(self) -> list[str]:
        raw = str(os.getenv("CN_MARKET_DATA_PROVIDER_ORDER", DEFAULT_PROVIDER_ORDER) or DEFAULT_PROVIDER_ORDER)
        aliases = {"tdx": "mootdx", "tongdaxin": "mootdx", "cn_cache": "local_cache", "tencent": "tencent_index", "qq": "tencent_index"}
        order: list[str] = []
        for item in raw.split(","):
            provider = aliases.get(item.strip().lower(), item.strip().lower())
            if provider in PROVIDER_IDS and provider not in order:
                order.append(provider)
        for provider in PROVIDER_IDS:
            if provider not in order:
                order.append(provider)
        front = [p for p in ("tencent_index",) if p in order]
        return front + [p for p in order if p not in front]

    def _providers_for(self, source: str) -> list[str]:
        src = str(source or "auto").strip().lower()
        src = {"tdx": "mootdx", "tongdaxin": "mootdx", "cn_cache": "local_cache", "tencent": "tencent_index", "qq": "tencent_index"}.get(src, src)
        if src != "auto":
            return [src] if src in PROVIDER_IDS else []
        return self._provider_order()

    def _provider_enabled(self, provider: str) -> bool:
        if provider == "tencent_index":
            return _env_bool("CN_MARKET_TENCENT_INDEX_ENABLED", "1")
        if provider == "mootdx":
            return _env_bool("CN_MARKET_MOOTDX_ENABLED", "1") and importlib.util.find_spec("mootdx") is not None
        if provider == "local_cache":
            return True
        if provider == "akshare":
            return _env_bool("CN_MARKET_AKSHARE_ENABLED", "1") and importlib.util.find_spec("akshare") is not None
        if provider == "tushare":
            return (
                _env_bool("CN_MARKET_TUSHARE_ENABLED", "1")
                and bool(os.getenv("TUSHARE_TOKEN"))
                and importlib.util.find_spec("tushare") is not None
            )
        if provider == "baostock":
            return _env_bool("CN_MARKET_BAOSTOCK_ENABLED", "1") and importlib.util.find_spec("baostock") is not None
        return False

    def _klines_by_provider(
        self,
        provider: str,
        symbol: str,
        *,
        period: str,
        adjust: str,
        days: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        if not self._provider_enabled(provider):
            return []
        if provider == "mootdx":
            return self._mootdx_klines(symbol, period=period, days=days, limit=limit)
        if provider == "local_cache":
            return self._local_cache_klines(symbol, period=period, days=days, limit=limit)
        if provider == "akshare":
            return self._akshare_klines(symbol, period=period, adjust=adjust, days=days, limit=limit)
        if provider == "tushare":
            return self._tushare_klines(symbol, adjust=adjust, days=days, limit=limit)
        if provider == "baostock":
            return self._baostock_klines(symbol, period=period, adjust=adjust, days=days, limit=limit)
        return []

    def _quote_one(self, symbol: str, source: str) -> dict[str, Any] | None:
        sym = normalize_cn_symbol(symbol)
        for provider in self._providers_for(source):
            if not self._provider_enabled(provider):
                continue
            try:
                if provider == "tencent_index":
                    item = self._tencent_index_quote(sym)
                    if item:
                        return item
                    continue
                if provider == "mootdx":
                    item = self._mootdx_quote(sym)
                    if item:
                        return item
                if provider == "akshare":
                    item = self._akshare_quote(sym)
                    if item:
                        return item
                rows = self._klines_by_provider(provider, sym, period="1d", adjust="qfq", days=10, limit=10)
                item = _quote_from_kline(sym, rows, provider)
                if item:
                    return item
            except Exception:
                continue
        return None

    def _tencent_index_quote(self, symbol: str) -> dict[str, Any] | None:
        sym = normalize_cn_symbol(symbol)
        if not _is_cn_index(sym):
            return None
        code = _tencent_code(sym)
        try:
            resp = requests.get(
                "https://qt.gtimg.cn/q=" + code,
                headers={"User-Agent": "Mozilla/5.0 cn-market-data/1.0", "Referer": "https://gu.qq.com/"},
                timeout=4,
            )
            resp.raise_for_status()
            text = resp.content.decode("gbk", errors="ignore")
        except Exception:
            return None
        marker = f'v_{code}="'
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
        return {
            "symbol": sym,
            "name": parts[1] or sym,
            "last": last,
            "change_pct": _safe_float(parts[32], _change_pct(last, prev)),
            "open": _safe_float(parts[5], last),
            "high": _safe_float(parts[33], last),
            "low": _safe_float(parts[34], last),
            "prev_close": prev,
            "volume": _safe_float(parts[36] if len(parts) > 36 else None, 0.0) or 0.0,
            "amount": _safe_float(parts[37] if len(parts) > 37 else None, None),
            "as_of": str(_parse_tencent_timestamp(parts[30] if len(parts) > 30 else "") or datetime.now(timezone.utc).isoformat()),
            "source": "tencent_index",
            "realtime": True,
            "cache": False,
        }

    def _local_cache_klines(self, symbol: str, period: str, days: int, limit: int) -> list[dict[str, Any]]:
        stem = _cache_stem(symbol)
        p = str(period or "1d").strip().lower()
        pattern = os.path.join(KLINE_CACHE_DIR, f"{stem}__{p}__p*.json")
        files = sorted(glob.glob(pattern), key=lambda x: os.path.getmtime(x), reverse=True)
        if not files:
            return []
        min_needed = max(1, int(limit or days or 1))
        chosen = files[0]
        for path in files:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                items = raw.get("items") if isinstance(raw, dict) else None
                if isinstance(items, list) and len(items) >= min_needed:
                    chosen = path
                    break
            except Exception:
                continue
        try:
            with open(chosen, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            return []
        items = raw.get("items") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            return []
        out = [_normalize_kline_row(x, symbol=symbol, source="local_cache") for x in items if isinstance(x, dict)]
        rows = [x for x in out if x]
        if limit > 0:
            rows = rows[-limit:]
        elif days > 0:
            cutoff = datetime.now() - timedelta(days=days * 2)
            filtered = [x for x in rows if _parse_dt(x.get("date")) >= cutoff]
            rows = filtered or rows[-days:]
        return rows

    @staticmethod
    def _mootdx_client() -> Any:
        from mootdx.quotes import Quotes  # type: ignore

        market = str(os.getenv("CN_MARKET_MOOTDX_MARKET", "std") or "std").strip() or "std"
        server_raw = str(os.getenv("CN_MARKET_MOOTDX_SERVER", "") or "").strip()
        if ":" in server_raw:
            host, port = server_raw.rsplit(":", 1)
            try:
                return Quotes.factory(market=market, server=(host.strip(), int(port.strip())))
            except Exception:
                pass
        try:
            return Quotes.factory(market=market)
        except Exception as first_exc:
            last_exc: Exception = first_exc
            for host, port in MOOTDX_FALLBACK_SERVERS:
                try:
                    return Quotes.factory(market=market, server=(host, port), timeout=3)
                except Exception as exc:
                    last_exc = exc
                    continue
            raise last_exc

    def _mootdx_quote(self, symbol: str) -> dict[str, Any] | None:
        code = _symbol_code(symbol)
        market = _mootdx_market(symbol)
        client = self._mootdx_client()
        candidates = [
            lambda: client.quotes(symbol=[code], market=market),
            lambda: client.quotes(symbol=[code]),
            lambda: client.quotes(symbol=[(market, code)]),
        ]
        rows: list[dict[str, Any]] = []
        for call in candidates:
            try:
                rows = _records_from_frame(call())
            except Exception:
                rows = []
            if rows:
                break
        for row in rows:
            row_code = str(_pick(row, "code", "symbol", "stock_code") or "").strip().upper()
            if row_code and code not in row_code:
                continue
            last = _safe_float(_pick(row, "price", "last", "close", "now", "new_price"))
            if last is None:
                continue
            prev = _safe_float(_pick(row, "last_close", "prev_close", "pre_close", "yesterday_close"))
            volume = _safe_float(_pick(row, "volume", "vol"), 0.0) or 0.0
            if _is_cn_index(symbol) and last < 100:
                continue
            if last <= 0 and (prev is None or prev <= 0 or volume <= 0):
                continue
            return {
                "symbol": normalize_cn_symbol(symbol),
                "name": str(_pick(row, "name", "stock_name") or ""),
                "last": last,
                "change_pct": _safe_float(_pick(row, "change_pct", "涨跌幅", "pct_chg")),
                "open": _safe_float(_pick(row, "open", "open_price"), last),
                "high": _safe_float(_pick(row, "high", "high_price"), last),
                "low": _safe_float(_pick(row, "low", "low_price"), last),
                "prev_close": prev,
                "volume": volume,
                "amount": _safe_float(_pick(row, "amount", "turnover"), None),
                "as_of": str(_pick(row, "datetime", "date", "time") or datetime.now(timezone.utc).isoformat()),
                "source": "mootdx",
                "realtime": True,
                "cache": False,
            }
        return None

    def _mootdx_klines(self, symbol: str, period: str, days: int, limit: int) -> list[dict[str, Any]]:
        code = _symbol_code(symbol)
        market = _mootdx_market(symbol)
        category = _mootdx_period_category(period)
        count = max(2, min(5000, int(limit or 0) or int(days or 180) * 2))
        client = self._mootdx_client()
        candidates = [
            {"symbol": code, "market": market, "frequency": category, "start": 0, "offset": count},
            {"symbol": code, "frequency": category, "start": 0, "offset": count},
            {"symbol": code, "market": market, "category": category, "start": 0, "offset": count},
            {"symbol": code, "category": category, "start": 0, "offset": count},
        ]
        rows: list[dict[str, Any]] = []
        for kwargs in candidates:
            try:
                rows = _records_from_frame_with_index(client.bars(**kwargs))
            except TypeError:
                reduced = {k: v for k, v in kwargs.items() if k in {"symbol", "market", "frequency", "category"}}
                try:
                    rows = _records_from_frame_with_index(client.bars(**reduced))
                except Exception:
                    rows = []
            except Exception:
                rows = []
            if rows:
                break
        out = [_normalize_kline_row(x, symbol=symbol, source="mootdx") for x in rows]
        clean = [x for x in out if x]
        clean.sort(key=lambda x: _parse_dt(x.get("date")))
        if days > 0:
            cutoff = datetime.now() - timedelta(days=int(days) * 2 + 8)
            clean = [x for x in clean if _parse_dt(x.get("date")) >= cutoff] or clean
        return clean[-limit:] if limit > 0 else clean

    def _tencent_valuation(self, symbol: str) -> dict[str, Any] | None:
        code = _tencent_code(symbol)
        url = "https://qt.gtimg.cn/q=" + code
        headers = {"User-Agent": "Mozilla/5.0 cn-market-data/1.0", "Referer": "https://gu.qq.com/"}
        timeout = max(0.5, min(10.0, _safe_float(os.getenv("CN_MARKET_TENCENT_TIMEOUT_SECONDS"), 2.5) or 2.5))
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        resp.encoding = "gbk"
        text = str(resp.text or "").strip()
        if not text or '="' not in text:
            return None
        raw = text.split('="', 1)[1].rsplit('"', 1)[0]
        parts = raw.split("~")
        if len(parts) < 10:
            return None

        def at(idx: int) -> float | None:
            return _safe_float(parts[idx] if idx < len(parts) else None)

        item = {
            "symbol": normalize_cn_symbol(symbol),
            "name": parts[1] if len(parts) > 1 else "",
            "code": parts[2] if len(parts) > 2 else _symbol_code(symbol),
            "last": at(3),
            "prev_close": at(4),
            "open": at(5),
            "volume": at(6),
            "amount": at(37),
            "turnover_rate": at(38),
            "pe_ttm": at(39),
            "pb": at(46),
            "total_market_cap": at(45),
            "float_market_cap": at(44),
            "market_cap_unit": "CNY 100M",
            "as_of": datetime.now(timezone.utc).isoformat(),
            "source": "tencent",
            "field_count": len(parts),
        }
        useful = any(item.get(k) is not None for k in ("last", "pe_ttm", "pb", "total_market_cap", "float_market_cap"))
        return item if useful else None

    def _akshare_quote(self, symbol: str) -> dict[str, Any] | None:
        try:
            import akshare as ak  # type: ignore

            rows = _records_from_frame(ak.stock_zh_a_spot_em())
        except Exception:
            return None
        code = _symbol_code(symbol)
        for row in rows:
            if str(row.get("代码") or row.get("code") or "").zfill(6) != code:
                continue
            last = _safe_float(_pick(row, "最新价", "last", "close"))
            if last is None:
                return None
            return {
                "symbol": normalize_cn_symbol(symbol),
                "name": str(_pick(row, "名称", "name") or ""),
                "last": last,
                "change_pct": _safe_float(_pick(row, "涨跌幅", "change_pct")),
                "open": _safe_float(_pick(row, "今开", "open")),
                "high": _safe_float(_pick(row, "最高", "high")),
                "low": _safe_float(_pick(row, "最低", "low")),
                "prev_close": _safe_float(_pick(row, "昨收", "prev_close")),
                "volume": _safe_float(_pick(row, "成交量", "volume")),
                "amount": _safe_float(_pick(row, "成交额", "amount")),
                "as_of": datetime.now(timezone.utc).isoformat(),
                "source": "akshare",
                "realtime": False,
                "cache": False,
            }
        return None

    def _akshare_klines(self, symbol: str, period: str, adjust: str, days: int, limit: int) -> list[dict[str, Any]]:
        import akshare as ak  # type: ignore

        end = date.today()
        start = end - timedelta(days=max(30, int(days) * 2))
        df = ak.stock_zh_a_hist(
            symbol=_symbol_code(symbol),
            period=_period_for_akshare(period),
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust=_adjust_for_akshare(adjust),
        )
        rows = [_normalize_kline_row(x, symbol=symbol, source="akshare") for x in _records_from_frame(df)]
        out = [x for x in rows if x]
        return out[-limit:] if limit > 0 else out

    def _tushare_klines(self, symbol: str, adjust: str, days: int, limit: int) -> list[dict[str, Any]]:
        import tushare as ts  # type: ignore

        token = str(os.getenv("TUSHARE_TOKEN") or "").strip()
        if not token:
            return []
        end = date.today()
        start = end - timedelta(days=max(30, int(days) * 2))
        ts.set_token(token)
        adj = str(adjust or "qfq").strip().lower()
        if adj not in {"qfq", "hfq"}:
            adj = None  # type: ignore[assignment]
        df = ts.pro_bar(
            ts_code=_tushare_code(symbol),
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adj=adj,
        )
        rows = [_normalize_kline_row(x, symbol=symbol, source="tushare") for x in _records_from_frame(df)]
        out = [x for x in rows if x]
        out.sort(key=lambda x: _parse_dt(x.get("date")))
        return out[-limit:] if limit > 0 else out

    def _baostock_klines(self, symbol: str, period: str, adjust: str, days: int, limit: int) -> list[dict[str, Any]]:
        import baostock as bs  # type: ignore

        end = date.today()
        start = end - timedelta(days=max(30, int(days) * 2))
        lg = bs.login()
        try:
            if getattr(lg, "error_code", "0") != "0":
                return []
            rs = bs.query_history_k_data_plus(
                _baostock_code(symbol),
                "date,open,high,low,close,volume,amount,pctChg",
                start_date=start.isoformat(),
                end_date=end.isoformat(),
                frequency=_frequency_for_baostock(period),
                adjustflag=_adjust_for_baostock(adjust),
            )
            rows: list[dict[str, Any]] = []
            fields = list(getattr(rs, "fields", []) or [])
            while getattr(rs, "error_code", "0") == "0" and rs.next():
                vals = rs.get_row_data()
                rows.append(dict(zip(fields, vals)))
            out = [_normalize_kline_row(x, symbol=symbol, source="baostock") for x in rows]
            clean = [x for x in out if x]
            return clean[-limit:] if limit > 0 else clean
        finally:
            try:
                bs.logout()
            except Exception:
                pass

    def _cache_symbols(self) -> list[str]:
        out: set[str] = set()
        for path in glob.glob(os.path.join(KLINE_CACHE_DIR, "*__*__p*.json")):
            name = os.path.basename(path)
            parts = name.split("__", 1)
            if not parts:
                continue
            sym = parts[0].replace("_", ".")
            if sym.endswith((".SH", ".SZ", ".BJ")):
                out.add(normalize_cn_symbol(sym))
        return sorted(out)

    def _symbols(self, symbols: str | list[str]) -> list[str]:
        if isinstance(symbols, str):
            raw = [x.strip() for x in symbols.split(",")]
        else:
            raw = [str(x).strip() for x in symbols]
        out: list[str] = []
        for item in raw:
            sym = normalize_cn_symbol(item)
            if sym and sym not in out:
                out.append(sym)
        return out

    def _data_status(self, items: list[dict[str, Any]], provider: str | None = None) -> dict[str, Any]:
        source = provider or (str(items[0].get("source")) if items else "")
        dates: list[datetime] = []
        for x in items:
            if not isinstance(x, dict):
                continue
            dt = _parse_dt(x.get("as_of") or x.get("date"))
            if dt == datetime.min:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dates.append(dt)
        last_at = max(dates).isoformat() if dates else None
        return {
            "source": source,
            "realtime": bool(items and source in {"akshare", "mootdx"}),
            "cache": bool(source == "local_cache" or any(x.get("cache") for x in items)),
            "stale": bool(source == "local_cache"),
            "last_at": last_at,
        }


def get_cn_market_data_service() -> CnMarketDataService:
    return CnMarketDataService()
