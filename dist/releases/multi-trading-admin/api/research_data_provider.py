import json
import math
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional, Protocol


TA_ANALYST_ORDER = ["market", "social", "news", "fundamentals"]
TA_ANALYST_AGENT_NAMES = {
    "market": "Market Analyst",
    "social": "Social Analyst",
    "news": "News Analyst",
    "fundamentals": "Fundamentals Analyst",
}
TA_ANALYST_REPORT_MAP = {
    "market": "market_report",
    "social": "sentiment_report",
    "news": "news_report",
    "fundamentals": "fundamentals_report",
}

_OPENBB_AUTOSTART_LOCK = threading.Lock()
_OPENBB_LAST_AUTOSTART_TS = 0.0


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _http_get_json(url: str, timeout: float = 3.0) -> Optional[dict[str, Any]]:
    try:
        with urllib.request.urlopen(url, timeout=max(0.5, float(timeout))) as resp:
            if int(getattr(resp, "status", 200) or 200) != 200:
                return None
            raw = resp.read().decode("utf-8", errors="ignore")
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
    except Exception:
        return None


def _http_get_any(url: str, timeout: float = 3.0) -> Any:
    try:
        with urllib.request.urlopen(url, timeout=max(0.5, float(timeout))) as resp:
            if int(getattr(resp, "status", 200) or 200) != 200:
                return None
            raw = resp.read().decode("utf-8", errors="ignore")
            return json.loads(raw)
    except Exception:
        return None


def _http_ping(url: str, timeout: float = 3.0) -> bool:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=max(0.5, float(timeout))) as resp:
            code = int(getattr(resp, "status", 0) or 0)
            return 200 <= code < 500
    except Exception:
        return False


def _ta_event(kind: str, **payload: Any) -> dict[str, Any]:
    out = {"kind": kind, "ts": datetime.now().isoformat()}
    out.update(payload)
    return out


def _ta_message_content(message: Any) -> str:
    content = getattr(message, "content", None)
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, dict):
        return str(content.get("text") or "").strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    parts.append(str(text))
            elif isinstance(item, str):
                parts.append(item)
        return " ".join(x.strip() for x in parts if x and x.strip())
    return str(content).strip()


def _ta_message_type(message: Any) -> str:
    cls = type(message).__name__.lower()
    if "human" in cls:
        return "User"
    if "tool" in cls:
        return "Data"
    if "ai" in cls:
        return "Agent"
    return "System"


