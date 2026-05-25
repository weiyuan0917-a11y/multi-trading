from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any
from zoneinfo import ZoneInfo

from longbridge.openapi import Config, ContentContext

from api.services.account_registry import get_account_registry
from config.live_settings import live_settings


_CACHE_TTL_SECONDS = max(30, int(os.getenv("MARKET_NEWS_CACHE_TTL_SECONDS", "300")))
_HTTP_TIMEOUT_SECONDS = max(2.0, float(os.getenv("MARKET_NEWS_HTTP_TIMEOUT_SECONDS", "6")))
_LONGBRIDGE_TIMEOUT_SECONDS = max(2.0, float(os.getenv("MARKET_NEWS_LONGBRIDGE_TIMEOUT_SECONDS", "8")))
_LONG_BRIDGE_NEWS_TZ = ZoneInfo(os.getenv("MARKET_NEWS_LONGBRIDGE_TIMEZONE", "Asia/Shanghai"))
_STALE_CACHE_TTL_SECONDS = max(_CACHE_TTL_SECONDS, int(os.getenv("MARKET_NEWS_STALE_CACHE_TTL_SECONDS", "3600")))
_MAX_WORKERS = max(2, min(16, int(os.getenv("MARKET_NEWS_FETCH_WORKERS", "8"))))
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


MARKET_SYMBOLS = {
    "global": ["SPY.US", "QQQ.US", "DIA.US", ".VIX.US"],
    "china": ["000001.SH", "399001.SZ", "HSI.HK", "2828.HK"],
}

CN_RSS_FEEDS = [
    ("Reuters China", "https://feeds.reuters.com/reuters/marketsNews"),
    ("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
]

GLOBAL_RSS_FEEDS = [
    ("Reuters Markets", "https://feeds.reuters.com/reuters/businessNews"),
    ("CNBC Top News", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
]

SINA_ROLL_FEEDS = [
    ("Sina Finance", 2515),
    ("Sina US Stocks", 2516),
    ("Sina Global Markets", 2517),
    ("Sina HK Stocks", 2518),
]

POSITIVE_KEYWORDS = {
    "beat",
    "beats",
    "surge",
    "surges",
    "rally",
    "rises",
    "rise",
    "gain",
    "gains",
    "upgrade",
    "upgraded",
    "record",
    "profit",
    "profits",
    "growth",
    "strong",
    "outperform",
    "buyback",
    "dividend",
    "approval",
    "approved",
    "partnership",
    "contract",
    "raises",
    "raised",
    "利好",
    "上涨",
    "大涨",
    "增长",
    "盈利",
    "超预期",
    "上调",
    "回购",
    "分红",
    "批准",
    "突破",
    "合作",
}

NEGATIVE_KEYWORDS = {
    "miss",
    "misses",
    "drop",
    "drops",
    "fall",
    "falls",
    "slump",
    "slumps",
    "plunge",
    "plunges",
    "downgrade",
    "downgraded",
    "lawsuit",
    "probe",
    "investigation",
    "recall",
    "loss",
    "losses",
    "weak",
    "cuts",
    "cut",
    "warning",
    "warns",
    "layoff",
    "layoffs",
    "risk",
    "tariff",
    "sanction",
    "利空",
    "下跌",
    "大跌",
    "亏损",
    "低于预期",
    "下调",
    "诉讼",
    "调查",
    "召回",
    "裁员",
    "风险",
    "制裁",
    "关税",
}


@dataclass(frozen=True)
class NewsItem:
    id: str
    title: str
    summary: str = ""
    url: str = ""
    source: str = ""
    published_at: str | None = None
    symbol: str | None = None
    region: str = "global"
    category: str = "market"
    origin: str = "rss"
    likes_count: int | None = None
    comments_count: int | None = None
    sentiment: str = "neutral"
    sentiment_score: int = 0
    sentiment_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "url": self.url,
            "source": self.source,
            "published_at": self.published_at,
            "symbol": self.symbol,
            "region": self.region,
            "category": self.category,
            "origin": self.origin,
            "likes_count": self.likes_count,
            "comments_count": self.comments_count,
            "sentiment": self.sentiment,
            "sentiment_score": self.sentiment_score,
            "sentiment_reasons": list(self.sentiment_reasons),
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_symbol(value: Any) -> str:
    raw = str(value or "").strip().upper()
    if not raw:
        return ""
    if "." not in raw and raw.isalpha():
        raw = f"{raw}.US"
    return raw


def _symbol_region(symbol: str | None) -> str:
    sym = str(symbol or "").upper()
    if sym.endswith(".SH") or sym.endswith(".SZ") or sym.endswith(".BJ") or sym.endswith(".HK"):
        return "china"
    return "global"


def _clean_text(value: Any, limit: int = 600) -> str:
    text = unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit].strip()


def _parse_datetime(value: Any, *, naive_tz: timezone | ZoneInfo = timezone.utc) -> str | None:
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=naive_tz)
        return dt.isoformat()
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=naive_tz)
        return dt.isoformat()
    except Exception:
        pass
    try:
        text = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=naive_tz)
        return dt.isoformat()
    except Exception:
        return raw


