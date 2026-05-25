from __future__ import annotations

from api.brokers.base import BrokerAdapter


_ADAPTERS: dict[str, BrokerAdapter] = {}


def register_broker_adapter(broker_id: str, adapter: BrokerAdapter) -> BrokerAdapter:
    key = str(broker_id or "").strip().lower()
    if not key:
        raise ValueError("broker_id_required")
    _ADAPTERS[key] = adapter
    return adapter


def _load_longbridge_adapter() -> BrokerAdapter:
    adapter = _ADAPTERS.get("longbridge")
    if adapter is None:
        from api.brokers.longbridge_adapter import LongBridgeAdapter

        adapter = LongBridgeAdapter()
        _ADAPTERS["longbridge"] = adapter
        # Keep backward compatibility with existing naming in env/config.
        _ADAPTERS["longport"] = adapter
    return adapter


def _load_tiger_adapter() -> BrokerAdapter:
    adapter = _ADAPTERS.get("tiger")
    if adapter is None:
        from api.brokers.tiger_adapter import TigerAdapter

        adapter = TigerAdapter()
        _ADAPTERS["tiger"] = adapter
        _ADAPTERS["itiger"] = adapter
    return adapter


def get_broker_adapter(broker_id: str) -> BrokerAdapter:
    key = str(broker_id or "").strip().lower()
    if key in {"longbridge", "longport"}:
        return _load_longbridge_adapter()
    if key in {"tiger", "itiger"}:
        return _load_tiger_adapter()
    adapter = _ADAPTERS.get(key)
    if adapter is None:
        supported = "longbridge, longport, tiger, itiger"
        raise ValueError(f"Unsupported broker provider: {broker_id}. Supported: {supported}")
    return adapter
