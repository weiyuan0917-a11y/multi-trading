#!/usr/bin/env python3
"""模拟 Supervisor 未合并 user_env、仅继承空/最小环境时的 Worker 拉 K 与强势股筛选。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run_case(label: str, env: dict[str, str]) -> None:
    root_lit = json.dumps(str(ROOT), ensure_ascii=False)
    env_lit = json.dumps(env, ensure_ascii=False)
    code = f"""
import json, os, sys
from pathlib import Path
ROOT = Path({root_lit})
sys.path.insert(0, str(ROOT))
# 清空常见键，模拟未合并 user_env 的 Worker
for k in list(os.environ.keys()):
  if k.startswith("AUTO_TRADER_") or k.startswith("LONGPORT_"):
    os.environ.pop(k, None)
for k, v in {env_lit}.items():
  os.environ[k] = str(v)
from api.auto_trader_worker import _api_base_url, _fetch_bars, _use_api_proxy
from api.auto_trader import AutoTraderService
from api.auto_trader_worker import _get_positions, _get_account, _quote_last, make_feishu_sender
cfg = json.load(open(ROOT / "api" / "auto_trader_config.json", encoding="utf-8"))
market = str(cfg.get("market", "us"))
kline = str(cfg.get("kline", "1d"))
top_n = int(cfg.get("top_n", 8))
trader = AutoTraderService(
    fetch_bars=lambda s, d, k: _fetch_bars(s, d, k),
    quote_last=_quote_last,
    send_feishu=make_feishu_sender(ROOT / "mcp_server" / "notification_config.json"),
    execute_trade=lambda *a, **k: {{}},
    get_positions=_get_positions,
    get_account=_get_account,
    config_path=str(ROOT / "api" / "auto_trader_config.json"),
)
rows = trader.screen_strong_stocks(market, top_n, kline)
print(json.dumps({{
  "label": {json.dumps(label, ensure_ascii=False)},
  "module_API_BASE": _api_base_url(),
  "module_USE_PROXY": _use_api_proxy(),
  "AMD_bars": len(_fetch_bars("AMD.US", 60, kline)),
  "strong_len": len(rows),
  "top": [r.get("symbol") for r in rows[:3]],
}}, ensure_ascii=False))
"""
    r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True, timeout=300)
    print((r.stdout or r.stderr)[-2000:])


def main() -> int:
    print("=== bare worker env simulation ===")
    run_case("默认（无 AUTO_TRADER_* 环境变量）", {})
    run_case("旧默认基址 8000", {"AUTO_TRADER_API_BASE_URL": "http://127.0.0.1:8000"})
    run_case("正确基址 8010", {"AUTO_TRADER_API_BASE_URL": "http://127.0.0.1:8010"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
