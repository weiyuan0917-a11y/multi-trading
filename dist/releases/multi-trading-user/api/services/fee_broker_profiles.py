"""
多券商费用模型：持久化在 config/fee_schedule.json（multi_broker_v1 格式），
与旧版「单表根级 hk_stock」文件自动兼容迁移。

生效规则（简化）：
- 默认交易账户已连接（行情+交易上下文就绪且非手动断开）时，使用与该账户 broker_provider
  同名的费用模板（大小写不敏感匹配 brokers 键）。
- 否则使用 manual_fee_broker_id 指定的模板（用户「未连接时」选用的快照）。
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from mcp_server.fee_model import get_default_fee_schedule, normalize_fee_schedule, set_fee_schedule

MULTI_FORMAT_V1 = "multi_broker_v1"
DEFAULT_BROKER_ID = "longbridge"
_BROKER_ID_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")

_path: Path | None = None
_doc: dict[str, Any] | None = None


def _read_json_file(p: Path) -> dict[str, Any]:
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _write_json_file(p: Path, data: dict[str, Any]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _broker_keys_sorted(d: dict[str, Any]) -> list[str]:
    return sorted((d.get("brokers") or {}).keys(), key=str)


def _ensure_manual_fee_broker_id(d: dict[str, Any]) -> None:
    br = d.get("brokers") or {}
    keys = _broker_keys_sorted(d)
    if not keys:
        return
    man = d.get("manual_fee_broker_id")
    if not man or str(man) not in br:
        fallback = str(d.get("active_broker_id") or keys[0])
        d["manual_fee_broker_id"] = fallback if fallback in br else keys[0]


def _new_doc_single(broker_id: str, display_name: str, schedule: dict[str, Any]) -> dict[str, Any]:
    norm = normalize_fee_schedule(schedule) if schedule else get_default_fee_schedule()
    return {
        "format": MULTI_FORMAT_V1,
        "active_broker_id": broker_id,
        "manual_fee_broker_id": broker_id,
        "brokers": {broker_id: {"display_name": display_name, "schedule": norm}},
    }


def migrate_raw_to_doc(raw: dict[str, Any]) -> dict[str, Any]:
    if raw.get("format") == MULTI_FORMAT_V1 and isinstance(raw.get("brokers"), dict) and raw["brokers"]:
        doc = copy.deepcopy(raw)
        doc.setdefault("active_broker_id", next(iter(doc["brokers"].keys())))
        for bid, meta in list(doc["brokers"].items()):
            if not isinstance(meta, dict):
                doc["brokers"].pop(bid, None)
                continue
            sched = meta.get("schedule")
            if not isinstance(sched, dict):
                meta["schedule"] = get_default_fee_schedule()
            else:
                meta["schedule"] = normalize_fee_schedule(sched)
            meta.setdefault("display_name", str(bid))
        act = str(doc.get("active_broker_id") or "")
        if act not in doc["brokers"]:
            doc["active_broker_id"] = next(iter(doc["brokers"].keys()))
        _ensure_manual_fee_broker_id(doc)
        return doc
    if "hk_stock" in raw and "brokers" not in raw:
        return _new_doc_single(DEFAULT_BROKER_ID, "长桥（默认）", raw)
    return _new_doc_single(DEFAULT_BROKER_ID, "长桥（默认）", get_default_fee_schedule())


def _ensure_doc_has_brokers(doc: dict[str, Any]) -> dict[str, Any]:
    br = doc.get("brokers")
    if isinstance(br, dict) and len(br) > 0:
        return doc
    return _new_doc_single(DEFAULT_BROKER_ID, "长桥（默认）", get_default_fee_schedule())


def init_fee_broker_profiles(file_path: str) -> None:
    global _path, _doc
    _path = Path(file_path)
    raw = _read_json_file(_path)
    migrated = migrate_raw_to_doc(raw)
    migrated = _ensure_doc_has_brokers(migrated)
    if raw != migrated:
        _write_json_file(_path, migrated)
    _doc = migrated
    try:
        sync_runtime_fee_from_accounts(persist_effective_mirror=False)
    except Exception:
        pass


def _require_doc() -> dict[str, Any]:
    if _doc is None:
        raise RuntimeError("fee broker profiles 未初始化")
    return _doc


def _default_account_connection_snapshot() -> tuple[bool, str]:
    """(默认账户是否已连接, broker_provider 小写)。任意异常时回退为未连接，避免 /fees/brokers 整体 500。"""
    try:
        from api import main as m

        owner = "__system__"
        aid = m.ACCOUNT_REGISTRY.get_default_account_id(owner_id=owner)
        rec = m.ACCOUNT_REGISTRY.get_account_record(aid, owner_id=owner)
        connected = (
            not bool(rec.manual_disconnected) and rec.quote_ctx is not None and rec.trade_ctx is not None
        )
        bp = str(rec.broker_provider or "").strip().lower() or "longbridge"
        return connected, bp
    except Exception:
        return False, "longbridge"


def _match_template_broker_id(d: dict[str, Any], provider: str) -> str | None:
    raw = (provider or "").strip()
    if not raw:
        return None
    br = d.get("brokers") or {}
    if raw in br:
        return str(raw)
    low = raw.lower()
    for k in br:
        if str(k).lower() == low:
            return str(k)
    return None


def resolve_effective_broker_id() -> tuple[str, str, dict[str, Any]]:
    """
    返回 (effective_broker_id, fee_source, detail)
    fee_source: account | manual | manual_fallback_no_template
    """
    d = _require_doc()
    connected, prov = _default_account_connection_snapshot()
    detail: dict[str, Any] = {
        "default_account_connected": connected,
        "default_broker_provider": prov,
    }
    keys = _broker_keys_sorted(d)
    if not keys:
        return "", "manual", {**detail, "note": "brokers 为空，请检查 config/fee_schedule.json"}
    br = d["brokers"]

    if connected:
        bid = _match_template_broker_id(d, prov)
        if bid:
            detail["note"] = "默认账户已连接，费用模板与账户券商一致"
            return bid, "account", detail
        detail["unmatched_broker_provider"] = prov
        detail["note"] = "默认账户已连接，但未找到同名费用模板，暂用「未连接时」所选模板"
        man = str(d.get("manual_fee_broker_id") or "")
        if man in br:
            return man, "manual_fallback_no_template", detail
        return keys[0], "manual_fallback_no_template", detail

    man = str(d.get("manual_fee_broker_id") or "")
    if man in br:
        detail["note"] = "默认账户未连接，使用手动选择的费用模板"
        return man, "manual", detail
    detail["note"] = "默认账户未连接，manual_fee_broker_id 无效，已回退到首个模板"
    return keys[0], "manual", detail


def sync_runtime_fee_from_accounts(*, persist_effective_mirror: bool = False) -> dict[str, Any]:
    """
    按账户连接状态刷新内存中的费用表（set_fee_schedule）。
    persist_effective_mirror=True 时把 active_broker_id 写入文件（仅用于需落盘场景）。
    """
    d = _require_doc()
    bid, source, detail = resolve_effective_broker_id()
    if not bid or bid not in d.get("brokers", {}):
        keys = _broker_keys_sorted(d)
        if not keys:
            return {"effective_broker_id": "", "fee_source": source, "fee_resolution": detail}
        bid = keys[0]
    sched = d["brokers"][bid]["schedule"]
    set_fee_schedule(sched)
    if persist_effective_mirror:
        d["active_broker_id"] = bid
        assert _path is not None
        _write_json_file(_path, d)
    return {
        "effective_broker_id": bid,
        "fee_source": source,
        "fee_resolution": detail,
    }


def list_broker_profiles() -> dict[str, Any]:
    d = _require_doc()
    brokers: list[dict[str, Any]] = []
    for bid, meta in d["brokers"].items():
        if not isinstance(meta, dict):
            continue
        brokers.append(
            {
                "broker_id": str(bid),
                "display_name": str(meta.get("display_name") or bid),
            }
        )
    brokers.sort(key=lambda x: x["broker_id"])
    try:
        eff, src, detail = resolve_effective_broker_id()
    except Exception as e:
        keys = _broker_keys_sorted(d)
        eff = keys[0] if keys else ""
        src = "manual"
        detail = {"fee_source": src, "resolve_exception": str(e)}
    if not eff and brokers:
        eff = brokers[0]["broker_id"]
    manual = str(d.get("manual_fee_broker_id") or eff)
    if manual not in (d.get("brokers") or {}) and brokers:
        manual = brokers[0]["broker_id"]
    return {
        "brokers": brokers,
        "manual_fee_broker_id": manual,
        "effective_broker_id": eff,
        "fee_source": src,
        "fee_resolution": {**detail, "fee_source": src},
        # 兼容旧前端：active_broker_id 表示「当前试算/回测实际生效」的模板
        "active_broker_id": eff,
    }


def get_schedule_for_broker(broker_id: str | None) -> dict[str, Any]:
    d = _require_doc()
    if broker_id is None or str(broker_id).strip() == "":
        try:
            bid, _, _ = resolve_effective_broker_id()
        except Exception:
            bid = ""
        if not bid or bid not in d.get("brokers", {}):
            keys = _broker_keys_sorted(d)
            bid = keys[0] if keys else ""
    else:
        bid = str(broker_id).strip()
    if not bid or bid not in d["brokers"]:
        raise HTTPException(status_code=404, detail=f"未知券商费用配置: {bid}")
    return copy.deepcopy(d["brokers"][bid]["schedule"])


def set_manual_fee_broker(broker_id: str) -> dict[str, Any]:
    """设置「未连接默认账户」时使用的费用模板，并立即按规则刷新运行时。"""
    d = _require_doc()
    bid = str(broker_id).strip()
    if bid not in d["brokers"]:
        raise HTTPException(status_code=404, detail=f"未知券商: {bid}")
    d["manual_fee_broker_id"] = bid
    assert _path is not None
    _write_json_file(_path, d)
    sync_runtime_fee_from_accounts(persist_effective_mirror=False)
    return list_broker_profiles()


def set_active_broker(broker_id: str) -> dict[str, Any]:
    """兼容旧 API：等同于 set_manual_fee_broker。"""
    return set_manual_fee_broker(broker_id)


def save_schedule_for_broker(broker_id: str | None, patch: dict[str, Any]) -> dict[str, Any]:
    d = _require_doc()
    if broker_id is None or str(broker_id).strip() == "":
        bid = resolve_effective_broker_id()[0]
    else:
        bid = str(broker_id).strip()
    if bid not in d["brokers"]:
        raise HTTPException(status_code=404, detail=f"未知券商: {bid}")
    merged = normalize_fee_schedule(patch)
    d["brokers"][bid]["schedule"] = merged
    assert _path is not None
    _write_json_file(_path, d)
    eff, _, _ = resolve_effective_broker_id()
    if bid == eff:
        set_fee_schedule(merged)
    return merged


def add_broker_profile(broker_id: str, display_name: str, copy_from: str | None) -> dict[str, Any]:
    d = _require_doc()
    bid = broker_id.strip()
    if not _BROKER_ID_RE.match(bid):
        raise HTTPException(
            status_code=400,
            detail="broker_id 须为字母开头，仅含字母数字、下划线、连字符，长度 1–64",
        )
    if bid in d["brokers"]:
        raise HTTPException(status_code=400, detail="该 broker_id 已存在")
    dn = (display_name or bid).strip() or bid
    if copy_from:
        cf = copy_from.strip()
        if cf not in d["brokers"]:
            raise HTTPException(status_code=400, detail="copy_from 指定的券商不存在")
        base = copy.deepcopy(d["brokers"][cf]["schedule"])
    else:
        base = get_default_fee_schedule()
    d["brokers"][bid] = {"display_name": dn, "schedule": base}
    assert _path is not None
    _write_json_file(_path, d)
    sync_runtime_fee_from_accounts(persist_effective_mirror=False)
    return list_broker_profiles()


def update_broker_display_name(broker_id: str, display_name: str) -> dict[str, Any]:
    d = _require_doc()
    bid = broker_id.strip()
    if bid not in d["brokers"]:
        raise HTTPException(status_code=404, detail=f"未知券商: {bid}")
    d["brokers"][bid]["display_name"] = (display_name or bid).strip() or bid
    assert _path is not None
    _write_json_file(_path, d)
    return list_broker_profiles()


def delete_broker_profile(broker_id: str) -> dict[str, Any]:
    d = _require_doc()
    bid = str(broker_id).strip()
    if bid not in d["brokers"]:
        raise HTTPException(status_code=404, detail=f"未知券商: {bid}")
    if len(d["brokers"]) <= 1:
        raise HTTPException(status_code=400, detail="至少须保留一个券商费用配置，无法删除")
    del d["brokers"][bid]
    rem = _broker_keys_sorted(d)
    if str(d.get("manual_fee_broker_id") or "") == bid:
        d["manual_fee_broker_id"] = rem[0]
    if str(d.get("active_broker_id") or "") == bid:
        d["active_broker_id"] = rem[0]
    assert _path is not None
    _write_json_file(_path, d)
    sync_runtime_fee_from_accounts(persist_effective_mirror=False)
    return list_broker_profiles()