def _stamp_to_iso(value: Any, *, naive_tz: timezone | ZoneInfo = timezone.utc) -> str | None:
    if value is None:
        return None
    if not isinstance(value, datetime) and not str(value):
        return None
    return _parse_datetime(value, naive_tz=naive_tz)


def _fingerprint(*parts: Any) -> str:
    text = "|".join(_clean_text(x, limit=300) for x in parts if x is not None)
    return str(abs(hash(text)))


def classify_news_sentiment(title: str, summary: str = "") -> dict[str, Any]:
    text = f"{title} {summary}".lower()
    positive = [kw for kw in POSITIVE_KEYWORDS if kw.lower() in text]
    negative = [kw for kw in NEGATIVE_KEYWORDS if kw.lower() in text]
    score = len(positive) - len(negative)
    if score >= 1:
        sentiment = "bullish"
    elif score <= -1:
        sentiment = "bearish"
    else:
        sentiment = "neutral"
    reasons: list[str] = []
    if positive:
        reasons.append("positive:" + ",".join(sorted(positive)[:4]))
    if negative:
        reasons.append("negative:" + ",".join(sorted(negative)[:4]))
    if not reasons:
        reasons.append("keyword_neutral")
    return {
        "sentiment": sentiment,
        "sentiment_score": max(-5, min(5, score)),
        "sentiment_reasons": tuple(reasons),
    }


def _with_sentiment(item: NewsItem) -> NewsItem:
    row = classify_news_sentiment(item.title, item.summary)
    return NewsItem(
        **{
            **item.to_dict(),
            "sentiment": row["sentiment"],
            "sentiment_score": row["sentiment_score"],
            "sentiment_reasons": tuple(row["sentiment_reasons"]),
        }
    )


def _get_account_credentials(account_id: str | None, owner_id: str | None) -> tuple[str, str, str] | None:
    try:
        rec = get_account_registry().get_account_record(account_id, owner_id=owner_id)
        if str(rec.broker_provider or "").strip().lower() != "longbridge":
            return None
        return rec.credentials.app_key, rec.credentials.app_secret, rec.credentials.access_token
    except Exception:
        pass
    try:
        return live_settings.get_longbridge_credentials()
    except Exception:
        return None


def _fetch_longbridge_sdk_news(symbol: str, *, account_id: str | None, owner_id: str | None, limit: int) -> list[NewsItem]:
    creds = _get_account_credentials(account_id, owner_id)
    if not creds or not all(creds):
        return []
    app_key, app_secret, access_token = creds
    cfg = Config.from_apikey(app_key, app_secret, access_token)
    ctx = ContentContext(cfg)
    rows = ctx.news(symbol)
    out: list[NewsItem] = []
    for row in list(rows or [])[: max(1, limit)]:
        title = _clean_text(getattr(row, "title", ""))
        if not title:
            continue
        out.append(
            _with_sentiment(
                NewsItem(
                    id=str(getattr(row, "id", "") or f"lb_{symbol}_{_fingerprint(title)}"),
                    title=title,
                    summary=_clean_text(getattr(row, "description", "")),
                    url=str(getattr(row, "url", "") or ""),
                    source="LongBridge",
                    published_at=_stamp_to_iso(getattr(row, "published_at", None), naive_tz=_LONG_BRIDGE_NEWS_TZ),
                    symbol=symbol,
                    region=_symbol_region(symbol),
                    category="holding",
                    origin="longbridge_sdk",
                    likes_count=_safe_int(getattr(row, "likes_count", None)),
                    comments_count=_safe_int(getattr(row, "comments_count", None)),
                )
            )
        )
    return out


