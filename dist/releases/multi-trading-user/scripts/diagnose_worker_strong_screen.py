#!/usr/bin/env python3
"""复现 Worker 内 screen_strong_stocks：对比代理基址与 kline 配置。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _child(extra_env: dict[str, str]) -> dict:
    root_lit = json.dumps(str(ROOT), ensure_ascii=False)
    extra_lit = json.dumps(extra_env, ensure_ascii=False)
    code = f"""
import json, os, sys
from pathlib import Path
ROOT = Path({root_lit})
sys.path.insert(0, str(ROOT))
from config.user_env_store import combined_env_for_cli
for k, v in combined_env_for_cli(ROOT).items():
    os.environ[k] = str(v)
for k, v in {extra_lit}.items():
    os.environ[k] = str(v)
from api.auto_trader_worker import _fetch_bars
from api.auto_trader import AutoTraderService
from api.auto_trader_worker import _get_positions, _get_account, _quote_last, make_feishu_sender
cfg = json.load(open(os.path.join(ROOT, "api", "auto_trader_config.json"), encoding="utf-8"))
market = str(cfg.get("market", "us"))
kline = str(cfg.get("kline", "1d"))
top_n = int(cfg.get("top_n", 8))
sym = "AMD.US"
trader = AutoTraderService(
    fetch_bars=lambda s, d, k: _fetch_bars(s, d, k),
    quote_last=_quote_last,
    send_feishu=make_feishu_sender(os.path.join(ROOT, "mcp_server", "notification_config.json")),
    execute_trade=lambda *a, **k: {{}},
    get_positions=_get_positions,
    get_account=_get_account,
    config_path=os.path.join(ROOT, "api", "auto_trader_config.json"),
)
rows = trader.screen_strong_stocks(market, top_n, kline)
out = {{
    "API_BASE": os.getenv("AUTO_TRADER_API_BASE_URL", ""),
    "USE_API_PROXY": os.getenv("AUTO_TRADER_WORKER_USE_API_PROXY", ""),
    "bars_1d": len(_fetch_bars(sym, 60, "1d")),
    "bars_kline": len(_fetch_bars(sym, 60, kline)),
    "universe_us_len": len(trader._get_universe(market)),
    "strong_len": len(rows),
    "top_symbols": [r.get("symbol") for r in rows[:5]],
    "config_kline": kline,
}}
print(json.dumps(out, ensure_ascii=False))
"""
    r = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    if r.returncode != 0:
        return {"error": r.stderr[-1500:] if r.stderr else "nonzero", "stdout": r.stdout}
    line = (r.stdout or "").strip().splitlines()[-1]
    return json.loads(line)


def main() -> int:
    cfg = json.loads((ROOT / "api" / "auto_trader_config.json").read_text(encoding="utf-8"))
    print("=== diagnose_worker_strong_screen ===")
    print("config market=", cfg.get("market"), "kline=", cfg.get("kline"), "top_n=", cfg.get("top_n"))

    scenarios = [
        ("combined_env（Supervisor 合并后）", {}),
        ("错误基址 :8000", {"AUTO_TRADER_API_BASE_URL": "http://127.0.0.1:8000"}),
        ("无代理直连", {"AUTO_TRADER_WORKER_USE_API_PROXY": "false"}),
    ]
    for label, extra in scenarios:
        print(f"\n--- {label} ---")
        try:
            print(json.dumps(_child(extra), ensure_ascii=False, indent=2))
        except Exception as e:
            print("failed:", e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
