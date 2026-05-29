from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timedelta
from io import StringIO
from typing import Any, Callable


SCHEMA = "a_share_research_data.v2"
_CACHE_TTL_SECONDS = 15 * 60
_SNAPSHOT_CACHE_TTL_SECONDS = 24 * 60 * 60
_ROOT_DIR = os.path.abspath(
    os.getenv("MULTITRADING_ROOT")
    or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
_SNAPSHOT_CACHE_DIR = os.path.join(_ROOT_DIR, "data", "research_cache", "a_share")

_EVENT_TAG_KEYWORDS = [
    ("业绩", ("年报", "季报", "一季报", "中报", "三季报", "业绩", "净利润", "营业收入", "扭亏", "亏损")),
    ("融资", ("定增", "向特定对象发行", "配股", "可转债", "募集说明书", "再融资", "募资")),
    ("分红", ("分红", "派息", "利润分配", "权益分派", "送股", "转增")),
    ("回购", ("回购", "股份回购")),
    ("增减持", ("减持", "增持", "持股变动", "股东权益变动")),
    ("并购重组", ("重组", "收购", "资产购买", "资产出售", "重大资产", "并购")),
    ("合同订单", ("中标", "合同", "订单", "框架协议", "采购")),
    ("监管风险", ("问询函", "监管", "处罚", "立案", "诉讼", "仲裁", "风险提示", "退市", "ST")),
    ("股权激励", ("股权激励", "员工持股", "限制性股票", "股票期权")),
]

_INDICATOR_FIELDS = [
    ("报告期", ("REPORT_DATE_NAME", "REPORT_DATE"), "text"),
    ("EPS", ("EPSJB", "EPSXS"), "per_share"),
    ("每股净资产", ("BPS",), "per_share"),
    ("每股经营现金流", ("MGJYXJJE", "PER_NETCASH"), "per_share"),
    ("营业总收入", ("TOTALOPERATEREVE",), "amount"),
    ("营收同比", ("TOTALOPERATEREVETZ", "YYZSRGDHBZC"), "pct"),
    ("归母净利润", ("PARENTNETPROFIT",), "amount"),
    ("归母净利同比", ("PARENTNETPROFITTZ", "NETPROFITRPHBZC"), "pct"),
    ("扣非净利润", ("KCFJCXSYJLR", "DEDU_PARENT_PROFIT"), "amount"),
    ("扣非净利同比", ("KCFJCXSYJLRTZ", "DPNP_YOY_RATIO"), "pct"),
    ("ROE", ("ROEJQ", "ROE_DILUTED"), "pct"),
    ("毛利率", ("XSMLL", "GROSS_PROFIT_RATIO"), "pct"),
    ("净利率", ("XSJLL", "NET_PROFIT_RATIO"), "pct"),
    ("资产负债率", ("ZCFZL",), "pct"),
]

_ABSTRACT_METRICS = [
    ("营业总收入", ("营业总收入", "营业收入"), "amount"),
    ("归母净利润", ("归母净利润", "归属净利润"), "amount"),
    ("扣非净利润", ("扣非净利润",), "amount"),
    ("经营现金流净额", ("经营现金流量净额", "经营现金流净额"), "amount"),
    ("基本每股收益", ("基本每股收益", "每股收益"), "per_share"),
    ("每股净资产", ("每股净资产",), "per_share"),
    ("ROE", ("净资产收益率", "加权净资产收益率"), "pct"),
    ("毛利率", ("销售毛利率", "毛利率"), "pct"),
    ("资产负债率", ("资产负债率",), "pct"),
]


def _safe_float(v: Any, default: float | None = None) -> float | None:
    try:
        if v is None:
            return default
        if isinstance(v, str):
            v = v.replace(",", "").replace("%", "").strip()
            if not v or v in {"-", "--", "nan", "None"}:
                return default
        out = float(v)
        return out if math.isfinite(out) else default
    except Exception:
        return default


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


def _cell(v: Any, max_len: int = 120) -> str:
    if v is None:
        return ""
    try:
        if isinstance(v, float) and not math.isfinite(v):
            return ""
    except Exception:
        pass
    s = str(v).replace("\r", " ").replace("\n", " ").strip()
    if s.lower() in {"nan", "none", "nat", "null", "-", "--"}:
        return ""
    s = s.replace("|", "\\|")
    if len(s) > max_len:
        return s[: max_len - 3].rstrip() + "..."
    return s


def _safe_slug(v: Any) -> str:
    raw = str(v or "").strip().upper()
    out = "".join(ch if ch.isalnum() or ch in {".", "_", "-"} else "_" for ch in raw)
    return out.strip("._-") or "unknown"


def _compact_text(v: Any, max_len: int = 240) -> str:
    return _cell(v, max_len=max_len).replace("\\|", "|")


def _format_number(v: Any, kind: str = "raw") -> str:
    if kind == "text":
        return _cell(v, 80) or "-"
    x = _safe_float(v)
    if x is None:
        return _cell(v, 80) or "-"
    if kind == "ratio_pct":
        return f"{x * 100.0:.2f}%"
    if kind == "pct":
        return f"{x:.2f}%"
    if kind == "per_share":
        return f"{x:.4f}"
    if kind == "amount":
        ax = abs(x)
        if ax >= 1_0000_0000:
            return f"{x / 1_0000_0000:.2f}亿"
        if ax >= 1_0000:
            return f"{x / 1_0000:.2f}万"
        return f"{x:.2f}"
    return f"{x:.4f}" if abs(x) < 100 else f"{x:.2f}"


def _row_get_any(row: dict[str, Any], keys: tuple[str, ...] | list[str]) -> Any:
    for key in keys:
        if key in row and _cell(row.get(key)):
            return row.get(key)
    low = {str(k).lower(): k for k in row.keys()}
    for key in keys:
        src = low.get(str(key).lower())
        if src is not None and _cell(row.get(src)):
            return row.get(src)
    return None


def _first(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        val = _cell(row.get(key))
        if val:
            return val
    low = {str(k).lower(): k for k in row.keys()}
    for key in keys:
        src = low.get(str(key).lower())
        if src is not None:
            val = _cell(row.get(src))
            if val:
                return val
    return ""


def _first_by_contains(row: dict[str, Any], *hints: str) -> str:
    for key, val in row.items():
        sk = str(key)
        if any(hint in sk for hint in hints):
            out = _cell(val)
            if out:
                return out
    return ""


def _symbol_code(symbol: str) -> str:
    raw = str(symbol or "").strip().upper().replace("_", ".")
    if "." in raw:
        raw = raw.split(".", 1)[0]
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits.zfill(6) if digits else raw


def _market_suffix(symbol: str) -> str:
    raw = str(symbol or "").strip().upper().replace("_", ".")
    if raw.endswith(".SH") or raw.startswith("SH"):
        return "SH"
    if raw.endswith(".SZ") or raw.startswith("SZ"):
        return "SZ"
    if raw.endswith(".BJ") or raw.startswith("BJ"):
        return "BJ"
    code = _symbol_code(raw)
    if code.startswith(("6", "9")):
        return "SH"
    if code.startswith(("4", "8")):
        return "BJ"
    return "SZ"


def _canonical_symbol(symbol: str) -> str:
    code = _symbol_code(symbol)
    suffix = _market_suffix(symbol)
    return f"{code}.{suffix}" if code else str(symbol or "").strip().upper()


def _eastmoney_symbol(symbol: str) -> str:
    code = _symbol_code(symbol)
    suffix = _market_suffix(symbol)
    prefix = "SH" if suffix == "SH" else "SZ" if suffix == "SZ" else "BJ"
    return f"{prefix}{code}"


def _ak_secucode(symbol: str) -> str:
    code = _symbol_code(symbol)
    suffix = _market_suffix(symbol)
    return f"{code}.{suffix}" if code else str(symbol or "").strip().upper()


def _parse_date_any(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v or "").strip()
    if not s:
        return None
    for pat in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"):
        try:
            return datetime.strptime(s[:10] if pat != "%Y%m%d" else s[:8], pat).date()
        except Exception:
            pass
    m = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except Exception:
            return None
    return None


def _compact_date(v: str | None, default: date) -> str:
    d = _parse_date_any(v) or default
    return d.strftime("%Y%m%d")


def _row_date(row: dict[str, Any]) -> date | None:
    for key in (
        "发布时间",
        "发布日期",
        "公告日期",
        "报告期",
        "报告日期",
        "日期",
        "时间",
        "publish_time",
        "date",
        "datetime",
        "REPORT_DATE",
    ):
        d = _parse_date_any(row.get(key))
        if d:
            return d
    for key, val in row.items():
        if any(hint in str(key) for hint in ("日期", "时间", "DATE")):
            d = _parse_date_any(val)
            if d:
                return d
    return None


def _choose_columns(rows: list[dict[str, Any]], preferred: list[str], max_cols: int = 8) -> list[str]:
    out: list[str] = []
    for key in preferred:
        if key not in out and any(_cell(r.get(key)) for r in rows):
            out.append(key)
    if len(out) >= max_cols:
        return out[:max_cols]
    for row in rows[:8]:
        for key in row.keys():
            k = str(key)
            if k in out:
                continue
            if any(_cell(r.get(k)) for r in rows):
                out.append(k)
            if len(out) >= max_cols:
                return out
    return out[:max_cols]


def _markdown_table(rows: list[dict[str, Any]], preferred: list[str] | None = None, limit: int = 8) -> str:
    usable = [r for r in rows if isinstance(r, dict)]
    if not usable:
        return ""
    cols = _choose_columns(usable, list(preferred or []), max_cols=8)
    if not cols:
        return ""
    lines = [
        "| " + " | ".join(_cell(c, 40) for c in cols) + " |",
        "| " + " | ".join("---" for _ in cols) + " |",
    ]
    for row in usable[: max(1, int(limit))]:
        lines.append("| " + " | ".join(_cell(row.get(c), 90) or "-" for c in cols) + " |")
    return "\n".join(lines)


def _bullet_pairs(rows: list[dict[str, Any]], limit: int = 16) -> str:
    bullets: list[str] = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        if len(row) >= 2:
            vals = list(row.values())
            k = _cell(vals[0], 60)
            v = _cell(vals[1], 180)
            if k and v:
                bullets.append(f"- {k}: {v}")
                continue
        parts = [f"{_cell(k, 40)}={_cell(v, 80)}" for k, v in row.items() if _cell(v)]
        if parts:
            bullets.append("- " + "; ".join(parts[:4]))
    return "\n".join(bullets)


def _filter_by_date(rows: list[dict[str, Any]], start_date: str, end_date: str) -> list[dict[str, Any]]:
    sd = _parse_date_any(start_date)
    ed = _parse_date_any(end_date)
    if not sd and not ed:
        return rows
    out: list[dict[str, Any]] = []
    for row in rows:
        d = _row_date(row)
        if not d:
            out.append(row)
            continue
        if sd and d < sd:
            continue
        if ed and d > ed:
            continue
        out.append(row)
    return out


def _event_tags(text: str) -> list[str]:
    s = str(text or "")
    tags: list[str] = []
    for tag, words in _EVENT_TAG_KEYWORDS:
        if any(word in s for word in words):
            tags.append(tag)
    return tags


def _event_score(item: dict[str, Any]) -> int:
    score = len(item.get("tags") or []) * 10
    title = str(item.get("title") or "")
    if item.get("kind") == "notice":
        score += 6
    if item.get("kind") == "research_report":
        score += 4
    if any(word in title for word in ("问询函", "监管", "处罚", "风险提示", "退市", "ST")):
        score += 8
    if any(word in title for word in ("年报", "一季报", "中报", "三季报", "定增", "向特定对象发行")):
        score += 5
    return score


def _date_text(v: Any) -> str:
    d = _parse_date_any(v)
    if d:
        return d.isoformat()
    return _cell(v, 40)


def _normalize_news_row(row: dict[str, Any], source: str, symbol: str) -> dict[str, Any] | None:
    title = _first(row, "新闻标题", "标题", "title", "TITLE") or _first_by_contains(row, "标题")
    title = _compact_text(title, 180)
    if not title:
        return None
    d = _row_date(row)
    summary = _first(row, "新闻内容", "摘要", "内容", "summary", "SUMMARY")
    url = _first(row, "新闻链接", "链接", "url", "URL")
    media = _first(row, "文章来源", "来源", "媒体", "source") or source
    tags = _event_tags(title + " " + _compact_text(summary, 500))
    return {
        "kind": "news",
        "symbol": _canonical_symbol(symbol),
        "date": d.isoformat() if d else _date_text(_first(row, "发布时间", "日期", "时间")),
        "title": title,
        "summary": _compact_text(summary, 500),
        "source": source,
        "media": media,
        "url": url,
        "tags": tags,
    }


def _normalize_notice_row(row: dict[str, Any], source: str, symbol: str) -> dict[str, Any] | None:
    title = _first(row, "公告标题", "标题", "notice_title", "art_code") or _first_by_contains(row, "公告", "标题")
    title = _compact_text(title, 200)
    if not title:
        return None
    d = _row_date(row)
    url = _first(row, "公告链接", "链接", "url", "URL")
    typ = _first(row, "公告类型", "类型", "category", "公告类别")
    tags = _event_tags(title + " " + typ)
    return {
        "kind": "notice",
        "symbol": _canonical_symbol(symbol),
        "date": d.isoformat() if d else _date_text(_first(row, "公告时间", "公告日期", "发布日期", "日期")),
        "title": title,
        "summary": typ,
        "source": source,
        "media": source,
        "url": url,
        "tags": tags,
    }


def _normalize_research_report_row(row: dict[str, Any], symbol: str) -> dict[str, Any] | None:
    title = _first(row, "报告名称", "标题", "title") or _first_by_contains(row, "报告")
    title = _compact_text(title, 200)
    if not title:
        return None
    d = _row_date(row)
    org = _first(row, "机构", "研究机构", "source")
    rating = _first(row, "东财评级", "评级", "rating")
    url = _first(row, "报告PDF链接", "报告链接", "链接", "url")
    summary_parts = [x for x in [rating and f"评级: {rating}", org and f"机构: {org}"] if x]
    return {
        "kind": "research_report",
        "symbol": _canonical_symbol(symbol),
        "date": d.isoformat() if d else _date_text(_first(row, "日期", "发布时间")),
        "title": title,
        "summary": "；".join(summary_parts),
        "source": "eastmoney_research",
        "media": org or "东方财富研报",
        "url": url,
        "tags": _event_tags(title),
    }


def _dedupe_title_key(title: str) -> str:
    s = str(title or "").strip().lower()
    s = re.sub(r"\s+", "", s)
    s = s.replace("（", "(").replace("）", ")").replace("：", ":")
    s = re.sub(r"^(?:[a-z]*\d{6}(?:\.[a-z]{2})?|永安行|[:：\s-])+", "", s, flags=re.IGNORECASE)
    for token in ("科技股份有限公司", "股份有限公司", "有限公司"):
        s = s.replace(token, "")
    s = re.sub(r"[，,。．.、：:；;（）()【】\[\]《》<>“”\"'\\-]", "", s)
    return s


def _dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        title = re.sub(r"\s+", "", str(item.get("title") or "")).lower()
        title_key = _dedupe_title_key(str(item.get("title") or ""))
        url = str(item.get("url") or "").strip().lower()
        day = str(item.get("date") or "")[:10]
        keys = {url, f"{day}:{title[:80]}", f"{day}:{title_key[:80]}"}
        keys = {x for x in keys if x}
        if not title or keys.intersection(seen):
            continue
        seen.update(keys)
        item["score"] = _event_score(item)
        out.append(item)
    out.sort(key=lambda x: (str(x.get("date") or ""), int(x.get("score") or 0)), reverse=True)
    return out


def _items_markdown(items: list[dict[str, Any]], limit: int = 12, *, include_summary: bool = False) -> str:
    lines: list[str] = []
    for item in items[: max(1, int(limit))]:
        ds = str(item.get("date") or "-")[:10]
        title = _cell(item.get("title"), 180) or "-"
        media = _cell(item.get("media") or item.get("source"), 80)
        tags = item.get("tags") if isinstance(item.get("tags"), list) else []
        tag_text = f" [{' / '.join(str(x) for x in tags[:3])}]" if tags else ""
        line = f"- {ds} | {title}{tag_text}"
        if media:
            line += f" | {media}"
        url = _cell(item.get("url"), 240)
        if url:
            line += f" | {url}"
        lines.append(line)
        if include_summary:
            summary = _cell(item.get("summary"), 240)
            if summary:
                lines.append(f"  摘要: {summary}")
    return "\n".join(lines)


def _source_diag_row(source: str, rows: list[Any], error: str = "") -> dict[str, Any]:
    return {"source": source, "count": len(rows or []), "ok": bool(rows), "error": error}


class AShareResearchDataService:
    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def _cached(self, key: str, loader: Callable[[], Any]) -> Any:
        now = time.time()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - float(hit[0]) <= _CACHE_TTL_SECONDS:
                return hit[1]
        try:
            value = loader()
        except Exception:
            value = []
        with self._lock:
            self._cache[key] = (now, value)
        return value

    def _ak_records(self, fn_name: str, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        key = f"ak:{fn_name}:{args!r}:{kwargs!r}"

        def load() -> list[dict[str, Any]]:
            import akshare as ak

            fn = getattr(ak, fn_name)
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                return _records_from_frame(fn(*args, **kwargs))

        value = self._cached(key, load)
        return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []

    def _public_quote(self, symbol: str) -> dict[str, Any]:
        try:
            from api.services.public_market_data_service import get_public_market_data_service

            payload = get_public_market_data_service().quote([_canonical_symbol(symbol)], source="auto")
            items = payload.get("items") if isinstance(payload, dict) else None
            if isinstance(items, list) and items and isinstance(items[0], dict):
                return dict(items[0])
        except Exception:
            return {}
        return {}

    def _public_valuation(self, symbol: str) -> dict[str, Any]:
        try:
            from api.services.cn_market_data_service import get_cn_market_data_service

            payload = get_cn_market_data_service().valuation(_canonical_symbol(symbol), source="auto")
            item = payload.get("item") if isinstance(payload, dict) else None
            if isinstance(item, dict):
                return dict(item)
        except Exception:
            return {}
        return {}

    def _snapshot_cache_path(self, symbol: str) -> str:
        return os.path.join(_SNAPSHOT_CACHE_DIR, f"{_safe_slug(_canonical_symbol(symbol))}.json")

    def _load_snapshot_cache(self, symbol: str) -> dict[str, Any] | None:
        path = self._snapshot_cache_path(symbol)
        try:
            st = os.stat(path)
            if time.time() - float(st.st_mtime) > _SNAPSHOT_CACHE_TTL_SECONDS:
                return None
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _save_snapshot_cache(self, symbol: str, payload: dict[str, Any]) -> None:
        try:
            os.makedirs(_SNAPSHOT_CACHE_DIR, exist_ok=True)
            path = self._snapshot_cache_path(symbol)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception:
            return

    def _collect_fundamentals(self, symbol: str) -> dict[str, Any]:
        sym = _canonical_symbol(symbol)
        code = _symbol_code(sym)
        secucode = _ak_secucode(sym)
        em_code = _eastmoney_symbol(sym)
        quote = self._public_quote(sym)
        valuation = self._public_valuation(sym)
        info = self._ak_records("stock_individual_info_em", symbol=code)
        indicators = self._ak_records("stock_financial_analysis_indicator_em", symbol=secucode)
        if not indicators:
            indicators = self._ak_records("stock_financial_analysis_indicator_em", symbol=secucode, indicator="按报告期")
        abstract = self._ak_records("stock_financial_abstract", symbol=code)
        business = self._ak_records("stock_zygc_em", symbol=em_code)
        return {
            "symbol": sym,
            "code": code,
            "secucode": secucode,
            "em_code": em_code,
            "quote": quote,
            "valuation": valuation,
            "info": info,
            "indicators": indicators,
            "abstract": abstract,
            "business": business,
        }

    def _fundamental_diagnostics(self, ctx: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {"source": "public_quote", "count": 1 if ctx.get("quote") else 0, "ok": bool(ctx.get("quote"))},
            {"source": "tencent_valuation", "count": 1 if ctx.get("valuation") else 0, "ok": bool(ctx.get("valuation"))},
            {"source": "eastmoney_company_info", "count": len(ctx.get("info") or []), "ok": bool(ctx.get("info"))},
            {"source": "eastmoney_financial_indicator", "count": len(ctx.get("indicators") or []), "ok": bool(ctx.get("indicators"))},
            {"source": "sina_financial_abstract", "count": len(ctx.get("abstract") or []), "ok": bool(ctx.get("abstract"))},
            {"source": "eastmoney_business_composition", "count": len(ctx.get("business") or []), "ok": bool(ctx.get("business"))},
        ]

    def _company_name(self, ctx: dict[str, Any]) -> str:
        for row in list(ctx.get("info") or []) + list(ctx.get("indicators") or []):
            if not isinstance(row, dict):
                continue
            name = _first(row, "股票简称", "SECURITY_NAME_ABBR", "简称", "名称")
            if name:
                return name
        return ""

    def _latest_indicator_row(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        usable = [r for r in rows if isinstance(r, dict)]
        if not usable:
            return {}
        return sorted(usable, key=lambda r: _row_date(r) or date.min, reverse=True)[0]

    def _indicator_metrics(self, row: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for label, keys, kind in _INDICATOR_FIELDS:
            val = _row_get_any(row, keys)
            if val is not None:
                out.append({"指标": label, "数值": _format_number(val, kind), "原字段": "/".join(keys)})
        return out

    def _abstract_latest_metrics(self, rows: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
        period_cols: list[str] = []
        for row in rows[:12]:
            for key in row.keys():
                sk = str(key)
                if re.fullmatch(r"20\d{6}", sk) or re.fullmatch(r"20\d{2}", sk):
                    if sk not in period_cols:
                        period_cols.append(sk)
        period_cols.sort(reverse=True)
        if not period_cols:
            return "", []
        latest = period_cols[0]
        out: list[dict[str, Any]] = []
        for label, hints, kind in _ABSTRACT_METRICS:
            for row in rows:
                metric = _first(row, "指标", "项目", "item", "metric")
                if metric and any(hint in metric for hint in hints):
                    val = row.get(latest)
                    if _cell(val):
                        out.append({"指标": label, "报告期": latest, "数值": _format_number(val, kind), "原指标": metric})
                    break
        return latest, out

    def _business_latest_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        usable = [r for r in rows if isinstance(r, dict)]
        if not usable:
            return []
        latest = max((_row_date(r) or date.min for r in usable), default=date.min)
        if latest != date.min:
            usable = [r for r in usable if (_row_date(r) or date.min) == latest]
        def rev(row: dict[str, Any]) -> float:
            return abs(_safe_float(row.get("主营收入"), 0.0) or 0.0)
        out: list[dict[str, Any]] = []
        for row in sorted(usable, key=rev, reverse=True)[:8]:
            out.append(
                {
                    "报告期": _date_text(_first(row, "报告日期", "报告期")),
                    "分类": _first(row, "分类类型", "类型"),
                    "构成": _first(row, "主营构成", "项目"),
                    "收入": _format_number(row.get("主营收入"), "amount"),
                    "收入比例": _format_number(row.get("收入比例"), "ratio_pct"),
                    "毛利率": _format_number(row.get("毛利率"), "ratio_pct"),
                }
            )
        return out

    def _fundamental_snapshot_v2(self, ctx: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        sym = str(ctx.get("symbol") or "")
        name = self._company_name(ctx)
        indicators = [r for r in (ctx.get("indicators") or []) if isinstance(r, dict)]
        abstract = [r for r in (ctx.get("abstract") or []) if isinstance(r, dict)]
        business = [r for r in (ctx.get("business") or []) if isinstance(r, dict)]
        latest_indicator = self._latest_indicator_row(indicators)
        latest_period = _first(latest_indicator, "REPORT_DATE_NAME", "REPORT_DATE") or "-"
        indicator_metrics = self._indicator_metrics(latest_indicator)
        abstract_period, abstract_metrics = self._abstract_latest_metrics(abstract)
        business_rows = self._business_latest_rows(business)
        valuation = ctx.get("valuation") if isinstance(ctx.get("valuation"), dict) else {}

        summary = [
            f"- 标的: {sym}" + (f" / {name}" if name else ""),
            f"- 最新财报期: {latest_period}",
        ]
        if valuation:
            summary.append(
                "- 估值: "
                f"PE(TTM) {_cell(valuation.get('pe_ttm')) or '-'} / "
                f"PB {_cell(valuation.get('pb')) or '-'} / "
                f"总市值 {_cell(valuation.get('total_market_cap')) or '-'} 亿"
            )
        if abstract_period and abstract_metrics:
            summary.append(f"- 财务摘要最新列: {abstract_period}")

        parts = ["## Fundamental snapshot v2", "", *summary]
        if indicator_metrics:
            parts.extend(["", "### 最新财报核心指标", "", _markdown_table(indicator_metrics, preferred=["指标", "数值"], limit=20)])
        if abstract_metrics:
            parts.extend(["", "### 财务摘要横截面", "", _markdown_table(abstract_metrics, preferred=["指标", "报告期", "数值"], limit=20)])
        if business_rows:
            parts.extend(["", "### 主营构成 Top", "", _markdown_table(business_rows, preferred=["报告期", "分类", "构成", "收入", "收入比例", "毛利率"], limit=8)])
        data = {
            "symbol": sym,
            "name": name,
            "latest_period": latest_period,
            "indicator_metrics": indicator_metrics,
            "abstract_period": abstract_period,
            "abstract_metrics": abstract_metrics,
            "business_top": business_rows,
            "diagnostics": self._fundamental_diagnostics(ctx),
        }
        return "\n".join(parts).strip(), data

    def _collect_news_context(self, symbol: str, start_date: str, end_date: str) -> dict[str, Any]:
        sym = _canonical_symbol(symbol)
        code = _symbol_code(sym)
        end_default = date.today()
        begin_default = end_default - timedelta(days=30)
        bd = _compact_date(start_date, begin_default)
        ed = _compact_date(end_date, end_default)

        em_news = _filter_by_date(self._ak_records("stock_news_em", symbol=code), start_date, end_date)
        cninfo_notices = _filter_by_date(
            self._ak_records(
                "stock_zh_a_disclosure_report_cninfo",
                symbol=code,
                market="沪深京",
                start_date=bd,
                end_date=ed,
            ),
            start_date,
            end_date,
        )
        em_notices = self._ak_records(
            "stock_individual_notice_report",
            security=code,
            symbol="全部",
            begin_date=bd,
            end_date=ed,
        )
        if not em_notices:
            em_notices = self._ak_records(
                "stock_individual_notice_report",
                security=code,
                begin_date=bd,
                end_date=ed,
            )
        em_notices = _filter_by_date(em_notices, start_date, end_date)
        research_reports_all = self._ak_records("stock_research_report_em", symbol=code)
        research_reports = _filter_by_date(research_reports_all, start_date, end_date)

        items: list[dict[str, Any]] = []
        items.extend(x for row in em_news if (x := _normalize_news_row(row, "eastmoney_news", sym)))
        items.extend(x for row in cninfo_notices if (x := _normalize_notice_row(row, "cninfo_disclosure", sym)))
        items.extend(x for row in em_notices if (x := _normalize_notice_row(row, "eastmoney_notice", sym)))
        items.extend(x for row in research_reports if (x := _normalize_research_report_row(row, sym)))
        deduped = _dedupe_items(items)

        historical_reports: list[dict[str, Any]] = []
        if not research_reports and research_reports_all:
            historical_reports = [
                x
                for row in research_reports_all[:3]
                if (x := _normalize_research_report_row(row, sym))
            ]

        return {
            "symbol": sym,
            "start_date": start_date or bd,
            "end_date": end_date or ed,
            "items": deduped,
            "news": [x for x in deduped if x.get("kind") == "news"],
            "notices": [x for x in deduped if x.get("kind") == "notice"],
            "research_reports": [x for x in deduped if x.get("kind") == "research_report"],
            "historical_reports": historical_reports,
            "diagnostics": [
                _source_diag_row("eastmoney_news", em_news),
                _source_diag_row("cninfo_disclosure", cninfo_notices),
                _source_diag_row("eastmoney_notice", em_notices),
                _source_diag_row("eastmoney_research", research_reports or research_reports_all),
            ],
        }

    def build_market_report(self, symbol: str, days: int = 180) -> str:
        sym = _canonical_symbol(symbol)
        try:
            from api.services.public_market_data_service import get_public_market_data_service

            payload = get_public_market_data_service().klines(
                symbol=sym,
                period="1d",
                days=max(30, min(int(days), 3650)),
                source="auto",
            )
        except Exception as exc:
            return f"# {sym} A股行情数据（公共源）\n\n暂无可用行情数据：{exc}\n"

        items = payload.get("items") if isinstance(payload, dict) else None
        rows = [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []
        closes = [_safe_float(r.get("close")) for r in rows]
        closes = [float(x) for x in closes if x is not None and x > 0]
        if len(closes) < 2:
            return f"# {sym} A股行情数据（公共源）\n\n暂无足够K线数据。\n"
        last = closes[-1]

        def ret(n: int) -> str:
            if len(closes) <= n or closes[-n - 1] <= 0:
                return "-"
            return f"{(last / closes[-n - 1] - 1.0) * 100.0:.2f}%"

        source = str(payload.get("source") or "-") if isinstance(payload, dict) else "-"
        latest = str(rows[-1].get("date") or "-") if rows else "-"
        return "\n".join(
            [
                f"# {sym} A股行情数据（公共源）",
                "",
                f"- 数据源: {source}",
                f"- 最新交易日: {latest}",
                f"- 最新收盘价: {last:.4f}",
                f"- 5日涨跌幅: {ret(5)}",
                f"- 20日涨跌幅: {ret(20)}",
                f"- 60日涨跌幅: {ret(60)}",
                "",
                "## 最近K线",
                "",
                _markdown_table(
                    rows[-12:],
                    preferred=["date", "open", "high", "low", "close", "volume", "amount", "source"],
                    limit=12,
                )
                or "暂无。",
            ]
        ).strip() + "\n"

    def build_fundamentals_report(self, symbol: str, curr_date: str = "") -> str:
        ctx = self._collect_fundamentals(symbol)
        sym = str(ctx.get("symbol") or _canonical_symbol(symbol))
        quote = ctx.get("quote") if isinstance(ctx.get("quote"), dict) else {}
        valuation = ctx.get("valuation") if isinstance(ctx.get("valuation"), dict) else {}
        info = [r for r in (ctx.get("info") or []) if isinstance(r, dict)]
        indicators = [r for r in (ctx.get("indicators") or []) if isinstance(r, dict)]
        abstract = [r for r in (ctx.get("abstract") or []) if isinstance(r, dict)]
        business = [r for r in (ctx.get("business") or []) if isinstance(r, dict)]
        snapshot_md, _snapshot_data = self._fundamental_snapshot_v2(ctx)

        parts = [
            f"# {sym} A股基本面数据（公共源）",
            "",
            "- 数据源: mootdx/Tencent/AkShare/EastMoney/Sina public endpoints",
            f"- 生成日期: {curr_date or date.today().isoformat()}",
            "- 说明: 公共数据为延迟/尽力而为，不需要券商API或交易权限。",
            "",
            snapshot_md,
        ]
        if quote:
            parts.extend(
                [
                    "",
                    "## 行情快照",
                    "",
                    f"- 最新价: {_cell(quote.get('last') or quote.get('price')) or '-'}",
                    f"- 涨跌幅: {_cell(quote.get('change_pct')) or '-'}",
                    f"- 昨收: {_cell(quote.get('prev_close')) or '-'}",
                    f"- 来源: {_cell(quote.get('source_label') or quote.get('source')) or '-'}",
                ]
            )
        if valuation:
            parts.extend(
                [
                    "",
                    "## Valuation snapshot",
                    "",
                    f"- PE(TTM): {_cell(valuation.get('pe_ttm')) or '-'}",
                    f"- PB: {_cell(valuation.get('pb')) or '-'}",
                    f"- Total market cap: {_cell(valuation.get('total_market_cap')) or '-'} CNY 100M",
                    f"- Float market cap: {_cell(valuation.get('float_market_cap')) or '-'} CNY 100M",
                    f"- Source: {_cell(valuation.get('source')) or 'tencent'}",
                ]
            )
        if info:
            parts.extend(["", "## 公司资料", "", _bullet_pairs(info, limit=18) or "暂无。"])
        if indicators:
            parts.extend(
                [
                    "",
                    "## 核心财务指标",
                    "",
                    _markdown_table(
                        indicators,
                        preferred=[
                            "REPORT_DATE",
                            "报告期",
                            "日期",
                            "基本每股收益",
                            "扣非每股收益",
                            "每股净资产",
                            "净资产收益率",
                            "销售毛利率",
                            "营业总收入",
                            "归属净利润",
                        ],
                        limit=8,
                    )
                    or "暂无。",
                ]
            )
        if abstract:
            parts.extend(
                [
                    "",
                    "## 财务摘要",
                    "",
                    _markdown_table(
                        abstract,
                        preferred=["报告期", "公告日期", "营业收入", "净利润", "净资产收益率", "每股收益", "每股净资产"],
                        limit=8,
                    )
                    or "暂无。",
                ]
            )
        if business:
            parts.extend(
                [
                    "",
                    "## 主营构成",
                    "",
                    _markdown_table(
                        business,
                        preferred=["报告期", "分类类型", "主营构成", "主营收入", "收入比例", "主营成本", "毛利率"],
                        limit=12,
                    )
                    or "暂无。",
                ]
            )
        parts.extend(
            [
                "",
                "## 数据源诊断",
                "",
                _markdown_table(self._fundamental_diagnostics(ctx), preferred=["source", "count", "ok", "error"], limit=12) or "暂无。",
            ]
        )
        if len(parts) <= 5:
            parts.append("\n暂无可用公共基本面数据。")
        return "\n".join(parts).strip() + "\n"

    def build_statement_report(self, symbol: str, statement: str, freq: str = "quarterly", curr_date: str = "") -> str:
        sym = _canonical_symbol(symbol)
        em_code = _eastmoney_symbol(sym)
        st = str(statement or "income").strip().lower()
        fn_map = {
            "income": ("stock_profit_sheet_by_report_em", "利润表"),
            "balance": ("stock_balance_sheet_by_report_em", "资产负债表"),
            "cashflow": ("stock_cash_flow_sheet_by_report_em", "现金流量表"),
        }
        fn_name, title = fn_map.get(st, fn_map["income"])
        rows = self._ak_records(fn_name, symbol=em_code)
        preferred = [
            "REPORT_DATE",
            "报告期",
            "公告日期",
            "营业总收入",
            "营业收入",
            "营业总成本",
            "净利润",
            "归属于母公司股东的净利润",
            "资产总计",
            "负债合计",
            "所有者权益合计",
            "经营活动产生的现金流量净额",
        ]
        body = _markdown_table(rows, preferred=preferred, limit=8) if rows else ""
        return "\n".join(
            [
                f"# {sym} {title}（公共源）",
                "",
                f"- 数据源: AkShare / EastMoney public endpoints",
                f"- 频率参数: {freq or '-'}",
                f"- 生成日期: {curr_date or date.today().isoformat()}",
                "",
                body or "暂无可用数据。",
            ]
        ).strip() + "\n"

    def build_news_report(self, symbol: str, start_date: str, end_date: str, limit: int = 12) -> str:
        ctx = self._collect_news_context(symbol, start_date, end_date)
        sym = str(ctx.get("symbol") or _canonical_symbol(symbol))
        news = [x for x in (ctx.get("news") or []) if isinstance(x, dict)]
        notices = [x for x in (ctx.get("notices") or []) if isinstance(x, dict)]
        reports = [x for x in (ctx.get("research_reports") or []) if isinstance(x, dict)]
        historical_reports = [x for x in (ctx.get("historical_reports") or []) if isinstance(x, dict)]
        event_items = [x for x in (ctx.get("items") or []) if isinstance(x, dict) and x.get("tags")]
        diagnostics = [x for x in (ctx.get("diagnostics") or []) if isinstance(x, dict)]

        parts = [
            f"# {sym} A股新闻与公告（公共源）",
            "",
            "- 数据源: EastMoney news / CNInfo disclosure / EastMoney notice / EastMoney research",
            f"- 请求区间: {ctx.get('start_date') or start_date} ~ {ctx.get('end_date') or end_date}",
        ]

        if event_items:
            parts.extend(["", "## 事件摘要", "", _items_markdown(event_items, limit=min(8, max(1, int(limit))), include_summary=True)])
        else:
            parts.extend(["", "## 事件摘要", "", "暂无可识别的重大事件标签。"])

        if news:
            parts.extend(["", "## 个股新闻", "", _items_markdown(news, limit=limit, include_summary=True)])
        else:
            parts.extend(["", "## 个股新闻", "", "暂无可用个股新闻。"])

        if notices:
            parts.extend(["", "## 公司公告", "", _items_markdown(notices, limit=limit, include_summary=False)])
        else:
            parts.extend(["", "## 公司公告", "", "暂无可用公告。"])

        if reports:
            parts.extend(["", "## 个股研报", "", _items_markdown(reports, limit=min(6, max(1, int(limit))), include_summary=True)])
        elif historical_reports:
            parts.extend(["", "## 历史研报", "", _items_markdown(historical_reports, limit=3, include_summary=True)])
        else:
            parts.extend(["", "## 个股研报", "", "暂无可用研报。"])

        parts.extend(["", "## 数据源诊断", "", _markdown_table(diagnostics, preferred=["source", "count", "ok", "error"], limit=8) or "暂无。"])
        return "\n".join(parts).strip() + "\n"

    def build_global_news_report(self, curr_date: str, look_back_days: int = 7, limit: int = 5) -> str:
        rows = self._ak_records("stock_news_main_cx")
        parts = [
            "# A股/宏观新闻（公共源）",
            "",
            "- 数据源: AkShare public endpoints",
            f"- 当前日期: {curr_date or date.today().isoformat()}",
            f"- 回看天数: {look_back_days}",
        ]
        if not rows:
            parts.extend(["", "暂无可用全局新闻。"])
            return "\n".join(parts).strip() + "\n"
        rows = rows[: max(1, int(limit))]
        parts.extend(["", _markdown_table(rows, preferred=["发布时间", "标题", "摘要", "来源", "链接"], limit=limit) or "暂无。"])
        return "\n".join(parts).strip() + "\n"

    def build_public_research_snapshot(
        self,
        symbol: str,
        *,
        reason: str = "",
        user_question: str = "",
    ) -> dict[str, Any]:
        sym = _canonical_symbol(symbol)
        cached = self._load_snapshot_cache(sym)
        market_report = self.build_market_report(sym)
        fundamentals = self.build_fundamentals_report(sym)
        end_date = date.today().isoformat()
        start_date = (date.today() - timedelta(days=30)).isoformat()
        news = self.build_news_report(sym, start_date=start_date, end_date=end_date)
        fund_ctx = self._collect_fundamentals(sym)
        news_ctx = self._collect_news_context(sym, start_date, end_date)
        fund_snapshot_md, fund_snapshot_data = self._fundamental_snapshot_v2(fund_ctx)
        data_diagnostics = {
            "schema": SCHEMA,
            "cache_used": False,
            "cache_path": self._snapshot_cache_path(sym),
            "fundamentals": self._fundamental_diagnostics(fund_ctx),
            "news": news_ctx.get("diagnostics") or [],
            "news_item_count": len(news_ctx.get("items") or []),
            "event_item_count": len([x for x in (news_ctx.get("items") or []) if isinstance(x, dict) and x.get("tags")]),
        }

        if cached and isinstance(cached.get("stage_reports"), dict):
            cached_reports = cached.get("stage_reports") or {}
            if "暂无可用个股新闻" in news and cached_reports.get("analyst_news"):
                news = str(cached_reports.get("analyst_news") or news)
                data_diagnostics["cache_used"] = True
                data_diagnostics["cache_reason"] = "fresh_news_empty"
            if "暂无可用公共基本面数据" in fundamentals and cached_reports.get("analyst_fundamentals"):
                fundamentals = str(cached_reports.get("analyst_fundamentals") or fundamentals)
                data_diagnostics["cache_used"] = True
                data_diagnostics["cache_reason"] = "fresh_fundamentals_empty"

        stage_reports = {
            "analyst_market": market_report,
            "analyst_news": news,
            "analyst_fundamentals": fundamentals,
        }
        report_parts = [
            f"# {sym} A股公共研究数据包",
            "",
            "## 说明",
            "",
            "- 这是无券商 API 模式下的公共数据兜底报告。",
            "- 数据源来自 mootdx/Tencent/AkShare/EastMoney/CNInfo/Sina public endpoints，适合研究和草稿分析，不等同于交易级行情。",
        ]
        if reason:
            report_parts.append(f"- 触发原因: {reason}")
        if user_question:
            report_parts.extend(["", "## 用户关注点", "", user_question[:4000]])
        if fund_snapshot_md:
            report_parts.extend(["", "## 基本面速览", "", fund_snapshot_md])
        report_parts.extend(["", "## 行情", "", market_report, "", "## 新闻与公告", "", news, "", "## 基本面", "", fundamentals])
        payload = {
            "symbol": sym,
            "request_symbol": sym,
            "market": "cn",
            "source": "local_public",
            "available": True,
            "reason": reason or "local_public_research_snapshot",
            "action": "hold",
            "confidence": 0.5,
            "decision_text": "已生成A股公共研究数据包；完整多智能体结论需等待LLM流程完成。",
            "stage_reports": stage_reports,
            "research_report_markdown": "\n".join(report_parts).strip() + "\n",
            "generated_at": datetime.now().isoformat(),
            "ran_analysts": ["market", "news", "fundamentals"],
            "fundamental_snapshot_v2": fund_snapshot_data,
            "data_diagnostics": data_diagnostics,
        }
        self._save_snapshot_cache(sym, payload)
        return payload


_SERVICE: AShareResearchDataService | None = None
_SERVICE_LOCK = threading.Lock()


def get_a_share_research_data_service() -> AShareResearchDataService:
    global _SERVICE
    if _SERVICE is None:
        with _SERVICE_LOCK:
            if _SERVICE is None:
                _SERVICE = AShareResearchDataService()
    return _SERVICE