def _ta_tool_call_items(message: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for call in list(getattr(message, "tool_calls", None) or []):
        if isinstance(call, dict):
            rows.append({"name": str(call.get("name") or ""), "args": call.get("args") or {}})
        else:
            rows.append({"name": str(getattr(call, "name", "") or ""), "args": getattr(call, "args", {}) or {}})
    return rows


class ResearchProvider(Protocol):
    def get_strong_stocks(self, market: str, top_n: int, kline: str) -> list[dict[str, Any]]:
        ...

    def score_symbol(
        self,
        symbol: str,
        strategies: list[str],
        backtest_days: int,
        kline: str,
        strategy_params_map: Optional[dict[str, dict[str, Any]]] = None,
    ) -> list[dict[str, Any]]:
        ...

    def run_pair_backtest(self, market: str, backtest_days: int, kline: str) -> dict[str, Any]:
        ...


class LongPortResearchProvider:
    def __init__(self, trader: Any) -> None:
        self._trader = trader

    def get_strong_stocks(self, market: str, top_n: int, kline: str) -> list[dict[str, Any]]:
        return self._trader.screen_strong_stocks(market=market, limit=max(1, int(top_n)), kline=str(kline))

    def score_symbol(
        self,
        symbol: str,
        strategies: list[str],
        backtest_days: int,
        kline: str,
        strategy_params_map: Optional[dict[str, dict[str, Any]]] = None,
    ) -> list[dict[str, Any]]:
        return self._trader.score_strategies(
            symbol=symbol,
            strategies=list(strategies),
            days=max(60, min(240, int(backtest_days))),
            kline=str(kline),
            initial_capital=100000.0,
            strategy_params_map=strategy_params_map if isinstance(strategy_params_map, dict) else None,
            cfg=self._trader.get_config(),
        )

    def run_pair_backtest(self, market: str, backtest_days: int, kline: str) -> dict[str, Any]:
        return self._trader.pair_portfolio_backtest(
            market=str(market),
            days=max(90, int(backtest_days)),
            kline=str(kline),
            initial_capital=100000.0,
        )


class OpenBBClient:
    def __init__(self) -> None:
        self.enabled = str(os.getenv("OPENBB_ENABLED", "0")).strip().lower() in {"1", "true", "yes", "on"}
        self.base_url = (str(os.getenv("OPENBB_BASE_URL", "")).strip() or "http://127.0.0.1:6900").rstrip("/")
        self.timeout = max(1.0, _env_float("OPENBB_TIMEOUT_SECONDS", 3.5))

    def is_configured(self) -> bool:
        return self.enabled and bool(self.base_url)

    def _local_port(self) -> tuple[bool, str, int]:
        parsed = urllib.parse.urlparse(self.base_url if "://" in self.base_url else f"http://{self.base_url}")
        host = parsed.hostname or "127.0.0.1"
        port = int(parsed.port or 6900)
        return host in {"127.0.0.1", "localhost", "0.0.0.0", "::1"}, ("127.0.0.1" if host in {"0.0.0.0", "::"} else host), port

    @staticmethod
    def _port_open(host: str, port: int) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.35)
                return s.connect_ex((host, int(port))) == 0
        except Exception:
            return False

    @staticmethod
    def _openbb_cmd(root: Path) -> tuple[list[str] | None, str]:
        candidates = [
            root / ".openbb-venv" / "Scripts" / "openbb-api.exe",
            root / ".openbb-venv" / "Scripts" / "openbb-api",
            root / ".venv" / "Scripts" / "openbb-api.exe",
            root / ".venv" / "Scripts" / "openbb-api",
        ]
        for p in candidates:
            if p.exists() and p.is_file():
                return [str(p)], str(p)
        found = shutil.which("openbb-api.exe") or shutil.which("openbb-api")
        if found:
            return [found], found
        for py in (root / ".openbb-venv" / "Scripts" / "python.exe", root / ".venv" / "Scripts" / "python.exe"):
            if py.exists() and py.is_file():
                return [str(py), "-m", "openbb_platform_api.main"], f"{py} -m openbb_platform_api.main"
        return None, ""

    def _maybe_autostart(self) -> dict[str, Any]:
        global _OPENBB_LAST_AUTOSTART_TS
        auto_start = str(os.getenv("OPENBB_AUTO_START", "1")).strip().lower() not in {"0", "false", "no", "off"}
        if not auto_start:
            return {"attempted": False, "reason": "openbb_auto_start_disabled"}
        is_local, host, port = self._local_port()
        if not is_local:
            return {"attempted": False, "reason": "openbb_remote_base_url"}
        if self._port_open(host, port):
            return {"attempted": False, "reason": "openbb_port_occupied"}
        now = time.time()
        with _OPENBB_AUTOSTART_LOCK:
            if now - _OPENBB_LAST_AUTOSTART_TS < 20:
                return {"attempted": False, "reason": "openbb_autostart_cooldown"}
            root = Path(__file__).resolve().parents[1]
            cmd, hint = self._openbb_cmd(root)
            if not cmd:
                return {"attempted": False, "reason": "openbb_command_not_found"}
            _OPENBB_LAST_AUTOSTART_TS = now
            flags = 0
            if os.name == "nt":
                flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
            subprocess.Popen(  # noqa: S603
                cmd,
                cwd=str(root),
                env=os.environ.copy(),
                creationflags=flags,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return {"attempted": True, "cmd": hint}

    def ensure_available(self) -> dict[str, Any]:
        health = self.health()
        if health.get("ok") or not self.is_configured():
            return health
        autostart = self._maybe_autostart()
        if autostart.get("attempted"):
            for _ in range(20):
                time.sleep(1)
                health = self.health()
                if health.get("ok"):
                    health["autostart"] = autostart
                    return health
        health["autostart"] = autostart
        return health

    @staticmethod
    def _mean(vals: list[float]) -> float:
        if not vals:
            return 0.0
        return float(sum(vals) / len(vals))

    @staticmethod
    def _std(vals: list[float]) -> float:
        if len(vals) < 2:
            return 0.0
        m = OpenBBClient._mean(vals)
        var = sum((x - m) ** 2 for x in vals) / (len(vals) - 1)
        return float(math.sqrt(max(0.0, var)))

    @staticmethod
    def _parse_timestamp(v: Any) -> float:
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v or "").strip()
        if not s:
            return 0.0
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            from datetime import datetime

            return float(datetime.fromisoformat(s).timestamp())
        except Exception:
            return 0.0

    @staticmethod
    def _extract_close_from_row(row: Any) -> Optional[float]:
        if not isinstance(row, dict):
            return None
        for k in ("close", "adj_close", "close_price", "c", "Close"):
            if k in row:
                val = _safe_float(row.get(k), default=float("nan"))
                if math.isfinite(val):
                    return float(val)
        return None

    @staticmethod
    def _extract_rows(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        if not isinstance(payload, dict):
            return []
        for key in ("results", "items", "data", "rows", "historical", "quotes", "values"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [x for x in rows if isinstance(x, dict)]
            if isinstance(rows, dict):
                nested = rows.get("data")
                if isinstance(nested, list):
                    return [x for x in nested if isinstance(x, dict)]
        return []

    def _fetch_daily_closes(self, symbol: str, bars: int = 180) -> list[float]:
        sym = urllib.parse.quote_plus(str(symbol or "").strip())
        lim = max(90, min(365, int(bars)))
        end_date = date.today()
        start_date = end_date - timedelta(days=max(220, lim * 3))
        start_q = urllib.parse.quote_plus(start_date.isoformat())
        end_q = urllib.parse.quote_plus(end_date.isoformat())
        candidates = [
            f"{self.base_url}/api/v1/equity/price/historical?symbol={sym}&interval=1d&provider=yfinance&start_date={start_q}&end_date={end_q}",
            f"{self.base_url}/api/v1/equity/price/historical?symbol={sym}&interval=1d&provider=tiingo&start_date={start_q}&end_date={end_q}",
            f"{self.base_url}/api/v1/etf/historical?symbol={sym}&interval=1d&provider=yfinance&start_date={start_q}&end_date={end_q}",
            f"{self.base_url}/api/v1/index/price/historical?symbol={sym}&interval=1d&provider=yfinance&start_date={start_q}&end_date={end_q}",
        ]
        for url in candidates:
            payload = _http_get_any(url, timeout=self.timeout)
            rows = self._extract_rows(payload)
            if not rows:
                continue
            rows = sorted(
                rows,
                key=lambda r: self._parse_timestamp(r.get("date") or r.get("datetime") or r.get("timestamp")),
            )
            closes = [self._extract_close_from_row(r) for r in rows]
            out = [float(x) for x in closes if isinstance(x, (int, float)) and math.isfinite(float(x))]
            if len(out) >= 80:
                return out
        return []

    @staticmethod
    def _normalize_symbol_for_openbb(symbol: str, market: str) -> str:
        s = str(symbol or "").strip().upper()
        if not s:
            return s
        m = str(market or "").lower()
        if m == "us" and s.endswith(".US"):
            return s[:-3]
        return s

    def health(self) -> dict[str, Any]:
        if not self.is_configured():
            return {"enabled": self.enabled, "ok": False, "reason": "openbb_disabled_or_unconfigured"}
        url = f"{self.base_url}/"
        info = _http_get_json(url, timeout=self.timeout)
        ok = _http_ping(url, timeout=self.timeout)
        return {
            "enabled": self.enabled,
            "ok": bool(ok),
            "base_url": self.base_url,
            "service": info if isinstance(info, dict) else None,
        }

    def market_regime(self, market: str) -> dict[str, Any]:
        """
        OpenBB 接口版本众多，这里做 best-effort 聚合：
        - 可达则返回轻量 regime 提示
        - 不可达时返回 fallback，不影响主流程
        """
        m = str(market or "us").lower()
        if not self.is_configured():
            return {"market": m, "source": "openbb", "available": False, "reason": "openbb_disabled"}
        benchmark_map = {"us": "SPY", "hk": "2800.HK", "cn": "510300.SH"}
        benchmark = benchmark_map.get(m, "SPY")
        root_url = f"{self.base_url}/"
        if not _http_ping(root_url, timeout=self.timeout):
            return {"market": m, "source": "openbb", "available": False, "reason": "openbb_unreachable"}
        closes = self._fetch_daily_closes(symbol=benchmark, bars=180)
        if len(closes) < 80:
            return {
                "market": m,
                "source": "openbb",
                "symbol": benchmark,
                "available": False,
                "reason": "insufficient_data",
                "regime": "unknown",
            }
        rets: list[float] = []
        for i in range(1, len(closes)):
            prev = float(closes[i - 1])
            cur = float(closes[i])
            if prev <= 0:
                continue
            rets.append(cur / prev - 1.0)
        if len(rets) < 70:
            return {
                "market": m,
                "source": "openbb",
                "symbol": benchmark,
                "available": False,
                "reason": "insufficient_returns",
                "regime": "unknown",
            }
        ret_20 = float(closes[-1] / closes[-21] - 1.0)
        ma20 = self._mean([float(x) for x in closes[-20:]])
        ma60 = self._mean([float(x) for x in closes[-60:]])
        vol_20 = self._std(rets[-20:]) * math.sqrt(252.0)
        rolling_vol20: list[float] = []
        for i in range(20, len(rets) + 1):
            rolling_vol20.append(self._std(rets[i - 20 : i]) * math.sqrt(252.0))
        baseline = rolling_vol20[-120:] if len(rolling_vol20) > 120 else rolling_vol20
        vol_mu = self._mean(baseline) if baseline else vol_20
        vol_sd = self._std(baseline) if baseline else 0.0
        vol_z = float((vol_20 - vol_mu) / max(vol_sd, 1e-6))
        trend_up = bool(ma20 > ma60)
        if ret_20 > 0.0 and trend_up and vol_z < 1.0:
            regime = "risk_on"
        elif (ret_20 < 0.0 and (not trend_up)) or vol_z > 1.5:
            regime = "risk_off"
        else:
            regime = "neutral"
        trend_score = min(abs(ret_20) / 0.06, 1.0)
        ma_score = 1.0 if trend_up else 0.6
        vol_score = max(0.0, 1.0 - min(abs(vol_z) / 2.0, 1.0))
        if regime == "neutral":
            base = 0.45
            conf = base + 0.35 * trend_score + 0.20 * vol_score
        else:
            conf = 0.25 + 0.45 * trend_score + 0.20 * ma_score + 0.10 * vol_score
        confidence = round(max(0.05, min(conf, 0.99)), 3)
        from datetime import datetime

        return {
            "market": m,
            "source": "openbb",
            "symbol": benchmark,
            "available": True,
            "regime": regime,
            "confidence": confidence,
            "as_of": datetime.now().isoformat(),
            "features": {
                "ret_20": round(ret_20, 6),
                "ma20": round(ma20, 4),
                "ma60": round(ma60, 4),
                "vol_20": round(vol_20, 6),
                "vol_z": round(vol_z, 4),
            },
            "note": "openbb_rule_based_v1",
        }

    def symbol_factor(self, symbol: str, market: str, kline: str) -> dict[str, Any]:
        sym = str(symbol or "").strip().upper()
        m = str(market or "us").lower()
        if not sym:
            return {"symbol": sym, "available": False, "reason": "empty_symbol"}
        if not self.is_configured():
            return {
                "symbol": sym,
                "market": m,
                "source": "openbb",
                "available": False,
                "reason": "openbb_disabled",
                "volatility_30d": None,
                "ret_20": None,
                "ma_gap_20": None,
                "sentiment_score": None,
                "quality_score": None,
                "note": "openbb_factor_unavailable",
            }
        _ = kline
        if not _http_ping(f"{self.base_url}/", timeout=self.timeout):
            return {
                "symbol": sym,
                "market": m,
                "source": "openbb",
                "available": False,
                "reason": "openbb_unreachable",
                "volatility_30d": None,
                "ret_20": None,
                "ma_gap_20": None,
                "sentiment_score": None,
                "quality_score": None,
                "note": "openbb_factor_unavailable",
            }
        openbb_symbol = self._normalize_symbol_for_openbb(sym, m)
        closes = self._fetch_daily_closes(symbol=openbb_symbol, bars=200)
        if len(closes) < 60:
            return {
                "symbol": sym,
                "market": m,
                "source": "openbb",
                "available": False,
                "reason": "insufficient_data",
                "symbol_openbb": openbb_symbol,
                "volatility_30d": None,
                "ret_20": None,
                "ma_gap_20": None,
                "sentiment_score": None,
                "quality_score": None,
                "note": "openbb_factor_unavailable",
            }
        rets: list[float] = []
        for i in range(1, len(closes)):
            prev = float(closes[i - 1])
            cur = float(closes[i])
            if prev <= 0:
                continue
            rets.append(cur / prev - 1.0)
        if len(rets) < 40:
            return {
                "symbol": sym,
                "market": m,
                "source": "openbb",
                "available": False,
                "reason": "insufficient_returns",
                "symbol_openbb": openbb_symbol,
                "volatility_30d": None,
                "ret_20": None,
                "ma_gap_20": None,
                "sentiment_score": None,
                "quality_score": None,
                "note": "openbb_factor_unavailable",
            }
        vol_30 = self._std(rets[-30:]) * math.sqrt(252.0) if len(rets) >= 30 else self._std(rets) * math.sqrt(252.0)
        ret_20 = float(closes[-1] / closes[-21] - 1.0) if len(closes) >= 21 and closes[-21] > 0 else 0.0
        ma20 = self._mean([float(x) for x in closes[-20:]])
        ma60 = self._mean([float(x) for x in closes[-60:]])
        close_last = float(closes[-1])
        ma_gap_20 = (close_last / max(ma20, 1e-6)) - 1.0
        trend_up = ma20 > ma60
        # price-only 因子：先交付稳定可复现指标，不引入外部新闻流依赖。
        sentiment_score = 0.5 + 0.35 * math.tanh(ret_20 / 0.08) + 0.15 * math.tanh(ma_gap_20 / 0.05)
        quality_score = 0.65 * max(0.0, 1.0 - min(vol_30 / 0.60, 1.0)) + 0.35 * (1.0 if trend_up else 0.35)
        sentiment_score = max(0.0, min(sentiment_score, 1.0))
        quality_score = max(0.0, min(quality_score, 1.0))
        return {
            "symbol": sym,
            "market": m,
            "source": "openbb",
            "available": True,
            "symbol_openbb": openbb_symbol,
            "volatility_30d": round(float(vol_30), 6),
            "ret_20": round(float(ret_20), 6),
            "ma_gap_20": round(float(ma_gap_20), 6),
            "trend_up": bool(trend_up),
            "sentiment_score": round(float(sentiment_score), 4),
            "quality_score": round(float(quality_score), 4),
            "note": "openbb_factor_v1_price_based",
        }


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    """与空字符串环境变量兼容（per-user .env 可能写入 KEY= 占位）。"""
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except ValueError:
        return int(default)


def _env_float(name: str, default: float) -> float:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except ValueError:
        return float(default)


def _longbridge_market_data_ready() -> bool:
    if _env_bool("PUBLIC_MARKET_DATA_ONLY", default=False):
        return False
    active = str(os.getenv("BROKER_PROVIDER", "longbridge")).strip().lower() or "longbridge"
    if active == "longport":
        active = "longbridge"
    if active != "longbridge":
        return False
    return bool(
        str(os.getenv("LONGPORT_APP_KEY", "")).strip()
        and str(os.getenv("LONGPORT_APP_SECRET", "")).strip()
        and str(os.getenv("LONGPORT_ACCESS_TOKEN", "")).strip()
    )


def _llm_api_key_ready(provider: str) -> bool:
    p = str(provider or "openai").strip().lower()
    if p == "ollama":
        return True
    env_key = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "google": "GOOGLE_API_KEY",
        "xai": "XAI_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "qwen": "DASHSCOPE_API_KEY",
        "glm": "ZHIPUAI_API_KEY",
        "azure": "AZURE_OPENAI_API_KEY",
    }.get(p, "OPENAI_API_KEY")
    return bool(str(os.getenv(env_key, "")).strip())


_ta_deepseek_llm_create_seq = threading.local()


def _patch_tradingagents_deepseek_thinking_extra_body() -> None:
    """
    TradingAgents 对同一 llm_kwargs 创建 deep / quick 两个 OpenAI 兼容客户端；
    DeepSeek 需在 extra_body 中声明思考开关（见官方文档）。本补丁按创建顺序注入：
    第一次 create_llm_client -> deep -> thinking enabled；第二次 -> quick -> disabled。
    设 TRADINGAGENTS_DEEPSEEK_THINKING_EXTRA_BODY=false 可关闭（用于排查）。
    """
    try:
        from tradingagents.graph import trading_graph as tg_mod
        from tradingagents.llm_clients import openai_client as oa_mod
    except Exception:
        return

    flag = "_multitrading_deepseek_thinking_extra_body"
    if getattr(tg_mod.TradingAgentsGraph, flag, False):
        return

    if not _env_bool("TRADINGAGENTS_DEEPSEEK_THINKING_EXTRA_BODY", default=True):
        return

    if "extra_body" not in oa_mod._PASSTHROUGH_KWARGS:
        oa_mod._PASSTHROUGH_KWARGS = tuple(oa_mod._PASSTHROUGH_KWARGS) + ("extra_body",)

    _orig_create = tg_mod.create_llm_client

    def _wrapped_create_llm_client(
        provider: str,
        model: str,
        base_url: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        p = str(provider or "").lower()
        if p == "deepseek":
            idx = getattr(_ta_deepseek_llm_create_seq, "i", None)
            if idx is not None:
                thinking_typ = "enabled" if int(idx) == 0 else "disabled"
                _ta_deepseek_llm_create_seq.i = int(idx) + 1
                kwargs = {**kwargs, "extra_body": {"thinking": {"type": thinking_typ}}}
        return _orig_create(provider, model, base_url, **kwargs)

    tg_mod.create_llm_client = _wrapped_create_llm_client

    _orig_init = tg_mod.TradingAgentsGraph.__init__

    def _wrapped_graph_init(
        self: Any,
        selected_analysts: Any = None,
        debug: bool = False,
        config: Any = None,
        callbacks: Any = None,
    ) -> None:
        # 与 TradingAgentsGraph.__init__ 默认一致：仅传 debug/config 时 selected_analysts 为 None
        if selected_analysts is None:
            selected_analysts = ["market", "social", "news", "fundamentals"]
        cfg = config if isinstance(config, dict) else {}
        provider = str(cfg.get("llm_provider") or "").lower()
        if provider == "deepseek":
            _ta_deepseek_llm_create_seq.i = 0
            try:
                _orig_init(self, selected_analysts, debug, config, callbacks)
            finally:
                if hasattr(_ta_deepseek_llm_create_seq, "i"):
                    delattr(_ta_deepseek_llm_create_seq, "i")
        else:
            _orig_init(self, selected_analysts, debug, config, callbacks)

    tg_mod.TradingAgentsGraph.__init__ = _wrapped_graph_init  # type: ignore[method-assign]
    setattr(tg_mod.TradingAgentsGraph, flag, True)


class TradingAgentsClient:
    """
    轻量适配 TradingAgents：
    - 仅用于研究层增强，不进入下单执行链路
    - 失败自动降级，不影响原有研究流程
    """

    def __init__(self) -> None:
        self.enabled = _env_bool("TRADINGAGENTS_ENABLED", default=False)
        # 允许高级模式设置更高超时；仅保留最小值保护，避免 0/负数导致立即超时。
        self.timeout_seconds = max(5.0, _env_float("TRADINGAGENTS_TIMEOUT_SECONDS", 25.0))
        self.max_symbols = max(1, min(_env_int("TRADINGAGENTS_MAX_SYMBOLS", 3), 10))
        self.llm_provider = str(os.getenv("TRADINGAGENTS_LLM_PROVIDER", "openai")).strip().lower() or "openai"
        self.deep_model = str(os.getenv("TRADINGAGENTS_DEEP_MODEL", "gpt-5.4")).strip() or "gpt-5.4"
        self.quick_model = str(os.getenv("TRADINGAGENTS_QUICK_MODEL", "gpt-5.4-mini")).strip() or "gpt-5.4-mini"
        self.output_language = str(os.getenv("TRADINGAGENTS_OUTPUT_LANGUAGE", "Chinese")).strip() or "Chinese"
        self.max_debate_rounds = max(1, min(_env_int("TRADINGAGENTS_MAX_DEBATE_ROUNDS", 1), 4))
        self.max_risk_discuss_rounds = max(1, min(_env_int("TRADINGAGENTS_MAX_RISK_DISCUSS_ROUNDS", 1), 4))
        self.checkpoint_enabled = _env_bool("TRADINGAGENTS_CHECKPOINT_ENABLED", default=False)
        self.data_source = str(os.getenv("TRADINGAGENTS_DATA_SOURCE", "auto")).strip().lower() or "auto"
        if self.data_source == "longport":
            self.data_source = "longbridge"
        if self.data_source == "public":
            self.data_source = "local_public"
        raw_public_market_source = str(os.getenv("TRADINGAGENTS_PUBLIC_MARKET_SOURCE", "")).strip().lower()
        self.public_market_source = raw_public_market_source or (
            self.data_source if self.data_source in {"mootdx", "eastmoney", "akshare", "cn_local_cache"} else "auto"
        )
        self.effective_data_source = self._resolve_effective_data_source()
        self.longbridge_api_base = (
            str(os.getenv("TRADINGAGENTS_LONGBRIDGE_API_BASE", "http://127.0.0.1:8010")).strip().rstrip("/")
        )
        self.longbridge_cli_timeout_seconds = max(
            3.0, min(_env_float("TRADINGAGENTS_LONGBRIDGE_CLI_TIMEOUT_SECONDS", 12.0), 60.0)
        )
        self.rate_limit_cooldown_seconds = max(
            15.0,
            min(_env_float("TRADINGAGENTS_RATE_LIMIT_COOLDOWN_SECONDS", 120.0), 1800.0),
        )
        self._rate_limited_until_ts = 0.0
        self._longbridge_patched = False
        self._local_public_patched = False
        raw_analysts = str(os.getenv("TRADINGAGENTS_SELECTED_ANALYSTS", "")).strip()
        allowed = {"market", "social", "news", "fundamentals"}
        parsed = [x.strip().lower() for x in raw_analysts.split(",") if x.strip()]
        self.selected_analysts = [x for x in parsed if x in allowed]
        self._coerce_models_for_tradingagents_multi_turn()

    def _resolve_effective_data_source(self) -> str:
        if self.data_source == "auto":
            return "longbridge" if _longbridge_market_data_ready() else "local_public"
        if self.data_source in {"local_public", "mootdx", "eastmoney", "akshare", "cn_local_cache"}:
            return "local_public"
        return self.data_source

    def _coerce_models_for_tradingagents_multi_turn(self) -> None:
        """
        DeepSeek `deepseek-reasoner`（思考模式）要求多轮请求把上一轮的 reasoning_content 原样带回；
        TradingAgents + LangChain 当前链路未满足，会稳定触发 400。
        默认将含 reasoner/thinking 的 DeepSeek 模型名改为 `deepseek-chat`。
        若你自行修补了上游支持，可设 TRADINGAGENTS_ALLOW_DEEPSEEK_REASONER=true 跳过此降级。
        """
        if self.llm_provider != "deepseek":
            return
        if _env_bool("TRADINGAGENTS_ALLOW_DEEPSEEK_REASONER", default=False):
            return

        def _to_chat(name: str) -> None:
            raw = str(getattr(self, name, "") or "").strip().lower()
            if not raw:
                return
            if "reasoner" in raw or "thinking" in raw:
                setattr(self, name, "deepseek-chat")

        _to_chat("deep_model")
        _to_chat("quick_model")

    @staticmethod
    def _normalize_action(text: str) -> str:
        s = str(text or "").strip().lower()
        if not s:
            return "hold"
        has_buy = any(k in s for k in (" buy", " long", "bullish", "增持", "看多", "买入"))
        has_sell = any(k in s for k in (" sell", " short", "bearish", "减持", "看空", "卖出"))
        has_hold = any(k in s for k in (" hold", "neutral", "观望", "中性", "等待", "wait"))
        if has_buy and not has_sell:
            return "buy"
        if has_sell and not has_buy:
            return "sell"
        if has_hold:
            return "hold"
        return "hold"

    @staticmethod
    def _extract_confidence(text: str) -> float:
        s = str(text or "")
        if not s:
            return 0.5
        m = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%", s)
        if m:
            pct = _safe_float(m.group(1), 50.0)
            return max(0.0, min(pct / 100.0, 1.0))
        m2 = re.search(r"\b(0(?:\.\d+)?|1(?:\.0+)?)\b", s)
        if m2:
            raw = _safe_float(m2.group(1), 0.5)
            return max(0.0, min(raw, 1.0))
        return 0.5

    @staticmethod
    def _to_tradingagents_symbol(symbol: str, market: str) -> str:
        """
        TradingAgents/yfinance 常用无市场后缀美股代码（如 AAPL）。
        我方研究层常用 AAPL.US，这里做轻量归一化，避免外部数据源解析变慢或卡住。
        """
        sym = str(symbol or "").strip().upper()
        mk = str(market or "us").strip().lower()
        if mk == "us":
            if sym.endswith(".US"):
                return sym[:-3]
            if "." in sym:
                return sym.split(".", 1)[0]
        return sym

    @staticmethod
    def _to_longbridge_symbol(symbol: str, market: str) -> str:
        sym = str(symbol or "").strip().upper()
        if not sym:
            return sym
        if "." in sym:
            return sym
        mk = str(market or "us").strip().lower()
        if mk == "us":
            return f"{sym}.US"
        if mk == "hk":
            return f"{sym}.HK"
        if mk == "cn":
            digits = "".join(ch for ch in sym if ch.isdigit())
            if digits:
                norm = digits.zfill(6)
                if norm.startswith(("6", "9")):
                    return f"{norm}.SH"
                return f"{norm}.SZ"
            return f"{sym}.SH"
        return sym

    @staticmethod
    def _infer_market_from_symbol(symbol: str, default_market: str = "us") -> str:
        sym = str(symbol or "").strip().upper()
        if not sym:
            return str(default_market or "us").strip().lower() or "us"
        if "." in sym:
            suffix = sym.rsplit(".", 1)[-1].upper()
            if suffix == "US":
                return "us"
            if suffix == "HK":
                return "hk"
            if suffix in {"SH", "SZ"}:
                return "cn"
        digits = "".join(ch for ch in sym if ch.isdigit())
        if digits:
            if len(digits) <= 5:
                return "hk"
            norm = digits.zfill(6)
            if norm.startswith(("6", "9")):
                return "cn"
            return "cn"
        return str(default_market or "us").strip().lower() or "us"

    @staticmethod
    def _fmt_num(v: Any, digits: int = 4) -> str:
        try:
            n = float(v)
            if math.isfinite(n):
                return f"{n:.{digits}f}"
        except Exception:
            pass
        return "-"

    @staticmethod
    def _parse_iso_date(v: str) -> Optional[date]:
        s = str(v or "").strip()
        if not s:
            return None
        try:
            return date.fromisoformat(s[:10])
        except Exception:
            return None

    def _run_longbridge_cli(self, args: list[str]) -> Optional[str]:
        try:
            cp = subprocess.run(
                ["longbridge", *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=self.longbridge_cli_timeout_seconds,
                check=False,
            )
        except Exception:
            return None
        out = (cp.stdout or "").strip()
        err = (cp.stderr or "").strip()
        if cp.returncode == 0 and out:
            return out
        if cp.returncode == 0 and err:
            return err
        return None

    def _public_history_items(self, symbol: str, market: str, days: int = 180) -> list[dict[str, Any]]:
        sym = self._to_longbridge_symbol(symbol, market)
        ds = max(20, min(int(days), 3650))
        try:
            from api.services.public_market_data_service import get_public_market_data_service

            payload = get_public_market_data_service().klines(
                symbol=sym,
                period="1d",
                days=ds,
                limit=0,
                source=self.public_market_source,
            )
        except Exception:
            return []
        items = payload.get("items") if isinstance(payload, dict) else None
        return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []

    def _public_quote(self, symbol: str, market: str) -> dict[str, Any]:
        sym = self._to_longbridge_symbol(symbol, market)
        try:
            from api.services.public_market_data_service import get_public_market_data_service

            payload = get_public_market_data_service().quote([sym], source=self.public_market_source)
        except Exception:
            return {}
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            return {}
        item = dict(items[0])
        item["symbol"] = sym
        item["available"] = True
        item.setdefault("price_type", item.get("source_label") or item.get("source") or "public_market")
        return item

    def _longbridge_history_items(self, symbol: str, market: str, days: int = 180) -> list[dict[str, Any]]:
        sym = self._to_longbridge_symbol(symbol, market)
        ds = max(20, min(int(days), 3650))
        qs = urllib.parse.urlencode(
            {"symbol": sym, "days": ds, "kline": "1d", "priority": "high"},
            doseq=False,
            safe="",
        )
        url = f"{self.longbridge_api_base}/internal/longport/history-bars?{qs}"
        payload = _http_get_json(url, timeout=min(30.0, max(3.0, self.timeout_seconds)))
        items = payload.get("items") if isinstance(payload, dict) else None
        if isinstance(items, list):
            rows = [x for x in items if isinstance(x, dict)]
            if rows:
                return rows
        return self._public_history_items(symbol=symbol, market=market, days=ds)

    def _longbridge_quote(self, symbol: str, market: str) -> dict[str, Any]:
        sym = self._to_longbridge_symbol(symbol, market)
        qs = urllib.parse.urlencode({"symbol": sym}, doseq=False, safe="")
        url = f"{self.longbridge_api_base}/internal/longport/quote?{qs}"
        payload = _http_get_json(url, timeout=min(10.0, max(2.0, self.timeout_seconds / 4.0)))
        if isinstance(payload, dict) and bool(payload.get("available")):
            return payload
        return self._public_quote(symbol=symbol, market=market)

    def _lb_get_stock_data(self, symbol: str, start_date: str, end_date: str) -> str:
        if self._infer_market_from_symbol(symbol, default_market="us") == "cn":
            return self._local_public_get_stock_data(symbol=symbol, start_date=start_date, end_date=end_date)
        sd = self._parse_iso_date(start_date)
        ed = self._parse_iso_date(end_date)
        if not sd or not ed or ed < sd:
            return f"{symbol} 日期范围无效：{start_date} ~ {end_date}"
        days = max(30, (date.today() - sd).days + 5)
        rows = self._longbridge_history_items(symbol=symbol, market="us", days=days)
        if not rows:
            return f"{symbol} 暂无 Longbridge K 线数据"
        use_rows: list[dict[str, Any]] = []
        for r in rows:
            d = self._parse_iso_date(str(r.get("date") or ""))
            if not d:
                continue
            if sd <= d <= ed:
                use_rows.append(r)
        if not use_rows:
            use_rows = rows[-30:]
        lines = [f"# {symbol} 行情数据（{start_date} ~ {end_date}）", "date,open,high,low,close,volume"]
        for r in use_rows[-120:]:
            lines.append(
                ",".join(
                    [
                        str(r.get("date") or "-"),
                        self._fmt_num(r.get("open"), 4),
                        self._fmt_num(r.get("high"), 4),
                        self._fmt_num(r.get("low"), 4),
                        self._fmt_num(r.get("close"), 4),
                        self._fmt_num(r.get("volume"), 0),
                    ]
                )
            )
        return "\n".join(lines)

    def _lb_get_indicators(self, symbol: str, indicator: str, curr_date: str, look_back_days: int = 30) -> str:
        _ = curr_date
        lb = max(20, min(int(look_back_days), 365))
        rows = self._longbridge_history_items(symbol=symbol, market="us", days=max(120, lb + 30))
        closes = [float(r.get("close", 0.0) or 0.0) for r in rows if _safe_float(r.get("close"), 0.0) > 0.0]
        if len(closes) < 30:
            return f"{symbol} 可用 K 线不足，无法计算指标：{indicator}"

        ind = str(indicator or "").strip().lower()
        last = closes[-1]
        sma20 = sum(closes[-20:]) / 20.0
        ema12 = closes[-1]
        for p in closes[-60:]:
            ema12 = (p * (2.0 / 13.0)) + ema12 * (1.0 - (2.0 / 13.0))
        ema26 = closes[-1]
        for p in closes[-80:]:
            ema26 = (p * (2.0 / 27.0)) + ema26 * (1.0 - (2.0 / 27.0))
        macd = ema12 - ema26
        gains: list[float] = []
        losses: list[float] = []
        for i in range(len(closes) - 15, len(closes) - 1):
            diff = closes[i + 1] - closes[i]
            if diff >= 0:
                gains.append(diff)
                losses.append(0.0)
            else:
                gains.append(0.0)
                losses.append(-diff)
        avg_gain = (sum(gains) / len(gains)) if gains else 0.0
        avg_loss = (sum(losses) / len(losses)) if losses else 0.0
        rsi14 = 100.0 if avg_loss <= 1e-9 else 100.0 - (100.0 / (1.0 + avg_gain / max(avg_loss, 1e-9)))

        if ind in {"rsi", "rsi14"}:
            return f"# {symbol} RSI(14)\nRSI14={self._fmt_num(rsi14, 2)}\nclose={self._fmt_num(last, 4)}"
        if ind in {"macd"}:
            return f"# {symbol} MACD\nMACD={self._fmt_num(macd, 4)}\nEMA12={self._fmt_num(ema12, 4)}\nEMA26={self._fmt_num(ema26, 4)}"
        if ind in {"sma", "sma20", "ma20"}:
            return f"# {symbol} SMA20\nSMA20={self._fmt_num(sma20, 4)}\nclose={self._fmt_num(last, 4)}"
        if ind in {"ema", "ema12"}:
            return f"# {symbol} EMA12\nEMA12={self._fmt_num(ema12, 4)}\nclose={self._fmt_num(last, 4)}"
        return (
            f"Longbridge 适配层暂未显式实现指标：{indicator}。\n"
            f"当前支持的快速指标：rsi/macd/sma/ema。\n"
            f"{symbol} 参考值：close={self._fmt_num(last, 4)}, "
            f"RSI14={self._fmt_num(rsi14, 2)}, MACD={self._fmt_num(macd, 4)}, SMA20={self._fmt_num(sma20, 4)}"
        )

    def _lb_get_fundamentals(self, ticker: str, curr_date: str = "") -> str:
        if self._infer_market_from_symbol(ticker, default_market="us") == "cn":
            return self._local_public_get_fundamentals(ticker=ticker, curr_date=curr_date)
        _ = curr_date
        q = self._longbridge_quote(ticker, "us")
        if q.get("available"):
            return (
                f"# {ticker} 基本面代理数据（Longbridge）\n"
                f"last={self._fmt_num(q.get('last'), 4)}, prev_close={self._fmt_num(q.get('prev_close'), 4)}, "
                f"change_pct={self._fmt_num(q.get('change_pct'), 2)}\n"
                "说明：当前本地 Longbridge 代理未完整暴露三大报表明细。"
            )
        cli = self._run_longbridge_cli(["filing", str(self._to_longbridge_symbol(ticker, "us"))])
        if cli:
            return f"# {ticker} Longbridge 公告/财报\n{cli}"
        return f"{ticker} 暂无可用的 Longbridge 基本面/公告数据"

    def _lb_get_balance_sheet(self, ticker: str, freq: str = "quarterly", curr_date: str = "") -> str:
        if self._infer_market_from_symbol(ticker, default_market="us") == "cn":
            return self._local_public_get_balance_sheet(ticker=ticker, freq=freq, curr_date=curr_date)
        _ = freq
        return self._lb_get_fundamentals(ticker=ticker, curr_date=curr_date)

    def _lb_get_cashflow(self, ticker: str, freq: str = "quarterly", curr_date: str = "") -> str:
        if self._infer_market_from_symbol(ticker, default_market="us") == "cn":
            return self._local_public_get_cashflow(ticker=ticker, freq=freq, curr_date=curr_date)
        _ = freq
        return self._lb_get_fundamentals(ticker=ticker, curr_date=curr_date)

    def _lb_get_income_statement(self, ticker: str, freq: str = "quarterly", curr_date: str = "") -> str:
        if self._infer_market_from_symbol(ticker, default_market="us") == "cn":
            return self._local_public_get_income_statement(ticker=ticker, freq=freq, curr_date=curr_date)
        _ = freq
        return self._lb_get_fundamentals(ticker=ticker, curr_date=curr_date)

    def _lb_get_news(self, ticker: str, start_date: str, end_date: str) -> str:
        mk = self._infer_market_from_symbol(ticker, default_market="us")
        if mk == "cn":
            return self._local_public_get_news(ticker=ticker, start_date=start_date, end_date=end_date)
        req_symbol = str(self._to_longbridge_symbol(ticker, mk))
        out = self._run_longbridge_cli(["news", req_symbol])
        if out:
            return (
                f"# {ticker} 新闻（Longbridge）\n"
                f"- 请求标的：{req_symbol}\n"
                f"- 请求区间：{start_date} ~ {end_date}\n\n"
                f"{out}"
            )
        return (
            f"{ticker} 暂无可用新闻数据（Longbridge）。\n"
            f"请求标的={req_symbol}，请求区间={start_date} ~ {end_date}"
        )

    def _lb_get_global_news(self, curr_date: str, look_back_days: int = 7, limit: int = 5) -> str:
        blocks: list[str] = []
        out = self._run_longbridge_cli(["market-temp"])
        if out:
            blocks.append(f"# 市场温度（Longbridge）\n{out}")

        # Longbridge CLI 当前没有稳定的 global-news 参数，这里用跨市场基准标的新闻做聚合补齐。
        benchmark_symbols = ["SPY.US", "QQQ.US", "2800.HK", "510300.SH"]
        max_items = max(1, min(int(limit or 5), len(benchmark_symbols)))
        for sym in benchmark_symbols[:max_items]:
            news = self._run_longbridge_cli(["news", sym])
            if news:
                blocks.append(f"# 基准标的新闻（{sym}）\n{news}")

        if blocks:
            return (
                f"# 全局新闻代理（日期={curr_date}，回看天数={look_back_days}，数量上限={limit}）\n\n"
                + "\n\n".join(blocks)
            )
        return (
            "暂无可用的 Longbridge 全局新闻代理数据。\n"
            "已尝试：market-temp + 基准标的新闻（SPY.US/QQQ.US/2800.HK/510300.SH）。"
        )

    def _lb_get_insider_transactions(self, ticker: str) -> str:
        if self._infer_market_from_symbol(ticker, default_market="us") == "cn":
            return self._local_public_get_insider_transactions(ticker=ticker)
        out = self._run_longbridge_cli(["insider-trades", str(self._to_longbridge_symbol(ticker, "us"))])
        if out:
            return f"# {ticker} 内幕交易（Longbridge）\n{out}"
        return f"{ticker} 暂无可用内幕交易数据（Longbridge）"

    def _local_public_get_stock_data(self, symbol: str, start_date: str, end_date: str) -> str:
        mk = self._infer_market_from_symbol(symbol, default_market="us")
        if mk == "cn":
            from api.services.a_share_research_data_service import get_a_share_research_data_service

            days = 180
            sd = self._parse_iso_date(start_date)
            if sd:
                days = max(60, (date.today() - sd).days + 10)
            report = get_a_share_research_data_service().build_market_report(
                self._to_longbridge_symbol(symbol, "cn"),
                days=days,
            )
            return self._cn_public_tool_contract("get_stock_data") + "\n\n" + report
        return self._lb_get_stock_data(symbol=symbol, start_date=start_date, end_date=end_date)

    def _local_public_get_indicators(self, symbol: str, indicator: str, curr_date: str, look_back_days: int = 30) -> str:
        return self._lb_get_indicators(symbol=symbol, indicator=indicator, curr_date=curr_date, look_back_days=look_back_days)

    def _local_public_get_fundamentals(self, ticker: str, curr_date: str = "") -> str:
        mk = self._infer_market_from_symbol(ticker, default_market="us")
        if mk == "cn":
            from api.services.a_share_research_data_service import get_a_share_research_data_service

            report = get_a_share_research_data_service().build_fundamentals_report(
                self._to_longbridge_symbol(ticker, "cn"),
                curr_date=curr_date,
            )
            return self._cn_public_tool_contract("get_fundamentals") + "\n\n" + report
        return self._lb_get_fundamentals(ticker=ticker, curr_date=curr_date)

    def _local_public_get_balance_sheet(self, ticker: str, freq: str = "quarterly", curr_date: str = "") -> str:
        mk = self._infer_market_from_symbol(ticker, default_market="us")
        if mk == "cn":
            from api.services.a_share_research_data_service import get_a_share_research_data_service

            return get_a_share_research_data_service().build_statement_report(
                self._to_longbridge_symbol(ticker, "cn"),
                statement="balance",
                freq=freq,
                curr_date=curr_date,
            )
        return self._lb_get_balance_sheet(ticker=ticker, freq=freq, curr_date=curr_date)

    def _local_public_get_cashflow(self, ticker: str, freq: str = "quarterly", curr_date: str = "") -> str:
        mk = self._infer_market_from_symbol(ticker, default_market="us")
        if mk == "cn":
            from api.services.a_share_research_data_service import get_a_share_research_data_service

            return get_a_share_research_data_service().build_statement_report(
                self._to_longbridge_symbol(ticker, "cn"),
                statement="cashflow",
                freq=freq,
                curr_date=curr_date,
            )
        return self._lb_get_cashflow(ticker=ticker, freq=freq, curr_date=curr_date)

    def _local_public_get_income_statement(self, ticker: str, freq: str = "quarterly", curr_date: str = "") -> str:
        mk = self._infer_market_from_symbol(ticker, default_market="us")
        if mk == "cn":
            from api.services.a_share_research_data_service import get_a_share_research_data_service

            return get_a_share_research_data_service().build_statement_report(
                self._to_longbridge_symbol(ticker, "cn"),
                statement="income",
                freq=freq,
                curr_date=curr_date,
            )
        return self._lb_get_income_statement(ticker=ticker, freq=freq, curr_date=curr_date)

    def _local_public_get_news(self, ticker: str, start_date: str, end_date: str) -> str:
        mk = self._infer_market_from_symbol(ticker, default_market="us")
        if mk == "cn":
            from api.services.a_share_research_data_service import get_a_share_research_data_service

            report = get_a_share_research_data_service().build_news_report(
                self._to_longbridge_symbol(ticker, "cn"),
                start_date=start_date,
                end_date=end_date,
            )
            return self._cn_public_tool_contract("get_news") + "\n\n" + report
        return self._lb_get_news(ticker=ticker, start_date=start_date, end_date=end_date)

    def _local_public_get_global_news(self, curr_date: str, look_back_days: int = 7, limit: int = 5) -> str:
        from api.services.a_share_research_data_service import get_a_share_research_data_service

        return get_a_share_research_data_service().build_global_news_report(
            curr_date=curr_date,
            look_back_days=look_back_days,
            limit=limit,
        )

    def _local_public_get_insider_transactions(self, ticker: str) -> str:
        return f"{ticker} A股暂无适用的公开内幕交易数据；可参考公告、股东变动和高管增减持公告。"

    @staticmethod
    def _cn_public_tool_contract(tool_name: str = "") -> str:
        return "\n".join(
            [
                "## A股 local_public 工具使用要求",
                "",
                f"- 当前工具: {tool_name or '-'}",
                "- 这是无券商 API 的公共数据，不等同于交易级实时行情。",
                "- 报告必须显式消费 Fundamental snapshot v2、事件摘要、公司公告和数据源诊断。",
                "- 公告优先级高于普通新闻；数据源诊断里若有缺失或失败，必须降低置信度并说明影响。",
            ]
        ).strip()

    def _lb_fetch_returns(self, ticker: str, trade_date: str, holding_days: int = 5) -> tuple[Optional[float], Optional[float], Optional[int]]:
        try:
            t0 = datetime.strptime(trade_date, "%Y-%m-%d").date()
        except Exception:
            return None, None, None
        today = date.today()
        days = max(30, (today - t0).days + holding_days + 8)
        rows = self._longbridge_history_items(symbol=ticker, market="us", days=days)
        spy_rows = self._longbridge_history_items(symbol="SPY", market="us", days=days)
        if len(rows) < 2 or len(spy_rows) < 2:
            return None, None, None

        def _series(items: list[dict[str, Any]]) -> list[tuple[date, float]]:
            out: list[tuple[date, float]] = []
            for x in items:
                d = self._parse_iso_date(str(x.get("date") or ""))
                c = _safe_float(x.get("close"), float("nan"))
                if d and math.isfinite(c) and c > 0:
                    out.append((d, float(c)))
            out.sort(key=lambda z: z[0])
            return out

        s_stock = _series(rows)
        s_spy = _series(spy_rows)
        if len(s_stock) < 2 or len(s_spy) < 2:
            return None, None, None

        def _pick(series: list[tuple[date, float]]) -> tuple[Optional[float], Optional[float], int]:
            base_idx = -1
            for i, (d, _) in enumerate(series):
                if d >= t0:
                    base_idx = i
                    break
            if base_idx < 0:
                return None, None, 0
            end_idx = min(len(series) - 1, base_idx + max(1, int(holding_days)))
            b = series[base_idx][1]
            e = series[end_idx][1]
            if b <= 0:
                return None, None, 0
            return b, e, max(1, end_idx - base_idx)

        sb, se, sdays = _pick(s_stock)
        pb, pe, pdays = _pick(s_spy)
        if sb is None or pb is None:
            return None, None, None
        raw = (se - sb) / sb
        spy_ret = (pe - pb) / pb
        alpha = raw - spy_ret
        return float(raw), float(alpha), int(min(sdays, pdays))

    def _patch_tradingagents_longbridge_vendor(self) -> None:
        if self._longbridge_patched:
            return
        try:
            from tradingagents.dataflows import interface as ta_interface
            from tradingagents.graph import trading_graph as ta_graph
        except Exception:
            return
        self._longbridge_patched = True

        if "longbridge" not in getattr(ta_interface, "VENDOR_LIST", []):
            try:
                ta_interface.VENDOR_LIST.append("longbridge")
            except Exception:
                pass

        method_map = {
            "get_stock_data": self._lb_get_stock_data,
            "get_indicators": self._lb_get_indicators,
            "get_fundamentals": self._lb_get_fundamentals,
            "get_balance_sheet": self._lb_get_balance_sheet,
            "get_cashflow": self._lb_get_cashflow,
            "get_income_statement": self._lb_get_income_statement,
            "get_news": self._lb_get_news,
            "get_global_news": self._lb_get_global_news,
            "get_insider_transactions": self._lb_get_insider_transactions,
        }
        for method_name, fn in method_map.items():
            if method_name not in ta_interface.VENDOR_METHODS:
                continue
            ta_interface.VENDOR_METHODS[method_name]["longbridge"] = fn

        if not hasattr(ta_graph.TradingAgentsGraph, "_openclaw_longbridge_patched"):
            client = self

            def _patched_fetch_returns(self_graph, ticker: str, trade_date: str, holding_days: int = 5):
                return client._lb_fetch_returns(ticker=ticker, trade_date=trade_date, holding_days=holding_days)

            ta_graph.TradingAgentsGraph._fetch_returns = _patched_fetch_returns
            setattr(ta_graph.TradingAgentsGraph, "_openclaw_longbridge_patched", True)

    def _patch_tradingagents_local_public_vendor(self) -> None:
        if self._local_public_patched:
            return
        try:
            from tradingagents.dataflows import interface as ta_interface
            from tradingagents.graph import trading_graph as ta_graph
        except Exception:
            return
        self._local_public_patched = True

        if "local_public" not in getattr(ta_interface, "VENDOR_LIST", []):
            try:
                ta_interface.VENDOR_LIST.append("local_public")
            except Exception:
                pass

        method_map = {
            "get_stock_data": self._local_public_get_stock_data,
            "get_indicators": self._local_public_get_indicators,
            "get_fundamentals": self._local_public_get_fundamentals,
            "get_balance_sheet": self._local_public_get_balance_sheet,
            "get_cashflow": self._local_public_get_cashflow,
            "get_income_statement": self._local_public_get_income_statement,
            "get_news": self._local_public_get_news,
            "get_global_news": self._local_public_get_global_news,
            "get_insider_transactions": self._local_public_get_insider_transactions,
        }
        for method_name, fn in method_map.items():
            if method_name not in ta_interface.VENDOR_METHODS:
                continue
            ta_interface.VENDOR_METHODS[method_name]["local_public"] = fn

        if not hasattr(ta_graph.TradingAgentsGraph, "_multitrading_local_public_patched"):
            client = self

            def _patched_fetch_returns(self_graph, ticker: str, trade_date: str, holding_days: int = 5):
                return client._lb_fetch_returns(ticker=ticker, trade_date=trade_date, holding_days=holding_days)

            ta_graph.TradingAgentsGraph._fetch_returns = _patched_fetch_returns
            setattr(ta_graph.TradingAgentsGraph, "_multitrading_local_public_patched", True)

    _TA_DEFAULT_ANALYSTS: list[str] = ["market", "social", "news", "fundamentals"]
    _TA_TEMPLATE_TO_ANALYST: dict[str, str] = {
        "mkt": "market",
        "technical": "market",
        "trend": "market",
        "news": "news",
        "sentiment": "social",
        "social": "social",
        "fund": "fundamentals",
        "fundamental": "fundamentals",
    }
    _TA_NON_ANALYST_TAGS: frozenset[str] = frozenset({"risk", "position", "short"})

    @classmethod
    def infer_template_ids_from_question(cls, question: str) -> list[str]:
        """Infer a focused report scope from a free-form chat question."""
        q = str(question or "").strip().lower()
        if not q:
            return []
        q = q.split("【会话上下文", 1)[0].split("[conversation context", 1)[0]
        checks: list[tuple[str, tuple[str, ...]]] = [
            ("news", ("新闻", "消息", "催化", "事件", "公告", "财报", "earnings", "news", "catalyst")),
            ("fund", ("基本面", "估值", "盈利", "营收", "利润", "现金流", "pe", "eps", "revenue", "valuation", "fundamental")),
            ("risk", ("风险", "回撤", "止损", "下跌", "亏损", "risk", "drawdown", "stop loss")),
            ("position", ("仓位", "买入", "卖出", "加仓", "减仓", "持有", "目标价", "入场", "出场", "position", "buy", "sell", "hold", "entry", "exit")),
            (
                "mkt",
                (
                    "今天",
                    "今日",
                    "明天",
                    "短线",
                    "方向",
                    "多空",
                    "看涨",
                    "看跌",
                    "涨还是跌",
                    "偏多",
                    "偏空",
                    "趋势",
                    "走势",
                    "技术",
                    "均线",
                    "支撑",
                    "压力",
                    "突破",
                    "k线",
                    "intraday",
                    "bullish",
                    "bearish",
                    "trend",
                    "direction",
                    "technical",
                    "support",
                    "resistance",
                ),
            ),
            ("sentiment", ("情绪", "社媒", "舆情", "热度", "sentiment", "social")),
            ("short", ("一句话", "简短", "结论", "summary", "brief", "short")),
        ]
        inferred: list[str] = []
        for tag, needles in checks:
            if any(n in q for n in needles) and tag not in inferred:
                inferred.append(tag)
        if inferred:
            return inferred
        if any(x in q for x in ("怎么看", "看法", "能不能", "是否", "为什么", "怎么", "如何", "what", "why", "how")):
            return ["mkt", "news"]
        return ["mkt"]

    @classmethod
    def _effective_analysts_for_templates(
        cls,
        template_ids: Optional[list[str]],
        env_selected: list[str],
    ) -> list[str]:
        """
        根据前端问题标签决定要跑哪些分析师节点；未选标签时沿用 TRADINGAGENTS_SELECTED_ANALYSTS 或默认四分析师。
        仅选风险/仓位/一句话等元标签时，至少跑 market 以驱动后续图。
        """
        raw_ids = [str(x).strip().lower() for x in (template_ids or []) if str(x).strip()]
        if template_ids is None:
            if env_selected:
                return list(env_selected)
            return list(cls._TA_DEFAULT_ANALYSTS)
        if not raw_ids:
            return ["market"]
        want: list[str] = []
        for tid in raw_ids:
            a = cls._TA_TEMPLATE_TO_ANALYST.get(tid)
            if a and a not in want:
                want.append(a)
        only_meta = all(t in cls._TA_NON_ANALYST_TAGS for t in raw_ids)
        if only_meta or not want:
            return ["market"]
        return want

    @staticmethod
    def _report_section_visibility(
        template_ids: Optional[list[str]],
        ran_analysts: list[str],
    ) -> dict[str, Any]:
        """按标签裁剪最终 Markdown；None 表示完整报告（兼容旧 API）。"""
        if template_ids is None:
            return {"mode": "full"}
        ids = {str(x).strip().lower() for x in template_ids if str(x).strip()}
        if not ids:
            return {
                "mode": "selective",
                "analyst_market": "market" in set(ran_analysts),
                "analyst_sentiment": "social" in set(ran_analysts),
                "analyst_news": "news" in set(ran_analysts),
                "analyst_fundamentals": "fundamentals" in set(ran_analysts),
                "research": False,
                "trading": False,
                "risk": False,
                "portfolio": False,
                "short_blurb": False,
            }
        if ids == {"short"}:
            return {"mode": "short_only"}
        ran = set(ran_analysts)
        return {
            "mode": "selective",
            "analyst_market": bool({"mkt", "technical", "trend"} & ids),
            "analyst_sentiment": bool({"sentiment", "social"} & ids) or "social" in ran,
            "analyst_news": "news" in ids,
            "analyst_fundamentals": bool({"fund", "fundamental"} & ids),
            "research": ("risk" in ids or "position" in ids),
            "trading": "position" in ids,
            "risk": "risk" in ids,
            "portfolio": "position" in ids,
            "short_blurb": "short" in ids,
        }

    @staticmethod
    def _is_cn_public_mode(market: str, data_source: str) -> bool:
        return str(market or "").strip().lower() == "cn" and str(data_source or "").strip().lower() == "local_public"

    @classmethod
    def _ensure_cn_public_analysts(cls, analysts: list[str]) -> list[str]:
        """A 股公共源模式下，TradingAgents 至少要消费行情、新闻公告和基本面。"""
        wanted = {str(x).strip().lower() for x in list(analysts or []) if str(x).strip()}
        wanted.update({"market", "news", "fundamentals"})
        ordered = [x for x in TA_ANALYST_ORDER if x in wanted]
        return ordered or ["market", "news", "fundamentals"]

    @staticmethod
    def _ensure_cn_public_template_ids(template_ids: Optional[list[str]]) -> Optional[list[str]]:
        if template_ids is None:
            return None
        out = [str(x).strip().lower() for x in list(template_ids or []) if str(x).strip()]

        def has_any(names: set[str]) -> bool:
            return bool(names & set(out))

        for tag, equivalents in (
            ("mkt", {"mkt", "technical", "trend"}),
            ("news", {"news"}),
            ("fund", {"fund", "fundamental"}),
        ):
            if not has_any(equivalents):
                out.append(tag)
        return out

    @staticmethod
    def _a_share_public_agent_prompt(symbol: str, request_symbol: str, user_question: str = "") -> str:
        question = str(user_question or "").strip() or "请完成 A 股研究分析。"
        return "\n".join(
            [
                f"分析标的：{request_symbol or symbol}（A 股）。",
                "",
                "这是无券商 API / local_public 模式，数据来自公共源。不要声称拥有券商实盘行情或交易权限。",
                "必须先使用工具获取数据，再给出结论：",
                "- get_stock_data：公共日线/K 线与趋势。",
                "- get_fundamentals：必须读取 Fundamental snapshot v2、估值、财报指标、主营构成和数据源诊断。",
                "- get_news：必须读取事件摘要、公司公告、个股新闻、研报和数据源诊断；公告优先级高于普通新闻。",
                "",
                "输出要求：",
                "1. 明确引用 Fundamental snapshot v2 的最新财报期、估值和核心财务指标。",
                "2. 明确区分事件摘要、公司公告、普通新闻和研报，说明哪些是正面、负面或待验证催化。",
                "3. 单独写数据源诊断：列出缺失/失败的数据源，并说明它们对结论置信度的影响。",
                "4. 若公共源数据不足，必须降低置信度，不能用泛泛结论替代证据。",
                "5. 最终仍要给出 BUY/HOLD/SELL、置信度和关键风险。",
                "",
                f"用户关注点：{question[:2000]}",
            ]
        )

    @staticmethod
    def _patch_cn_public_initial_prompt(ta: Any, prompt: str) -> None:
        if not str(prompt or "").strip():
            return
        propagator = getattr(ta, "propagator", None)
        create_initial_state = getattr(propagator, "create_initial_state", None)
        if not callable(create_initial_state):
            return

        def _create_initial_state(company_name: str, trade_date: str, past_context: str = "") -> dict[str, Any]:
            state = create_initial_state(company_name, trade_date, past_context=past_context)
            if isinstance(state, dict):
                state["messages"] = [("human", prompt)]
                state["a_share_public_template"] = "cn_public_tradingagents_v2"
            return state

        setattr(propagator, "create_initial_state", _create_initial_state)

    @staticmethod
    def _merge_report_with_public_context(existing: str, public_report: str, title: str) -> str:
        existing_text = str(existing or "").strip()
        public_text = str(public_report or "").strip()
        if not public_text:
            return existing_text
        marker = f"## {title}"
        if marker in existing_text:
            return existing_text
        if existing_text:
            return f"{existing_text}\n\n---\n\n{marker}\n\n{public_text}".strip()
        return f"{marker}\n\n{public_text}".strip()

    @staticmethod
    def _diag_summary(rows: Any) -> str:
        if not isinstance(rows, list) or not rows:
            return "-"
        parts: list[str] = []
        for row in rows[:8]:
            if not isinstance(row, dict):
                continue
            source = str(row.get("source") or row.get("name") or "-")
            count = row.get("count")
            ok = row.get("ok")
            error = str(row.get("error") or "").strip()
            if error:
                parts.append(f"{source}: error={error[:80]}")
            elif count is not None:
                parts.append(f"{source}: count={count}, ok={ok}")
            else:
                parts.append(f"{source}: ok={ok}")
        return "; ".join(parts) if parts else "-"

    @staticmethod
    def _a_share_public_context_markdown(snapshot: Any) -> str:
        if not isinstance(snapshot, dict):
            return ""
        fs = snapshot.get("fundamental_snapshot_v2") if isinstance(snapshot.get("fundamental_snapshot_v2"), dict) else {}
        diag = snapshot.get("data_diagnostics") if isinstance(snapshot.get("data_diagnostics"), dict) else {}
        latest_period = str(fs.get("latest_period") or "-")
        symbol = str(snapshot.get("symbol") or snapshot.get("request_symbol") or "-")
        lines = [
            "# A股公共数据上下文 v2",
            "",
            "- 模板版本: cn_public_tradingagents_v2",
            "- 已要求 TradingAgents 消费: Fundamental snapshot v2、事件摘要、公司公告、数据源诊断。",
            f"- 标的: {symbol}",
            f"- 最新财报期: {latest_period}",
            f"- 新闻/公告条目数: {diag.get('news_item_count', '-')}",
            f"- 事件摘要条目数: {diag.get('event_item_count', '-')}",
            f"- 是否使用缓存: {bool(diag.get('cache_used'))}",
            f"- 基本面诊断: {TradingAgentsClient._diag_summary(diag.get('fundamentals'))}",
            f"- 新闻公告诊断: {TradingAgentsClient._diag_summary(diag.get('news'))}",
        ]
        return "\n".join(lines).strip() + "\n"

    @classmethod
    def _augment_cn_public_stage_reports(
        cls,
        stage_reports: dict[str, str],
        snapshot: Any,
    ) -> dict[str, str]:
        if not isinstance(snapshot, dict):
            return dict(stage_reports or {})
        out = dict(stage_reports or {})
        public_reports = snapshot.get("stage_reports") if isinstance(snapshot.get("stage_reports"), dict) else {}
        out["analyst_market"] = cls._merge_report_with_public_context(
            out.get("analyst_market", ""),
            str(public_reports.get("analyst_market") or ""),
            "A股公共行情补强",
        )
        out["analyst_news"] = cls._merge_report_with_public_context(
            out.get("analyst_news", ""),
            str(public_reports.get("analyst_news") or ""),
            "A股事件摘要、公告与新闻补强",
        )
        out["analyst_fundamentals"] = cls._merge_report_with_public_context(
            out.get("analyst_fundamentals", ""),
            str(public_reports.get("analyst_fundamentals") or ""),
            "A股 Fundamental snapshot v2 与基本面补强",
        )
        context = cls._a_share_public_context_markdown(snapshot)
        if context:
            out["cn_public_context"] = context
        return out

    def _single_symbol_insight(
        self,
        symbol: str,
        market: str,
        *,
        template_ids: Optional[list[str]] = None,
        user_question: str = "",
        event_callback: Any = None,
    ) -> dict[str, Any]:
        sym = str(symbol or "").strip().upper()
        mk = str(market or "us").strip().lower()
        ta_symbol = self._to_tradingagents_symbol(sym, mk)
        now_ts = time.time()
        if now_ts < float(self._rate_limited_until_ts or 0.0):
            retry_after = max(1, int(self._rate_limited_until_ts - now_ts))
            return {
                "symbol": sym,
                "request_symbol": ta_symbol,
                "market": mk,
                "source": "tradingagents",
                "available": False,
                "reason": "tradingagents_rate_limited_cooldown",
                "retry_after_seconds": retry_after,
            }
        if not self.enabled:
            return {
                "symbol": sym,
                "market": mk,
                "source": "tradingagents",
                "available": False,
                "reason": "tradingagents_disabled",
            }
        if not _llm_api_key_ready(self.llm_provider):
            return {
                "symbol": sym,
                "market": mk,
                "source": "tradingagents",
                "available": False,
                "reason": "tradingagents_llm_key_missing",
                "llm_provider": self.llm_provider,
                "data_source": self.effective_data_source,
            }
        try:
            from tradingagents.default_config import DEFAULT_CONFIG
            from tradingagents.graph.trading_graph import TradingAgentsGraph
        except Exception as e:
            return {
                "symbol": sym,
                "market": mk,
                "source": "tradingagents",
                "available": False,
                "reason": "tradingagents_import_failed",
                "error": str(e),
            }

        _patch_tradingagents_deepseek_thinking_extra_body()

        try:
            cn_public_mode = self._is_cn_public_mode(mk, self.effective_data_source)
            cn_public_snapshot: Optional[dict[str, Any]] = None
            cn_public_agent_prompt = ""
            template_ids_for_report = template_ids

            config = dict(DEFAULT_CONFIG)
            config["llm_provider"] = self.llm_provider
            config["deep_think_llm"] = self.deep_model
            config["quick_think_llm"] = self.quick_model
            config["output_language"] = self.output_language
            config["max_debate_rounds"] = self.max_debate_rounds
            config["max_risk_discuss_rounds"] = self.max_risk_discuss_rounds
            config["checkpoint_enabled"] = bool(self.checkpoint_enabled)
            # 透传到 provider client，帮助外部 LLM 调用在可控时间内失败返回。
            config["timeout"] = float(max(5.0, min(self.timeout_seconds, 600.0)))
            if self.effective_data_source == "longbridge":
                self._patch_tradingagents_longbridge_vendor()
                config["data_vendors"] = {
                    "core_stock_apis": "longbridge,yfinance",
                    "technical_indicators": "longbridge,yfinance",
                    "fundamental_data": "longbridge,yfinance",
                    "news_data": "longbridge,yfinance",
                }
                config["tool_vendors"] = {
                    "get_stock_data": "longbridge,yfinance",
                    "get_indicators": "longbridge,yfinance",
                    "get_fundamentals": "longbridge,yfinance",
                    "get_balance_sheet": "longbridge,yfinance",
                    "get_cashflow": "longbridge,yfinance",
                    "get_income_statement": "longbridge,yfinance",
                    "get_news": "longbridge,yfinance",
                    "get_global_news": "longbridge,yfinance",
                    "get_insider_transactions": "longbridge,yfinance",
                }
            elif self.effective_data_source == "local_public":
                self._patch_tradingagents_local_public_vendor()
                config["data_vendors"] = {
                    "core_stock_apis": "local_public",
                    "technical_indicators": "local_public",
                    "fundamental_data": "local_public",
                    "news_data": "local_public",
                }
                config["tool_vendors"] = {
                    "get_stock_data": "local_public",
                    "get_indicators": "local_public",
                    "get_fundamentals": "local_public",
                    "get_balance_sheet": "local_public",
                    "get_cashflow": "local_public",
                    "get_income_statement": "local_public",
                    "get_news": "local_public",
                    "get_global_news": "local_public",
                    "get_insider_transactions": "local_public",
                }
            elif self.effective_data_source in {"yfinance", "yahoo"}:
                config["data_vendors"] = {
                    "core_stock_apis": "yfinance",
                    "technical_indicators": "yfinance",
                    "fundamental_data": "yfinance",
                    "news_data": "yfinance",
                }
                config["tool_vendors"] = {
                    "get_stock_data": "yfinance",
                    "get_indicators": "yfinance",
                    "get_fundamentals": "yfinance",
                    "get_balance_sheet": "yfinance",
                    "get_cashflow": "yfinance",
                    "get_income_statement": "yfinance",
                    "get_news": "yfinance",
                    "get_global_news": "yfinance",
                    "get_insider_transactions": "yfinance",
                }

            effective_analysts = self._effective_analysts_for_templates(
                template_ids, list(self.selected_analysts or [])
            )
            if cn_public_mode:
                effective_analysts = self._ensure_cn_public_analysts(effective_analysts)
                template_ids_for_report = self._ensure_cn_public_template_ids(template_ids)
                cn_public_agent_prompt = self._a_share_public_agent_prompt(
                    symbol=sym,
                    request_symbol=ta_symbol,
                    user_question=str(user_question or "").strip(),
                )
                try:
                    from api.services.a_share_research_data_service import get_a_share_research_data_service

                    cn_public_snapshot = get_a_share_research_data_service().build_public_research_snapshot(
                        self._to_longbridge_symbol(sym, "cn"),
                        reason="tradingagents_cn_public_template_v2",
                        user_question=str(user_question or "").strip(),
                    )
                except Exception as snapshot_error:
                    cn_public_snapshot = {
                        "symbol": self._to_longbridge_symbol(sym, "cn"),
                        "request_symbol": ta_symbol,
                        "market": "cn",
                        "source": "local_public",
                        "available": False,
                        "reason": "tradingagents_cn_public_snapshot_failed",
                        "data_diagnostics": {
                            "schema": "a_share_research_data.v2",
                            "error": str(snapshot_error),
                        },
                    }
            ta = TradingAgentsGraph(selected_analysts=effective_analysts, debug=False, config=config)
            if cn_public_mode:
                self._patch_cn_public_initial_prompt(ta, cn_public_agent_prompt)
            today = datetime.now().strftime("%Y-%m-%d")
            if event_callback:
                try:
                    final_state, decision = self._stream_tradingagents_run(
                        ta=ta,
                        ta_symbol=ta_symbol,
                        trade_date=today,
                        effective_analysts=list(effective_analysts),
                        event_callback=event_callback,
                    )
                except Exception as stream_error:
                    event_callback(_ta_event("stream_fallback", message=str(stream_error)[:500]))
                    final_state, decision = ta.propagate(ta_symbol, today)
            else:
                final_state, decision = ta.propagate(ta_symbol, today)
            decision_text = str(decision or "").strip()
            action = self._normalize_action(decision_text)
            confidence = self._extract_confidence(decision_text)
            stage_reports = self._extract_stage_reports(final_state)
            if cn_public_mode:
                stage_reports = self._augment_cn_public_stage_reports(stage_reports, cn_public_snapshot)
            research_report_markdown = self._build_research_report_markdown(
                symbol=sym,
                request_symbol=ta_symbol,
                market=mk,
                generated_at=datetime.now().isoformat(),
                action=action,
                confidence=round(float(confidence), 4),
                decision_text=decision_text,
                stage_reports=stage_reports,
                template_ids=template_ids_for_report,
                user_question=str(user_question or "").strip(),
                ran_analysts=list(effective_analysts),
            )

            result = {
                "symbol": sym,
                "request_symbol": ta_symbol,
                "market": mk,
                "source": "tradingagents",
                "available": True,
                "action": action,
                "confidence": round(float(confidence), 4),
                "decision_text": decision_text,
                "stage_reports": stage_reports,
                "research_report_markdown": research_report_markdown,
                "generated_at": datetime.now().isoformat(),
                "selected_template_ids": list(template_ids_for_report) if template_ids_for_report is not None else None,
                "ran_analysts": list(effective_analysts),
            }
            if cn_public_mode and isinstance(cn_public_snapshot, dict):
                result["a_share_template"] = "cn_public_tradingagents_v2"
                result["fundamental_snapshot_v2"] = cn_public_snapshot.get("fundamental_snapshot_v2")
                result["data_diagnostics"] = cn_public_snapshot.get("data_diagnostics")
            return result
        except Exception as e:
            msg = str(e or "")
            low = msg.lower()
            is_rate_limited = (
                "too many requests" in low
                or "rate limit" in low
                or "rate limited" in low
                or "http 429" in low
                or " 429" in low
                or "quota exceeded" in low
            )
            if is_rate_limited:
                self._rate_limited_until_ts = time.time() + float(self.rate_limit_cooldown_seconds)
                return {
                    "symbol": sym,
                    "request_symbol": ta_symbol,
                    "market": mk,
                    "source": "tradingagents",
                    "available": False,
                    "reason": "tradingagents_rate_limited",
                    "retry_after_seconds": int(self.rate_limit_cooldown_seconds),
                    "error": msg,
                }
            return {
                "symbol": sym,
                "request_symbol": ta_symbol,
                "market": mk,
                "source": "tradingagents",
                "available": False,
                "reason": "tradingagents_run_failed",
                "error": msg,
            }

    @staticmethod
    def _as_text(v: Any) -> str:
        if v is None:
            return ""
        if isinstance(v, str):
            return v.strip()
        try:
            return json.dumps(v, ensure_ascii=False, indent=2).strip()
        except Exception:
            return str(v).strip()

    def _stream_tradingagents_run(
        self,
        *,
        ta: Any,
        ta_symbol: str,
        trade_date: str,
        effective_analysts: list[str],
        event_callback: Any,
    ) -> tuple[dict[str, Any], str]:
        event_callback(_ta_event("stream_start", symbol=ta_symbol, trade_date=trade_date))
        for analyst in effective_analysts:
            agent = TA_ANALYST_AGENT_NAMES.get(str(analyst).lower(), str(analyst))
            event_callback(_ta_event("agent_status", team="Analyst Team", agent=agent, status="pending"))
        for team, agents in {
            "Research Team": ["Bull Researcher", "Bear Researcher", "Research Manager"],
            "Trading Team": ["Trader"],
            "Risk Management": ["Aggressive Analyst", "Neutral Analyst", "Conservative Analyst"],
            "Portfolio Management": ["Portfolio Manager"],
        }.items():
            for agent in agents:
                event_callback(_ta_event("agent_status", team=team, agent=agent, status="pending"))

        init_state = ta.propagator.create_initial_state(ta_symbol, trade_date)
        args = ta.propagator.get_graph_args()
        trace: list[dict[str, Any]] = []
        completed_reports: set[str] = set()
        processed_messages: set[str] = set()

        for chunk in ta.graph.stream(init_state, **args):
            if not isinstance(chunk, dict):
                continue
            trace.append(chunk)

            for message in list(chunk.get("messages") or []):
                msg_id = str(getattr(message, "id", "") or "")
                if msg_id and msg_id in processed_messages:
                    continue
                if msg_id:
                    processed_messages.add(msg_id)
                content = _ta_message_content(message)
                if content:
                    event_callback(
                        _ta_event(
                            "message",
                            message_type=_ta_message_type(message),
                            content=content[:1200],
                        )
                    )
                for tool in _ta_tool_call_items(message):
                    event_callback(_ta_event("tool_call", name=tool.get("name"), args=tool.get("args")))

            active_found = False
            for analyst in TA_ANALYST_ORDER:
                if analyst not in effective_analysts:
                    continue
                report_key = TA_ANALYST_REPORT_MAP[analyst]
                agent = TA_ANALYST_AGENT_NAMES[analyst]
                report = self._as_text(chunk.get(report_key))
                if report:
                    completed_reports.add(report_key)
                    event_callback(_ta_event("report_section", section=report_key, agent=agent, content=report[:2400]))
                    event_callback(_ta_event("agent_status", team="Analyst Team", agent=agent, status="completed"))
                elif report_key in completed_reports:
                    event_callback(_ta_event("agent_status", team="Analyst Team", agent=agent, status="completed"))
                elif not active_found:
                    event_callback(_ta_event("agent_status", team="Analyst Team", agent=agent, status="in_progress"))
                    active_found = True

            debate = chunk.get("investment_debate_state") if isinstance(chunk.get("investment_debate_state"), dict) else {}
            if debate:
                bull = self._as_text(debate.get("bull_history"))
                bear = self._as_text(debate.get("bear_history"))
                judge = self._as_text(debate.get("judge_decision"))
                if bull or bear:
                    for agent in ["Bull Researcher", "Bear Researcher", "Research Manager"]:
                        event_callback(_ta_event("agent_status", team="Research Team", agent=agent, status="in_progress"))
                if bull:
                    event_callback(_ta_event("report_section", section="research_bull", agent="Bull Researcher", content=bull[:2400]))
                if bear:
                    event_callback(_ta_event("report_section", section="research_bear", agent="Bear Researcher", content=bear[:2400]))
                if judge:
                    event_callback(_ta_event("report_section", section="research_manager", agent="Research Manager", content=judge[:2400]))
                    for agent in ["Bull Researcher", "Bear Researcher", "Research Manager"]:
                        event_callback(_ta_event("agent_status", team="Research Team", agent=agent, status="completed"))
                    event_callback(_ta_event("agent_status", team="Trading Team", agent="Trader", status="in_progress"))

            trader_plan = self._as_text(chunk.get("trader_investment_plan"))
            if trader_plan:
                event_callback(_ta_event("report_section", section="trading_trader", agent="Trader", content=trader_plan[:2400]))
                event_callback(_ta_event("agent_status", team="Trading Team", agent="Trader", status="completed"))
                event_callback(_ta_event("agent_status", team="Risk Management", agent="Aggressive Analyst", status="in_progress"))

            risk = chunk.get("risk_debate_state") if isinstance(chunk.get("risk_debate_state"), dict) else {}
            if risk:
                for key, agent in [
                    ("aggressive_history", "Aggressive Analyst"),
                    ("conservative_history", "Conservative Analyst"),
                    ("neutral_history", "Neutral Analyst"),
                ]:
                    content = self._as_text(risk.get(key))
                    if content:
                        event_callback(_ta_event("agent_status", team="Risk Management", agent=agent, status="in_progress"))
                        event_callback(_ta_event("report_section", section=f"risk_{key}", agent=agent, content=content[:2400]))
                judge = self._as_text(risk.get("judge_decision"))
                if judge:
                    event_callback(_ta_event("agent_status", team="Portfolio Management", agent="Portfolio Manager", status="in_progress"))
                    event_callback(_ta_event("report_section", section="portfolio_decision", agent="Portfolio Manager", content=judge[:2400]))
                    for agent in ["Aggressive Analyst", "Conservative Analyst", "Neutral Analyst"]:
                        event_callback(_ta_event("agent_status", team="Risk Management", agent=agent, status="completed"))
                    event_callback(_ta_event("agent_status", team="Portfolio Management", agent="Portfolio Manager", status="completed"))

        if not trace:
            raise RuntimeError("tradingagents_stream_empty")
        final_state = trace[-1]
        decision = ta.process_signal(final_state.get("final_trade_decision", ""))
        event_callback(_ta_event("stream_done", symbol=ta_symbol))
        return final_state, str(decision or "")

    @staticmethod
    def _extract_stage_reports(final_state: Any) -> dict[str, str]:
        if not isinstance(final_state, dict):
            return {}
        out: dict[str, str] = {}
        mapping = {
            "analyst_market": final_state.get("market_report"),
            "analyst_sentiment": final_state.get("sentiment_report"),
            "analyst_news": final_state.get("news_report"),
            "analyst_fundamentals": final_state.get("fundamentals_report"),
            "research_bull": (final_state.get("investment_debate_state") or {}).get("bull_history"),
            "research_bear": (final_state.get("investment_debate_state") or {}).get("bear_history"),
            "research_manager": (final_state.get("investment_debate_state") or {}).get("judge_decision"),
            "trading_trader": final_state.get("trader_investment_plan"),
            "risk_aggressive": (final_state.get("risk_debate_state") or {}).get("aggressive_history"),
            "risk_conservative": (final_state.get("risk_debate_state") or {}).get("conservative_history"),
            "risk_neutral": (final_state.get("risk_debate_state") or {}).get("neutral_history"),
            "portfolio_decision": final_state.get("final_trade_decision"),
            "portfolio_plan": final_state.get("investment_plan"),
        }
        for k, v in mapping.items():
            s = TradingAgentsClient._as_text(v)
            if s:
                out[k] = s
        return out

    @staticmethod
    def _build_research_report_markdown(
        symbol: str,
        request_symbol: str,
        market: str,
        generated_at: str,
        action: str,
        confidence: float,
        decision_text: str,
        stage_reports: dict[str, str],
        template_ids: Optional[list[str]] = None,
        user_question: str = "",
        ran_analysts: Optional[list[str]] = None,
    ) -> str:
        def sec(title: str, key: str) -> str:
            txt = str(stage_reports.get(key) or "").strip()
            body = txt if txt else "（该阶段无内容或未返回）"
            return f"## {title}\n\n{body}\n"

        ran = list(ran_analysts or [])
        vis = TradingAgentsClient._report_section_visibility(template_ids, ran)
        uq = str(user_question or "").strip()
        user_block = f"## 用户关注点\n\n{uq[:4000]}\n\n---\n\n" if uq else ""
        cn_public_context = str(stage_reports.get("cn_public_context") or "").strip()

        meta_lines = [
            f"- Symbol: {symbol}",
            f"- Request Symbol: {request_symbol}",
            f"- Market: {market}",
            f"- Generated At: {generated_at}",
            f"- Action: {str(action or '').upper() or '-'}",
            f"- Confidence: {confidence:.4f}",
        ]
        if ran:
            meta_lines.append(f"- Ran Analysts: {', '.join(ran)}")

        if vis.get("mode") == "short_only":
            core = f"# TradingAgents 结论\n\n{str(decision_text or '（无最终结论文本）').strip()}\n"
            return (user_block + core).strip() + "\n"

        if vis.get("mode") == "full":
            parts: list[str] = [
                "# TradingAgents 完整研究过程报告",
                "",
            ]
            if user_block:
                parts.append(user_block.rstrip())
                parts.append("")
            parts.extend(meta_lines)
            if cn_public_context:
                parts.extend(["", cn_public_context])
            parts.extend(
                [
                    "",
                    "## 最终结论",
                    "",
                    str(decision_text or "（无最终结论文本）"),
                    "",
                    "# 1_analysts",
                    "",
                    sec("market", "analyst_market"),
                    sec("sentiment", "analyst_sentiment"),
                    sec("news", "analyst_news"),
                    sec("fundamentals", "analyst_fundamentals"),
                    "# 2_research",
                    "",
                    sec("bull", "research_bull"),
                    sec("bear", "research_bear"),
                    sec("manager", "research_manager"),
                    "# 3_trading",
                    "",
                    sec("trader", "trading_trader"),
                    "# 4_risk",
                    "",
                    sec("aggressive", "risk_aggressive"),
                    sec("conservative", "risk_conservative"),
                    sec("neutral", "risk_neutral"),
                    "# 5_portfolio",
                    "",
                    sec("decision", "portfolio_decision"),
                    sec("investment_plan", "portfolio_plan"),
                ]
            )
            return "\n".join(parts).strip() + "\n"

        # selective
        v = vis
        parts2: list[str] = [
            "# TradingAgents 报告（按选中侧重点节选）",
            "",
        ]
        if user_block:
            parts2.append(user_block.rstrip())
            parts2.append("")
        parts2.extend(meta_lines)
        if cn_public_context:
            parts2.extend(["", cn_public_context])
        parts2.extend(
            [
                "",
                "## 最终结论",
                "",
                str(decision_text or "（无最终结论文本）"),
                "",
            ]
        )
        any_analyst = (
            v.get("analyst_market")
            or v.get("analyst_sentiment")
            or v.get("analyst_news")
            or v.get("analyst_fundamentals")
        )
        if any_analyst:
            parts2.extend(["# 1_分析师摘要", ""])
            if v.get("analyst_market"):
                parts2.append(sec("market", "analyst_market"))
            if v.get("analyst_sentiment"):
                parts2.append(sec("sentiment", "analyst_sentiment"))
            if v.get("analyst_news"):
                parts2.append(sec("news", "analyst_news"))
            if v.get("analyst_fundamentals"):
                parts2.append(sec("fundamentals", "analyst_fundamentals"))

        if v.get("research"):
            parts2.extend(["# 2_多空与研究经理", "", sec("bull", "research_bull"), sec("bear", "research_bear"), sec("manager", "research_manager")])
        if v.get("trading"):
            parts2.extend(["# 3_交易计划", "", sec("trader", "trading_trader")])
        if v.get("risk"):
            parts2.extend(
                [
                    "# 4_风险辩论",
                    "",
                    sec("aggressive", "risk_aggressive"),
                    sec("conservative", "risk_conservative"),
                    sec("neutral", "risk_neutral"),
                ]
            )
        if v.get("portfolio"):
            parts2.extend(["# 5_组合决策", "", sec("decision", "portfolio_decision"), sec("investment_plan", "portfolio_plan")])

        if v.get("short_blurb"):
            parts2.extend(["# 一句话结论（标签）", "", str(decision_text or "（无）").strip(), ""])

        return "\n".join(parts2).strip() + "\n"

    def insights(
        self,
        symbols: list[str],
        market: str,
        kline: str,
        limit: int = 8,
        *,
        template_ids: Optional[list[str]] = None,
        user_question: str = "",
        event_callback: Any = None,
    ) -> list[dict[str, Any]]:
        _ = kline
        selected = [str(s).strip().upper() for s in list(symbols or []) if str(s).strip()]
        if not selected:
            return []
        cap = max(1, min(int(limit), self.max_symbols))
        out: list[dict[str, Any]] = []
        for idx, sym in enumerate(selected[:cap]):
            pool = ThreadPoolExecutor(max_workers=1)
            fut = pool.submit(
                self._single_symbol_insight,
                sym,
                market,
                template_ids=template_ids,
                user_question=user_question,
                event_callback=event_callback if idx == 0 else None,
            )
            try:
                res = fut.result(timeout=self.timeout_seconds)
                out.append(res)
                pool.shutdown(wait=False, cancel_futures=True)
                if str(res.get("reason") or "") in {
                    "tradingagents_rate_limited",
                    "tradingagents_rate_limited_cooldown",
                }:
                    remaining = selected[idx + 1 : cap]
                    retry_after = int(res.get("retry_after_seconds") or self.rate_limit_cooldown_seconds)
                    for rem in remaining:
                        out.append(
                            {
                                "symbol": rem,
                                "request_symbol": self._to_tradingagents_symbol(rem, market),
                                "market": str(market or "us").strip().lower(),
                                "source": "tradingagents",
                                "available": False,
                                "reason": "tradingagents_rate_limited_cooldown",
                                "retry_after_seconds": retry_after,
                            }
                        )
                    break
            except FuturesTimeoutError:
                # 不能 wait=True，否则会被阻塞任务反向拖住整个 research 流程。
                pool.shutdown(wait=False, cancel_futures=True)
                pool.shutdown(wait=False, cancel_futures=True)
                mk = str(market or "us").strip().lower()
                if mk == "cn":
                    from api.services.a_share_research_data_service import get_a_share_research_data_service

                    fallback = get_a_share_research_data_service().build_public_research_snapshot(
                        self._to_longbridge_symbol(sym, "cn"),
                        reason="tradingagents_timeout",
                        user_question=user_question,
                    )
                    fallback["timeout_seconds"] = float(self.timeout_seconds)
                    out.append(fallback)
                else:
                    out.append(
                        {
                            "symbol": sym,
                            "market": mk,
                            "source": "tradingagents",
                            "available": False,
                            "reason": "tradingagents_timeout",
                            "timeout_seconds": float(self.timeout_seconds),
                        }
                    )
            except Exception as e:
                pool.shutdown(wait=False, cancel_futures=True)
                out.append(
                    {
                        "symbol": sym,
                        "market": str(market or "us").strip().lower(),
                        "source": "tradingagents",
                        "available": False,
                        "reason": "tradingagents_executor_error",
                        "error": str(e),
                    }
                )
        return out

    def status(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.enabled),
            "max_symbols": int(self.max_symbols),
            "llm_provider": self.llm_provider,
            "deep_model": self.deep_model,
            "quick_model": self.quick_model,
            "data_source": self.data_source,
            "effective_data_source": self.effective_data_source,
            "longbridge_market_data_ready": _longbridge_market_data_ready(),
            "llm_key_ready": _llm_api_key_ready(self.llm_provider),
        }


class ResearchProviderRouter:
    """
    阶段3：统一研究数据层路由。
    当前主数据源为 LongPort；OpenBB 作为外部增强，不进入下单关键路径。
    """

    def __init__(self, primary: ResearchProvider) -> None:
        self.primary = primary
        self.openbb = OpenBBClient()
        self.tradingagents = TradingAgentsClient()

    def strong_stocks(self, market: str, top_n: int, kline: str) -> list[dict[str, Any]]:
        return self.primary.get_strong_stocks(market=market, top_n=top_n, kline=kline)

    def score_symbol(
        self,
        symbol: str,
        strategies: list[str],
        backtest_days: int,
        kline: str,
        strategy_params_map: Optional[dict[str, dict[str, Any]]] = None,
    ) -> list[dict[str, Any]]:
        return self.primary.score_symbol(
            symbol=symbol,
            strategies=strategies,
            backtest_days=backtest_days,
            kline=kline,
            strategy_params_map=strategy_params_map if isinstance(strategy_params_map, dict) else None,
        )

    def pair_backtest(self, market: str, backtest_days: int, kline: str) -> dict[str, Any]:
        return self.primary.run_pair_backtest(market=market, backtest_days=backtest_days, kline=kline)

    def external_market_regime(self, market: str) -> dict[str, Any]:
        return self.openbb.market_regime(market=market)

    def external_symbol_factors(self, symbols: list[str], market: str, kline: str, limit: int = 8) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for sym in list(symbols or [])[: max(1, int(limit))]:
            out.append(self.openbb.symbol_factor(symbol=sym, market=market, kline=kline))
        return out

    def external_tradingagents_insights(
        self,
        symbols: list[str],
        market: str,
        kline: str,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        return self.tradingagents.insights(symbols=symbols, market=market, kline=kline, limit=limit)

    def provider_status(self) -> dict[str, Any]:
        hb = self.openbb.ensure_available()
        ta = self.tradingagents.status()
        return {
            "primary": "longport",
            "openbb_enabled": bool(self.openbb.enabled),
            "openbb_connected": bool(hb.get("ok")),
            "openbb_base_url": self.openbb.base_url if self.openbb.enabled else "",
            "tradingagents_enabled": bool(ta.get("enabled")),
            "tradingagents_provider": ta.get("llm_provider"),
            "tradingagents_max_symbols": ta.get("max_symbols"),
            "tradingagents_data_source": ta.get("data_source"),
            "tradingagents_effective_data_source": ta.get("effective_data_source"),
            "tradingagents_llm_key_ready": bool(ta.get("llm_key_ready")),
            "tradingagents_longbridge_market_data_ready": bool(ta.get("longbridge_market_data_ready")),
        }
