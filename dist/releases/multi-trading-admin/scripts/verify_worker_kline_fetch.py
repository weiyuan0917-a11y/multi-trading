#!/usr/bin/env python3
"""
验证 Auto Trader Worker 拉 K 是否与配置一致。

Worker 默认通过 HTTP 访问 API 的 /internal/longport/history-bars（见 auto_trader_worker._fetch_bars）。
本脚本会读取与 Supervisor 相同的 combined_env（davies + 根 .env），并依次探测常见端口。

用法（在 multi-trading 目录下）:
  python scripts/verify_worker_kline_fetch.py
  python scripts/verify_worker_kline_fetch.py --symbol MU.US --days 60
  python scripts/verify_worker_kline_fetch.py --try-direct
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _combined_api_base() -> str | None:
    try:
        from config.user_env_store import combined_env_for_cli

        u = str(combined_env_for_cli(ROOT).get("AUTO_TRADER_API_BASE_URL") or "").strip()
        return u.rstrip("/") if u else None
    except Exception:
        return None


def _proxy_probe(base: str, symbol: str, days: int, kline: str, timeout: float) -> dict:
    q = urllib.parse.urlencode(
        {"symbol": symbol, "days": int(days), "kline": str(kline), "priority": "high"}
    )
    url = f"{base.rstrip('/')}/internal/longport/history-bars?{q}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
            data = json.loads(raw) if raw else {}
            if not isinstance(data, dict):
                return {"ok": False, "url": url, "error": "invalid_json", "raw_head": raw[:200]}
            items = data.get("items")
            n = len(items) if isinstance(items, list) else 0
            return {
                "ok": True,
                "url": url,
                "http_status": getattr(resp, "status", 200),
                "count": int(data.get("count", n) or 0),
                "available": bool(data.get("available", n > 0)),
            }
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")[:500]
        return {"ok": False, "url": url, "error": f"http_{e.code}", "body": body}
    except Exception as e:
        return {"ok": False, "url": url, "error": type(e).__name__, "detail": str(e)}


def _probe_bases(
    bases: list[str], symbol: str, days: int, kline: str, timeout: float
) -> tuple[dict | None, list[dict]]:
    """按顺序探测，返回 (首个成功且 count>=21 的结果, 全部原始结果)"""
    raw: list[dict] = []
    best: dict | None = None
    for b in bases:
        b = b.strip().rstrip("/")
        if not b or b in {x.get("base") for x in raw if isinstance(x, dict)}:
            continue
        r = _proxy_probe(b, symbol, days, kline, timeout)
        r["base"] = b
        raw.append(r)
        if r.get("ok") and int(r.get("count", 0) or 0) >= 21:
            return r, raw
        if r.get("ok") and best is None:
            best = r
    return best, raw


def _direct_subprocess(symbol: str, days: int, kline: str) -> int:
    root_lit = json.dumps(str(ROOT), ensure_ascii=False)
    code = (
        "import os, sys\n"
        f"ROOT = {root_lit}\n"
        "if ROOT not in sys.path:\n"
        "    sys.path.insert(0, ROOT)\n"
        "try:\n"
        "    from config.user_env_store import bootstrap_process_env_from_davies\n"
        "    bootstrap_process_env_from_davies()\n"
        "except Exception:\n"
        "    pass\n"
        "os.environ['AUTO_TRADER_WORKER_USE_API_PROXY'] = 'false'\n"
        "from api.auto_trader_worker import _fetch_bars\n"
        f"bars = _fetch_bars({symbol!r}, {int(days)}, {kline!r})\n"
        "print(len(bars))\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    print("--- 直连子进程 (AUTO_TRADER_WORKER_USE_API_PROXY=false，已 bootstrap davies 密钥) ---")
    print("exit_code:", r.returncode)
    if out:
        print("stdout:", out)
    if err:
        print("stderr:", err[:2000])
    try:
        return int(out.splitlines()[-1]) if out else -1
    except ValueError:
        return -1


def main() -> int:
    p = argparse.ArgumentParser(description="Verify Worker K-line fetch path")
    p.add_argument("--symbol", default="AMD.US", help="测试代码")
    p.add_argument("--days", type=int, default=60, help="日历天数（与强势股扫描接近）")
    p.add_argument("--kline", default="1d")
    p.add_argument("--timeout", type=float, default=25.0)
    p.add_argument("--try-direct", action="store_true", help="额外试 LongPort 直连（子进程）")
    args = p.parse_args()

    combined_base = _combined_api_base()
    env_base = str(os.getenv("AUTO_TRADER_API_BASE_URL", "")).strip().rstrip("/")
    use_proxy = str(os.getenv("AUTO_TRADER_WORKER_USE_API_PROXY", "true")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    direct_fb = str(os.getenv("LONGPORT_DIRECT_FALLBACK", "0")).strip().lower() in {"1", "true", "yes", "on"}

    probe_order: list[str] = []
    for b in (env_base, combined_base, "http://127.0.0.1:8010", "http://127.0.0.1:8000"):
        if b and b not in probe_order:
            probe_order.append(b)

    print("=== Worker K 线拉取自检 ===")
    print(f"进程环境 AUTO_TRADER_API_BASE_URL={env_base or '(未设置)'}")
    print(f"combined_env(davies) AUTO_TRADER_API_BASE_URL={combined_base or '(无)'}")
    print(f"AUTO_TRADER_WORKER_USE_API_PROXY={use_proxy}")
    print(f"LONGPORT_DIRECT_FALLBACK={direct_fb}")
    print(f"symbol={args.symbol} days={args.days} kline={args.kline}")
    print(f"探测顺序: {probe_order}")
    print()

    print("--- HTTP 代理路径（与 Worker 一致，多基址探测）---")
    winner, all_results = _probe_bases(probe_order, args.symbol.upper(), args.days, args.kline, args.timeout)
    for r in all_results:
        print(json.dumps({k: v for k, v in r.items() if k != "url"}, ensure_ascii=False))
        print("  ", r.get("url", ""))

    if winner and int(winner.get("count", 0) or 0) >= 21:
        print(
            f"\n结论: 代理可用（{winner.get('base')}），K 根数 {winner.get('count')} >= 21。"
            " 请保证 Worker 进程使用的 AUTO_TRADER_API_BASE_URL 与此一致（Supervisor 已合并 combined_env）。"
        )
        rc = 0
    elif winner:
        print(
            f"\n结论: 可达但 K 不足 21（count={winner.get('count')}）。"
            " 检查行情窗口或标的代码。"
        )
        rc = 2
    else:
        print(
            "\n结论: 所列基址均失败。请启动后端（默认端口 8010），"
            "并检查 data/user_env/davies.env 中 AUTO_TRADER_API_BASE_URL；"
            "或设置 LONGPORT_DIRECT_FALLBACK=1 / AUTO_TRADER_WORKER_USE_API_PROXY=false。"
        )
        rc = 1

    if env_base and combined_base and env_base != combined_base:
        print(
            f"\n提示: 当前 shell 的 AUTO_TRADER_API_BASE_URL（{env_base}）"
            f"与 combined_env（{combined_base}）不一致；Worker 经 Supervisor 启动后以文件为准。"
        )

    if args.try_direct:
        n = _direct_subprocess(args.symbol.upper(), args.days, args.kline)
        if n >= 21:
            print(f"\n直连成功: bar_count={n}")
        elif n >= 0:
            print(f"\n直连返回 bar_count={n}（<21 则筛不出强势股）")
        else:
            print("\n直连未能解析 bar 数量，请查看子进程 stderr。")

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
