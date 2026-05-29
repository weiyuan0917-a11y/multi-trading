from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import base64
import binascii
from datetime import datetime, timezone
from typing import Any


_ROOT = os.path.abspath(
    os.getenv("MULTITRADING_ROOT")
    or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
_AUTH_DATA_DIR = os.path.join(_ROOT, "data", "auth")
_LOCAL_LICENSES_FILE = os.path.join(_AUTH_DATA_DIR, "local_licenses.json")
_LOCK = threading.RLock()

PLAN_RANK: dict[str, int] = {
    "free": 0,
    "pro": 1,
    "premium": 2,
}

PLAN_FEATURES: dict[str, list[str]] = {
    "free": ["research", "backtest", "tradingagents", "openbb"],
    "pro": ["research", "backtest", "tradingagents", "openbb", "stock_auto_trading"],
    "premium": [
        "research",
        "backtest",
        "tradingagents",
        "openbb",
        "stock_auto_trading",
        "option_auto_trading",
        "multi_broker",
        "multi_account",
    ],
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _load_file() -> dict[str, Any]:
    if not os.path.isfile(_LOCAL_LICENSES_FILE):
        return {"licenses": {}}
    try:
        with open(_LOCAL_LICENSES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("licenses"), dict):
            return data
    except Exception:
        pass
    return {"licenses": {}}


def _save_file(data: dict[str, Any]) -> None:
    _ensure_parent_dir(_LOCAL_LICENSES_FILE)
    tmp = _LOCAL_LICENSES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, _LOCAL_LICENSES_FILE)


def normalize_owner(value: Any) -> str:
    return str(value or "").strip().lower()


def normalize_plan(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw == "premium":
        return "premium"
    if raw == "pro":
        return "pro"
    return "free"


def normalize_role(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"admin", "owner"}:
        return raw
    return "user"


def normalize_bool(value: Any) -> bool:
    raw = str(value or "").strip().lower()
    return raw in {"1", "true", "yes", "y", "on", "admin", "owner"}


def _root_env_value(name: str) -> str:
    value = str(os.getenv(name) or "").strip()
    if value:
        return value
    env_path = os.path.join(_ROOT, ".env")
    try:
        with open(env_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                raw = line.strip()
                if not raw or raw.startswith("#") or "=" not in raw:
                    continue
                key, val = raw.split("=", 1)
                if key.strip() == name:
                    return val.strip().strip("\"'")
    except Exception:
        return ""
    return ""


def _root_env_multiline(name: str) -> str:
    return _root_env_value(name).replace("\\n", "\n").strip()


def stronger_plan(left: str, right: str) -> str:
    a = normalize_plan(left)
    b = normalize_plan(right)
    return b if PLAN_RANK.get(b, 0) > PLAN_RANK.get(a, 0) else a


def _parse_time(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _time_ms(value: Any) -> int:
    parsed = _parse_time(value)
    if parsed is None:
        return 0
    return int(parsed.timestamp() * 1000)


def _number_ms(value: Any) -> int:
    try:
        n = float(value)
    except Exception:
        return 0
    if not n or n <= 0:
        return 0
    return int(n * 1000) if n < 100000000000 else int(n)


def _subscription_end_ms(license_data: dict[str, Any]) -> int:
    from_number = _number_ms(license_data.get("subscription_current_period_end"))
    if from_number > 0:
        return from_number
    return _time_ms(license_data.get("subscription_expires_at"))


def _subscription_active(license_data: dict[str, Any], now_ms: int | None = None) -> bool:
    if str(license_data.get("status") or "").lower() not in {"active", "trialing"}:
        return False
    end_ms = _subscription_end_ms(license_data)
    if end_ms <= 0:
        return False
    now = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    return end_ms > now


def _is_active_license(license_data: dict[str, Any]) -> bool:
    return bool(license_data.get("valid")) and str(license_data.get("status") or "").lower() in {"active", "trialing"}


def _import_rejection_reason(existing: dict[str, Any] | None, incoming: dict[str, Any]) -> str:
    if not existing or not _is_active_license(existing) or not _is_active_license(incoming):
        return ""

    existing_signature = str(existing.get("signature") or "").strip()
    incoming_signature = str(incoming.get("signature") or "").strip()
    if existing_signature and incoming_signature and existing_signature == incoming_signature:
        return ""

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    existing_issued = _time_ms(existing.get("issued_at"))
    incoming_issued = _time_ms(incoming.get("issued_at"))
    existing_period_end = _subscription_end_ms(existing)
    incoming_period_end = _subscription_end_ms(incoming)
    existing_active_end = existing_period_end or _time_ms(existing.get("expires_at"))
    existing_rank = PLAN_RANK.get(normalize_plan(existing.get("plan")), 0)
    incoming_rank = PLAN_RANK.get(normalize_plan(incoming.get("plan")), 0)

    if existing_issued > 0 and incoming_issued > 0 and incoming_issued < existing_issued:
        return "older_license_rejected"
    if existing_active_end > now_ms and incoming_rank < existing_rank:
        return "lower_plan_rejected"
    if existing_period_end > now_ms and incoming_period_end > 0 and incoming_period_end < existing_period_end:
        return "shorter_subscription_rejected"
    if existing_issued > 0 and incoming_issued <= 0:
        return "missing_issued_at_rejected"
    return ""


def _preview_import_decision(existing: dict[str, Any] | None, incoming: dict[str, Any]) -> dict[str, Any]:
    if not incoming.get("valid"):
        return {
            "can_import": False,
            "action": "invalid",
            "reason": incoming.get("validation_reason") or "invalid_license",
        }

    rejection_reason = _import_rejection_reason(existing, incoming)
    if rejection_reason:
        return {
            "can_import": False,
            "action": "rejected",
            "reason": rejection_reason,
        }

    if not existing or not _is_active_license(existing):
        return {"can_import": True, "action": "activate", "reason": "no_active_license"}

    existing_signature = str(existing.get("signature") or "").strip()
    incoming_signature = str(incoming.get("signature") or "").strip()
    if existing_signature and incoming_signature and existing_signature == incoming_signature:
        return {"can_import": True, "action": "duplicate", "reason": "same_license"}

    existing_rank = PLAN_RANK.get(normalize_plan(existing.get("plan")), 0)
    incoming_rank = PLAN_RANK.get(normalize_plan(incoming.get("plan")), 0)
    existing_period_end = _subscription_end_ms(existing)
    incoming_period_end = _subscription_end_ms(incoming)
    existing_issued = _time_ms(existing.get("issued_at"))
    incoming_issued = _time_ms(incoming.get("issued_at"))

    if incoming_rank > existing_rank and incoming_period_end > existing_period_end:
        return {"can_import": True, "action": "upgrade_and_renew", "reason": "higher_plan_longer_period"}
    if incoming_rank > existing_rank:
        return {"can_import": True, "action": "upgrade", "reason": "higher_plan"}
    if incoming_period_end > existing_period_end:
        return {"can_import": True, "action": "renew", "reason": "longer_period"}
    if incoming_issued > existing_issued:
        return {"can_import": True, "action": "replace", "reason": "newer_license"}
    return {"can_import": True, "action": "replace", "reason": "valid_license"}


def _signature_payload(license_data: dict[str, Any]) -> str:
    ignored = {
        "cached_at",
        "signature",
        "signature_status",
        "updated_at",
        "valid",
        "validation_reason",
    }
    body = {k: v for k, v in license_data.items() if k not in ignored}
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _signature_algorithm_and_value(license_data: dict[str, Any]) -> tuple[str, str]:
    signature = str(license_data.get("signature") or "").strip()
    alg = str(license_data.get("signature_alg") or license_data.get("signatureAlg") or "").strip().lower()
    alg = alg.replace("_", "-")
    aliases = {
        "sha256": "hmac-sha256",
        "hmac": "hmac-sha256",
        "hmac-sha256": "hmac-sha256",
        "rsa-pss": "rsa-pss-sha256",
        "rsa-pss-sha256": "rsa-pss-sha256",
        "rsassa-pss-sha256": "rsa-pss-sha256",
        "rsa-pkcs1": "rsassa-pkcs1-v1-5-sha256",
        "rsa-pkcs1-sha256": "rsassa-pkcs1-v1-5-sha256",
        "rsassa-pkcs1": "rsassa-pkcs1-v1-5-sha256",
        "rsassa-pkcs1-sha256": "rsassa-pkcs1-v1-5-sha256",
        "rsassa-pkcs1-v1-5-sha256": "rsassa-pkcs1-v1-5-sha256",
        "rsassa-pkcs1-v1.5-sha256": "rsassa-pkcs1-v1-5-sha256",
    }
    if "=" in signature:
        prefix, value = signature.split("=", 1)
        prefix_alg = aliases.get(prefix.strip().lower().replace("_", "-"))
        if prefix_alg:
            alg = alg or prefix_alg
            signature = value.strip()
    return aliases.get(alg or "hmac-sha256", alg or "hmac-sha256"), signature


def _verify_hmac_signature(license_data: dict[str, Any], signature: str) -> tuple[bool, str]:
    secret = _root_env_value("LOCAL_LICENSE_SIGNING_SECRET") or _root_env_value("CONVEX_LOCAL_LICENSE_SIGNING_SECRET")
    if not secret:
        return False, "missing_signing_secret"
    expected = hmac.new(secret.encode("utf-8"), _signature_payload(license_data).encode("utf-8"), hashlib.sha256)
    expected_hex = expected.hexdigest()
    supplied = signature[7:] if signature.lower().startswith("sha256=") else signature
    return (True, "valid_signature") if hmac.compare_digest(expected_hex, supplied) else (False, "invalid_signature")


def _decode_base64_signature(value: str) -> bytes:
    raw = str(value or "").strip()
    if not raw:
        return b""
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        padded = raw + ("=" * (-len(raw) % 4))
        return base64.urlsafe_b64decode(padded.encode("ascii"))


def _license_public_key_pem() -> str:
    for name in (
        "LOCAL_LICENSE_PUBLIC_KEY_PEM",
        "CONVEX_LOCAL_LICENSE_PUBLIC_KEY_PEM",
        "LOCAL_LICENSE_RSA_PUBLIC_KEY_PEM",
        "CONVEX_LOCAL_LICENSE_RSA_PUBLIC_KEY_PEM",
    ):
        value = _root_env_multiline(name)
        if value:
            return value
    for name in ("LOCAL_LICENSE_PUBLIC_KEY_PATH", "CONVEX_LOCAL_LICENSE_PUBLIC_KEY_PATH"):
        path = _root_env_value(name)
        if not path:
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                value = f.read().strip()
            if value:
                return value
        except Exception:
            continue
    return ""


def _verify_rsa_pss_signature(license_data: dict[str, Any], signature: str) -> tuple[bool, str]:
    public_pem = _license_public_key_pem()
    if not public_pem:
        return False, "missing_public_key"
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except Exception:
        return False, "missing_crypto_backend"
    try:
        public_key = serialization.load_pem_public_key(public_pem.encode("utf-8"))
        public_key.verify(
            _decode_base64_signature(signature),
            _signature_payload(license_data).encode("utf-8"),
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
            hashes.SHA256(),
        )
        return True, "valid_rsa_pss_signature"
    except InvalidSignature:
        return False, "invalid_signature"
    except Exception:
        return False, "invalid_public_key_or_signature"


def _verify_rsa_pkcs1_signature(license_data: dict[str, Any], signature: str) -> tuple[bool, str]:
    public_pem = _license_public_key_pem()
    if not public_pem:
        return False, "missing_public_key"
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except Exception:
        return False, "missing_crypto_backend"
    try:
        public_key = serialization.load_pem_public_key(public_pem.encode("utf-8"))
        public_key.verify(
            _decode_base64_signature(signature),
            _signature_payload(license_data).encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return True, "valid_rsa_pkcs1_signature"
    except InvalidSignature:
        return False, "invalid_signature"
    except Exception:
        return False, "invalid_public_key_or_signature"


def _verify_signature(license_data: dict[str, Any]) -> tuple[bool, str]:
    signature = str(license_data.get("signature") or "").strip()
    allow_unsigned = normalize_bool(_root_env_value("LOCAL_LICENSE_ALLOW_UNSIGNED") or "false")
    if not signature:
        return (True, "unsigned_allowed") if allow_unsigned else (False, "missing_signature")
    alg, supplied = _signature_algorithm_and_value(license_data)
    if alg == "hmac-sha256":
        return _verify_hmac_signature(license_data, supplied)
    if alg == "rsa-pss-sha256":
        return _verify_rsa_pss_signature(license_data, supplied)
    if alg == "rsassa-pkcs1-v1-5-sha256":
        return _verify_rsa_pkcs1_signature(license_data, supplied)
    return False, "unsupported_signature_alg"


def normalize_license(raw_license: dict[str, Any], fallback_owner_id: str = "") -> dict[str, Any]:
    source = raw_license if isinstance(raw_license, dict) else {}
    owner_id = normalize_owner(source.get("owner_id") or source.get("ownerId") or fallback_owner_id)
    plan = normalize_plan(source.get("plan"))
    role = normalize_role(source.get("role"))
    is_admin = normalize_bool(source.get("is_admin") or source.get("isAdmin")) or role in {"admin", "owner"}
    status = str(source.get("status") or "active").strip().lower()
    if status not in {"active", "trialing", "inactive", "canceled", "expired"}:
        status = "active"
    features = source.get("features")
    if not isinstance(features, list):
        features = PLAN_FEATURES.get(plan, PLAN_FEATURES["free"])
    clean_features = [str(x).strip() for x in features if str(x).strip()]
    license_data = {
        "owner_id": owner_id,
        "plan": plan,
        "status": status,
        "role": "admin" if is_admin and role == "user" else role,
        "is_admin": is_admin,
        "features": clean_features,
        "expires_at": source.get("expires_at") or source.get("expiresAt") or None,
        "issued_at": source.get("issued_at") or source.get("issuedAt") or None,
        "source": str(source.get("source") or "local_license").strip() or "local_license",
        "signature": str(source.get("signature") or "").strip(),
    }
    signature_alg = str(source.get("signature_alg") or source.get("signatureAlg") or "").strip().lower()
    if signature_alg:
        license_data["signature_alg"] = signature_alg
    signature_kid = str(source.get("signature_kid") or source.get("signatureKid") or "").strip()
    if signature_kid:
        license_data["signature_kid"] = signature_kid
    subscription_expires_at = source.get("subscription_expires_at") or source.get("subscriptionExpiresAt")
    if subscription_expires_at not in (None, ""):
        license_data["subscription_expires_at"] = subscription_expires_at
    subscription_current_period_end = source.get("subscription_current_period_end") or source.get("subscriptionCurrentPeriodEnd")
    if subscription_current_period_end not in (None, ""):
        license_data["subscription_current_period_end"] = subscription_current_period_end
    return license_data


def validate_license(raw_license: dict[str, Any], fallback_owner_id: str = "") -> dict[str, Any]:
    license_data = normalize_license(raw_license, fallback_owner_id)
    identity_valid = True
    reason = "ok"
    if not license_data["owner_id"]:
        identity_valid = False
        reason = "owner_required"
    elif license_data["status"] not in {"active", "trialing"}:
        identity_valid = False
        reason = f"status_{license_data['status']}"

    now = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    license_key_valid = identity_valid
    license_key_reason = "ok" if identity_valid else reason
    expires_at = _parse_time(license_data.get("expires_at"))
    if identity_valid and expires_at is not None and expires_at < now:
        license_key_valid = False
        license_key_reason = "expired"

    signature_ok, signature_status = _verify_signature(license_data)
    if not signature_ok:
        identity_valid = False
        license_key_valid = False
        reason = signature_status
        license_key_reason = signature_status

    subscription_valid = bool(identity_valid and signature_ok and _subscription_active(license_data, now_ms))
    entitlement_valid = bool(signature_ok and identity_valid and (license_key_valid or subscription_valid))
    if entitlement_valid:
        reason = "ok"
    else:
        if reason == "ok":
            reason = license_key_reason

    license_data["valid"] = entitlement_valid
    license_data["validation_reason"] = reason
    license_data["signature_status"] = signature_status
    license_data["license_key_valid"] = license_key_valid
    license_data["license_key_reason"] = license_key_reason
    license_data["subscription_valid"] = subscription_valid
    license_data["entitlement_valid"] = entitlement_valid
    return license_data


def get_local_license(owner_id: str) -> dict[str, Any] | None:
    owner = normalize_owner(owner_id)
    if not owner:
        return None
    with _LOCK:
        licenses = _load_file().get("licenses") or {}
        raw = licenses.get(owner)
    if not isinstance(raw, dict):
        return None
    return validate_license(raw, owner)


def save_local_license(raw_license: dict[str, Any], fallback_owner_id: str = "") -> dict[str, Any]:
    license_data = validate_license(raw_license, fallback_owner_id)
    if not license_data.get("valid"):
        return license_data
    owner = normalize_owner(license_data.get("owner_id"))
    cached_at = _now_iso()
    license_data["cached_at"] = cached_at
    license_data["updated_at"] = cached_at
    with _LOCK:
        data = _load_file()
        licenses = data.get("licenses") if isinstance(data.get("licenses"), dict) else {}
        existing = licenses.get(owner)
        existing_license = validate_license(existing, owner) if isinstance(existing, dict) else None
        rejection_reason = _import_rejection_reason(existing_license, license_data)
        if rejection_reason:
            license_data["valid"] = False
            license_data["validation_reason"] = rejection_reason
            if existing_license:
                license_data["existing_plan"] = existing_license.get("plan")
                license_data["existing_issued_at"] = existing_license.get("issued_at")
                license_data["existing_subscription_expires_at"] = existing_license.get("subscription_expires_at")
                license_data["existing_subscription_current_period_end"] = existing_license.get(
                    "subscription_current_period_end"
                )
            return license_data
        licenses[owner] = license_data
        data["licenses"] = licenses
        _save_file(data)
    return license_data


def preview_local_license_import(raw_license: dict[str, Any], fallback_owner_id: str = "") -> dict[str, Any]:
    incoming = validate_license(raw_license, fallback_owner_id)
    owner = normalize_owner(incoming.get("owner_id") or fallback_owner_id)
    current = None
    if owner:
        with _LOCK:
            licenses = _load_file().get("licenses") or {}
            existing = licenses.get(owner)
        current = validate_license(existing, owner) if isinstance(existing, dict) else None
    decision = _preview_import_decision(current, incoming)
    return {
        "ok": True,
        "owner_id": owner,
        "current": current,
        "incoming": incoming,
        **decision,
    }


def delete_local_license(owner_id: str) -> bool:
    owner = normalize_owner(owner_id)
    if not owner:
        return False
    with _LOCK:
        data = _load_file()
        licenses = data.get("licenses") if isinstance(data.get("licenses"), dict) else {}
        existed = owner in licenses
        licenses.pop(owner, None)
        data["licenses"] = licenses
        _save_file(data)
    return existed


def valid_license_identity(owner_id: str) -> dict[str, Any] | None:
    license_data = get_local_license(owner_id)
    if not license_data or not license_data.get("valid"):
        return None
    return {
        "owner_id": normalize_owner(license_data.get("owner_id")),
        "plan": normalize_plan(license_data.get("plan")),
        "role": normalize_role(license_data.get("role")),
        "is_admin": bool(license_data.get("is_admin")),
        "features": list(license_data.get("features") or []),
        "source": "local_license",
    }
