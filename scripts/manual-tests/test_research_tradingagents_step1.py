import json
import time
import urllib.request
from typing import Any, Tuple


BASE = "http://127.0.0.1:8010"


def _http_get(path: str, timeout: float = 10.0) -> Tuple[int, Any]:
    try:
        req = urllib.request.Request(f"{BASE}{path}", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
            return int(resp.status), json.loads(raw) if raw else {}
    except Exception as e:
        return -1, {"error": str(e), "path": path}


def _http_post(path: str, body: dict[str, Any], timeout: float = 20.0) -> Tuple[int, Any]:
    try:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(f"{BASE}{path}", data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
            return int(resp.status), json.loads(raw) if raw else {}
    except Exception as e:
        return -1, {"error": str(e), "path": path}


def _poll_task(task_id: str, timeout_seconds: int = 900) -> Tuple[bool, dict[str, Any]]:
    started = time.time()
    while time.time() - started < timeout_seconds:
        code, row = _http_get(f"/auto-trader/research/tasks/{task_id}", timeout=60.0)
        if code != 200:
            # 忙时查询接口可能短时超时，继续重试，避免误报失败。
            time.sleep(2.0)
            continue
        status = str(row.get("status", "")).lower()
        if status == "completed":
            return True, row
        if status in {"failed", "cancelled"}:
            return False, row
        time.sleep(2.0)
    return False, {"error": "task_timeout", "task_id": task_id}


def main() -> int:
    print("== Step1 验证：TradingAgents 研究接入 ==")

    code, health = _http_get("/health")
    print(f"[health] status={code} body={health}")
    if code != 200:
        print("FAIL: 后端未就绪")
        return 1

    run_body = {
        "async_run": True,
        "top_n": 2,
        "backtest_days": 90,
        "kline": "1d",
    }
    code, accepted = _http_post("/auto-trader/research/run", run_body, timeout=30.0)
    print(f"[research.run] status={code} body={accepted}")
    if code != 200:
        print("FAIL: 研究任务提交失败")
        return 2

    task_id = str(accepted.get("task_id", "")).strip()
    if not task_id:
        print("FAIL: 未返回 task_id")
        return 3

    ok, task = _poll_task(task_id, timeout_seconds=900)
    print(f"[research.task] ok={ok} body={task}")
    if not ok:
        print("FAIL: 研究任务未完成")
        return 4

    code, snap_row = _http_get("/auto-trader/research/snapshot")
    print(f"[research.snapshot] status={code}")
    if code != 200 or not bool(snap_row.get("has_snapshot")):
        print(f"FAIL: 快照读取失败，detail={snap_row}")
        return 5

    snap = snap_row.get("snapshot") if isinstance(snap_row, dict) else None
    if str(snap.get("trace_id", "")).strip() != str(accepted.get("trace_id", "")).strip():
        print(
            "FAIL: 快照 trace_id 与本次任务不一致，说明读取到了旧快照，"
            f"snapshot_trace={snap.get('trace_id')} expected={accepted.get('trace_id')}"
        )
        return 10

    if not isinstance(snap, dict):
        print("FAIL: snapshot 格式异常")
        return 6

    external_research_present = isinstance(snap.get("external_research"), dict)
    ext = snap.get("external_research") if external_research_present else {}
    ta_key_present = external_research_present and ("tradingagents_insights" in ext)
    ta_rows = ext.get("tradingagents_insights") if isinstance(ext.get("tradingagents_insights"), list) else []
    agent_gating_present = "agent_gating" in snap
    agent_gating = snap.get("agent_gating") if isinstance(snap.get("agent_gating"), dict) else {}
    strategy_rankings = snap.get("strategy_rankings") if isinstance(snap.get("strategy_rankings"), list) else []

    has_agent_fields = False
    for row in strategy_rankings:
        if isinstance(row, dict) and isinstance(row.get("best_strategy"), dict):
            best = row.get("best_strategy") or {}
            if "tradingagents_multiplier" in best or "tradingagents_action" in best:
                has_agent_fields = True
                break

    print(
        json.dumps(
            {
                "tradingagents_insights_count": len(ta_rows),
                "has_tradingagents_key": ta_key_present,
                "has_agent_gating_key": agent_gating_present,
                "agent_gating": agent_gating,
                "ranking_has_tradingagents_fields": has_agent_fields,
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    if not agent_gating_present:
        print("FAIL: 缺少 agent_gating 字段")
        return 7
    if not ta_key_present:
        print("FAIL: 缺少 external_research.tradingagents_insights 字段")
        return 8
    if not strategy_rankings:
        print("FAIL: strategy_rankings 为空，无法验证链路")
        return 9

    print("PASS: Step1 接入验证通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