def _fetch_longbridge_cli_news(symbol: str, *, limit: int) -> list[NewsItem]:
    try:
        proc = subprocess.run(
            ["longbridge", "news", symbol, "--format", "json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_LONGBRIDGE_TIMEOUT_SECONDS,
            check=False,
        )
    except Exception:
        return []
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return []
    try:
        parsed = json.loads(proc.stdout)
    except Exception:
        return []
    rows = parsed if isinstance(parsed, list) else parsed.get("items") if isinstance(parsed, dict) else []
    if not isinstance(rows, list):
        return []
    out: list[NewsItem] = []
    for row in rows[: max(1, limit)]:
        if not isinstance(row, dict):
            continue
        title = _clean_text(row.get("title") or row.get("headline"))
        if not title:
            continue
        out.append(
            _with_sentiment(
                NewsItem(
                    id=str(row.get("id") or f"lbcli_{symbol}_{_fingerprint(title, row.get('url'))}"),
                    title=title,
                    summary=_clean_text(row.get("description") or row.get("summary")),
                    url=str(row.get("url") or ""),
                    source="LongBridge",
                    published_at=_parse_datetime(
                        row.get("published_at") or row.get("publishedAt") or row.get("time"),
                        naive_tz=_LONG_BRIDGE_NEWS_TZ,
                    ),
                    symbol=symbol,
                    region=_symbol_region(symbol),
                    category="holding",
                    origin="longbridge_cli",
                    likes_count=_safe_int(row.get("likes_count") or row.get("likesCount")),
                    comments_count=_safe_int(row.get("comments_count") or row.get("commentsCount")),
                )
            )
        )
    return out


def _fetch_rss(url: str, source: str, *, region: str, category: str, limit: int) -> list[NewsItem]:
    req = urllib.request.Request(url, headers={"User-Agent": "MultiTrading/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
            raw = resp.read(1_200_000)
    except (urllib.error.URLError, TimeoutError, OSError):
        return []
    try:
        root = ET.fromstring(raw)
    except Exception:
        return []
    items = root.findall(".//item")
    if not items:
        items = root.findall(".//{http://www.w3.org/2005/Atom}entry")
    out: list[NewsItem] = []
    for node in items[: max(1, limit)]:
        title = _clean_text(_xml_text(node, "title"))
        if not title:
            continue
        summary = _clean_text(_xml_text(node, "description") or _xml_text(node, "summary"))
        link = _xml_text(node, "link")
        if not link:
            atom_link = node.find("{http://www.w3.org/2005/Atom}link")
            link = str(atom_link.attrib.get("href", "")) if atom_link is not None else ""
        published = _parse_datetime(_xml_text(node, "pubDate") or _xml_text(node, "published") or _xml_text(node, "updated"))
        out.append(
            _with_sentiment(
                NewsItem(
                    id=f"rss_{_fingerprint(source, title, link)}",
                    title=title,
                    summary=summary,
                    url=link,
                    source=source,
                    published_at=published,
                    region=region,
                    category=category,
                    origin="rss",
                )
            )
        )
    return out


def _yahoo_symbol(symbol: str) -> str:
    sym = _normalize_symbol(symbol)
    if not sym:
        return ""
    if sym.startswith(".") and sym.endswith(".US"):
        return "^" + sym[1:-3]
    if sym.endswith(".US"):
        return sym[:-3]
    if sym.endswith(".HK"):
        return sym[:-3].zfill(4) + ".HK"
    if sym.endswith(".SH"):
        return sym[:-3] + ".SS"
    if sym.endswith(".SZ"):
        return sym[:-3] + ".SZ"
    return sym.replace(".", "-")


def _fetch_yahoo_symbol_news(symbol: str, *, limit: int) -> list[NewsItem]:
    yahoo = _yahoo_symbol(symbol)
    if not yahoo:
        return []
    params = urllib.parse.urlencode({"s": yahoo, "region": "US", "lang": "en-US"})
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?{params}"
    items = _fetch_rss(url, "Yahoo Finance", region=_symbol_region(symbol), category="holding", limit=limit)
    out: list[NewsItem] = []
    for item in items:
        out.append(
            NewsItem(
                **{
                    **item.to_dict(),
                    "id": f"yahoo_{symbol}_{item.id}",
                    "symbol": _normalize_symbol(symbol),
                    "origin": "yahoo_rss",
                    "sentiment_reasons": tuple(item.sentiment_reasons),
                }
            )
        )
    return out[:limit]


def _fetch_yahoo_market_news(region: str, *, limit: int) -> list[NewsItem]:
    out: list[NewsItem] = []
    for symbol in MARKET_SYMBOLS.get(region, [])[:4]:
        for item in _fetch_yahoo_symbol_news(symbol, limit=max(1, limit // 4)):
            out.append(
                NewsItem(
                    **{
                        **item.to_dict(),
                        "category": "market",
                        "region": region,
                        "sentiment_reasons": tuple(item.sentiment_reasons),
                    }
                )
            )
    return out[:limit]


def _fetch_sina_roll_feed(source: str, lid: int, *, region: str, category: str, limit: int) -> list[NewsItem]:
    params = urllib.parse.urlencode({"pageid": 153, "lid": int(lid), "num": max(1, min(50, limit)), "page": 1})
    url = f"https://feed.mix.sina.com.cn/api/roll/get?{params}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 MultiTrading/1.0",
            "Referer": "https://finance.sina.com.cn/",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
            raw = resp.read(1_200_000).decode("utf-8", errors="ignore")
        data = json.loads(raw)
    except Exception:
        return []
    result = data.get("result") if isinstance(data, dict) else {}
    rows = result.get("data") if isinstance(result, dict) else []
    if not isinstance(rows, list):
        return []
    out: list[NewsItem] = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        title = _clean_text(row.get("title") or row.get("stitle"))
        if not title:
            continue
        ctime = _safe_int(row.get("ctime"))
        published = datetime.fromtimestamp(ctime, tz=_LONG_BRIDGE_NEWS_TZ).isoformat() if ctime else None
        media = _clean_text(row.get("media_name"), limit=80) or source
        out.append(
            _with_sentiment(
                NewsItem(
                    id=str(row.get("docid") or f"sina_{lid}_{_fingerprint(title, row.get('url'))}"),
                    title=title,
                    summary=_clean_text(row.get("summary") or row.get("intro") or row.get("wapsummary")),
                    url=str(row.get("url") or row.get("wapurl") or ""),
                    source=media,
                    published_at=published,
                    region=region,
                    category=category,
                    origin="sina_roll",
                )
            )
        )
    return out


def _fetch_sina_market_news(region: str, *, limit: int) -> list[NewsItem]:
    out: list[NewsItem] = []
    for source, lid in SINA_ROLL_FEEDS:
        out.extend(_fetch_sina_roll_feed(source, lid, region=region, category="market", limit=max(3, limit // 2)))
    return out[:limit]


def _xml_text(node: ET.Element, name: str) -> str:
    found = node.find(name)
    if found is None:
        found = node.find(f"{{http://www.w3.org/2005/Atom}}{name}")
    return str(found.text or "").strip() if found is not None else ""


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _dedupe(items: list[NewsItem]) -> list[NewsItem]:
    seen: set[str] = set()
    out: list[NewsItem] = []
    for item in items:
        key = (item.url or item.title).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _sort_items(items: list[NewsItem]) -> list[NewsItem]:
    def key(item: NewsItem) -> tuple[int, str]:
        score = 0
        if item.published_at:
            try:
                score = int(datetime.fromisoformat(item.published_at.replace("Z", "+00:00")).timestamp())
            except Exception:
                score = 0
        return score, item.title

    return sorted(items, key=key, reverse=True)


def _position_symbols(account_id: str | None, owner_id: str | None, explicit_symbols: list[str]) -> tuple[list[str], dict[str, Any]]:
    symbols = [_normalize_symbol(x) for x in explicit_symbols if _normalize_symbol(x)]
    meta: dict[str, Any] = {"source": "request", "available": True, "error": None}
    if symbols:
        return symbols[:20], meta
    try:
        from api import runtime_bridge as rt

        positions = rt.trade_positions(account_id=account_id, owner_id=owner_id)
        rows = positions.get("positions") if isinstance(positions, dict) else []
        symbols = []
        for row in rows if isinstance(rows, list) else []:
            sym = _normalize_symbol(row.get("symbol") if isinstance(row, dict) else "")
            qty = float(row.get("quantity") or 0) if isinstance(row, dict) else 0.0
            if sym and abs(qty) > 0:
                symbols.append(sym)
        meta = {"source": "broker_positions", "available": True, "error": None, "count": len(symbols)}
    except Exception as exc:
        meta = {"source": "broker_positions", "available": False, "error": str(exc), "count": 0}
        symbols = []
    return list(dict.fromkeys(symbols))[:20], meta


def _fetch_symbol_news(symbol: str, *, account_id: str | None, owner_id: str | None, limit: int) -> list[NewsItem]:
    calls = [
        lambda: _fetch_longbridge_sdk_news(symbol, account_id=account_id, owner_id=owner_id, limit=limit),
        lambda: _fetch_longbridge_cli_news(symbol, limit=limit),
        lambda: _fetch_yahoo_symbol_news(symbol, limit=limit),
    ]
    items: list[NewsItem] = []
    with ThreadPoolExecutor(max_workers=min(3, _MAX_WORKERS), thread_name_prefix="market-news-symbol") as pool:
        futures = [pool.submit(fn) for fn in calls]
        for future in as_completed(futures, timeout=_LONGBRIDGE_TIMEOUT_SECONDS + _HTTP_TIMEOUT_SECONDS + 2):
            try:
                items.extend(future.result() or [])
            except Exception:
                continue
    return _sort_items(_dedupe(items))[:limit]


def _market_symbol_news(region: str, *, account_id: str | None, owner_id: str | None, per_symbol: int) -> list[NewsItem]:
    out: list[NewsItem] = []
    symbols = MARKET_SYMBOLS.get(region, [])
    with ThreadPoolExecutor(max_workers=min(len(symbols) or 1, _MAX_WORKERS), thread_name_prefix="market-news-market") as pool:
        futures = {
            pool.submit(_fetch_symbol_news, symbol, account_id=account_id, owner_id=owner_id, limit=per_symbol): symbol
            for symbol in symbols
        }
        for future in as_completed(futures):
            try:
                rows = future.result() or []
            except Exception:
                rows = []
            for item in rows:
                out.append(
                    NewsItem(
                        **{
                            **item.to_dict(),
                            "category": "market",
                            "region": region,
                            "sentiment_reasons": tuple(item.sentiment_reasons),
                        }
                    )
                )
    return out


def _fetch_many(tasks: list[tuple[str, Any]], errors: list[dict[str, Any]]) -> list[NewsItem]:
    items: list[NewsItem] = []
    if not tasks:
        return items
    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(tasks)), thread_name_prefix="market-news") as pool:
        futures = {pool.submit(fn): name for name, fn in tasks}
        for future in as_completed(futures):
            name = futures[future]
            try:
                rows = future.result() or []
                items.extend(rows)
            except Exception as exc:
                errors.append({"source": name, "error": str(exc)})
    return items


def get_market_news_feed(
    *,
    account_id: str | None = None,
    owner_id: str | None = None,
    symbols: list[str] | None = None,
    region: str = "all",
    limit: int = 80,
    refresh: bool = False,
) -> dict[str, Any]:
    region_key = str(region or "all").strip().lower()
    explicit = [_normalize_symbol(x) for x in (symbols or []) if _normalize_symbol(x)]
    cache_key = json.dumps(
        {
            "account_id": account_id or "",
            "owner_id": owner_id or "",
            "symbols": explicit,
            "region": region_key,
            "limit": max(1, min(120, int(limit or 80))),
        },
        sort_keys=True,
    )
    if not refresh:
        cached = _CACHE.get(cache_key)
        if cached and time.time() - cached[0] <= _CACHE_TTL_SECONDS:
            payload = dict(cached[1])
            payload["cache"] = True
            payload["stale"] = False
            return payload

    max_items = max(10, min(120, int(limit or 80)))
    position_symbols, positions_meta = _position_symbols(account_id, owner_id, explicit)
    items: list[NewsItem] = []
    errors: list[dict[str, Any]] = []
    stale_cached = _CACHE.get(cache_key)

    regions = ["china", "global"] if region_key == "all" else [region_key]
    tasks: list[tuple[str, Any]] = []
    for reg in regions:
        if reg in {"china", "cn", "hk"}:
            tasks.append((
                "LongBridge/Yahoo China market symbols",
                lambda reg="china": _market_symbol_news(reg, account_id=account_id, owner_id=owner_id, per_symbol=4),
            ))
            tasks.append(("Yahoo China market", lambda: _fetch_yahoo_market_news("china", limit=12)))
            tasks.append(("Sina Finance", lambda: _fetch_sina_market_news("china", limit=24)))
            for source, url in CN_RSS_FEEDS:
                tasks.append((source, lambda source=source, url=url: _fetch_rss(url, source, region="china", category="market", limit=10)))
        if reg in {"global", "us", "international", "intl"}:
            tasks.append((
                "LongBridge/Yahoo Global market symbols",
                lambda reg="global": _market_symbol_news(reg, account_id=account_id, owner_id=owner_id, per_symbol=4),
            ))
            tasks.append(("Yahoo Global market", lambda: _fetch_yahoo_market_news("global", limit=16)))
            for source, url in GLOBAL_RSS_FEEDS:
                tasks.append((source, lambda source=source, url=url: _fetch_rss(url, source, region="global", category="market", limit=10)))

    for sym in position_symbols:
        tasks.append((
            f"holding:{sym}",
            lambda sym=sym: _fetch_symbol_news(sym, account_id=account_id, owner_id=owner_id, limit=10),
        ))

    items.extend(_fetch_many(tasks, errors))

    if not items and stale_cached and time.time() - stale_cached[0] <= _STALE_CACHE_TTL_SECONDS:
        payload = dict(stale_cached[1])
        payload["cache"] = True
        payload["stale"] = True
        payload["stale_reason"] = "refresh_returned_no_news"
        payload["errors"] = [*(payload.get("errors") or []), *errors]
        return payload

    sorted_items = _sort_items(_dedupe(items))[:max_items]
    counts = {"bullish": 0, "bearish": 0, "neutral": 0}
    for item in sorted_items:
        counts[item.sentiment] = counts.get(item.sentiment, 0) + 1

    payload = {
        "ok": True,
        "generated_at": _now_iso(),
        "cache": False,
        "stale": False,
        "cache_ttl_seconds": _CACHE_TTL_SECONDS,
        "stale_cache_ttl_seconds": _STALE_CACHE_TTL_SECONDS,
        "region": region_key,
        "positions": {"symbols": position_symbols, **positions_meta},
        "counts": {
            "total": len(sorted_items),
            **counts,
            "holding": sum(1 for x in sorted_items if x.category == "holding"),
            "market": sum(1 for x in sorted_items if x.category == "market"),
        },
        "items": [x.to_dict() for x in sorted_items],
        "sources": sorted({x.source for x in sorted_items if x.source}),
        "errors": errors,
        "sentiment_method": "keyword_heuristic_v1",
    }
    _CACHE[cache_key] = (time.time(), payload)
    return payload
