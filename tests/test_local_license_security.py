from __future__ import annotations

import base64
import hmac
import hashlib
import os
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from api.main import app
from api.services import local_license_service as licenses


def _iso(offset_days: int = 0, offset_hours: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=offset_days, hours=offset_hours)).isoformat()


@pytest.fixture()
def isolated_license_store(tmp_path, monkeypatch):
    monkeypatch.setattr(licenses, "_LOCAL_LICENSES_FILE", str(tmp_path / "local_licenses.json"))
    for key in (
        "LOCAL_AGENT_OWNER_PLAN",
        "LOCAL_AGENT_OWNER_ROLE",
        "LOCAL_AGENT_OWNER_IS_ADMIN",
        "LOCAL_AGENT_ALLOWED_OWNERS",
        "LOCAL_LICENSE_SIGNING_SECRET",
        "CONVEX_LOCAL_LICENSE_SIGNING_SECRET",
        "LOCAL_LICENSE_PUBLIC_KEY_PEM",
        "CONVEX_LOCAL_LICENSE_PUBLIC_KEY_PEM",
        "LOCAL_LICENSE_RSA_PUBLIC_KEY_PEM",
        "CONVEX_LOCAL_LICENSE_RSA_PUBLIC_KEY_PEM",
        "LOCAL_LICENSE_ALLOW_UNSIGNED",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LOCAL_AGENT_ALLOW_USER_OWNERS", "true")
    return tmp_path


@pytest.fixture()
def rsa_keypair(monkeypatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    monkeypatch.setenv("LOCAL_LICENSE_PUBLIC_KEY_PEM", public_pem.decode("utf-8"))
    return private_key


def _rsa_license(private_key, *, owner: str = "alice", plan: str = "pro", issued_at: str | None = None) -> dict:
    row = {
        "owner_id": owner,
        "plan": plan,
        "status": "active",
        "role": "user",
        "is_admin": False,
        "features": licenses.PLAN_FEATURES[licenses.normalize_plan(plan)],
        "expires_at": _iso(offset_days=14),
        "issued_at": issued_at or _iso(),
        "source": "pytest",
        "signature_alg": "rsa-pss-sha256",
    }
    signature = private_key.sign(
        licenses._signature_payload(row).encode("utf-8"),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
        hashes.SHA256(),
    )
    row["signature"] = "rsa-pss-sha256=" + base64.b64encode(signature).decode("ascii")
    return row


def _rsa_license_with_subscription(
    private_key,
    *,
    owner: str = "alice",
    plan: str = "premium",
    license_expires_days: int = -1,
    subscription_expires_days: int = 82,
) -> dict:
    subscription_expires_at = _iso(offset_days=subscription_expires_days)
    subscription_ms = int(datetime.fromisoformat(subscription_expires_at).timestamp() * 1000)
    row = {
        "owner_id": owner,
        "plan": plan,
        "status": "active",
        "role": "user",
        "is_admin": False,
        "features": licenses.PLAN_FEATURES[licenses.normalize_plan(plan)],
        "expires_at": _iso(offset_days=license_expires_days),
        "subscription_expires_at": subscription_expires_at,
        "subscription_current_period_end": subscription_ms,
        "issued_at": _iso(offset_days=-8),
        "source": "pytest_subscription_renewal",
        "signature_alg": "rsa-pss-sha256",
    }
    signature = private_key.sign(
        licenses._signature_payload(row).encode("utf-8"),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
        hashes.SHA256(),
    )
    row["signature"] = "rsa-pss-sha256=" + base64.b64encode(signature).decode("ascii")
    return row


def _hmac_license(secret: str, *, owner: str = "alice", plan: str = "pro") -> dict:
    row = {
        "owner_id": owner,
        "plan": plan,
        "status": "active",
        "role": "user",
        "is_admin": False,
        "features": licenses.PLAN_FEATURES[licenses.normalize_plan(plan)],
        "expires_at": _iso(offset_days=14),
        "issued_at": _iso(),
        "source": "pytest",
    }
    digest = hmac.new(secret.encode("utf-8"), licenses._signature_payload(row).encode("utf-8"), hashlib.sha256)
    row["signature"] = "sha256=" + digest.hexdigest()
    return row


def test_rsa_signature_valid_and_tamper_fails(isolated_license_store, rsa_keypair):
    signed = _rsa_license(rsa_keypair, plan="pro")

    valid = licenses.validate_license(signed)
    assert valid["valid"] is True
    assert valid["signature_status"] == "valid_rsa_pss_signature"

    tampered = deepcopy(signed)
    tampered["plan"] = "premium"
    invalid = licenses.validate_license(tampered)
    assert invalid["valid"] is False
    assert invalid["validation_reason"] == "invalid_signature"


def test_legacy_hmac_license_still_valid(isolated_license_store, monkeypatch):
    monkeypatch.setenv("LOCAL_LICENSE_SIGNING_SECRET", "unit-test-secret")
    signed = _hmac_license("unit-test-secret", plan="pro")

    valid = licenses.validate_license(signed)
    assert valid["valid"] is True
    assert valid["signature_status"] == "valid_signature"


def test_license_downgrade_is_rejected(isolated_license_store, rsa_keypair):
    premium = _rsa_license(rsa_keypair, plan="premium", issued_at=_iso())
    pro = _rsa_license(rsa_keypair, plan="pro", issued_at=_iso(offset_hours=1))

    saved = licenses.save_local_license(premium)
    assert saved["valid"] is True

    downgraded = licenses.save_local_license(pro)
    assert downgraded["valid"] is False
    assert downgraded["validation_reason"] == "lower_plan_rejected"


def test_removed_auto_trading_endpoint_is_410_without_license(isolated_license_store):
    client = TestClient(app)
    response = client.post(
        "/auto-trading/stocks/start",
        json={},
        headers={
            "X-MT-Local-Owner": "alice",
            "X-MT-Cloud-Plan": "premium",
            "X-MT-Cloud-Role": "admin",
            "X-MT-Cloud-Is-Admin": "true",
        },
    )

    assert response.status_code == 410
    assert response.json()["detail"]["reason"] == "auto_trading_removed"


def test_removed_auto_trading_endpoint_is_410_with_valid_license(isolated_license_store, rsa_keypair):
    saved = licenses.save_local_license(_rsa_license(rsa_keypair, owner="alice", plan="pro"))
    assert saved["valid"] is True

    client = TestClient(app)
    response = client.post(
        "/auto-trading/stocks/start",
        json={},
        headers={"X-MT-Local-Owner": "alice"},
    )

    assert response.status_code == 410
    assert response.json()["detail"]["reason"] == "auto_trading_removed"


def test_expired_license_key_keeps_entitlement_when_subscription_is_active(isolated_license_store, rsa_keypair):
    row = _rsa_license_with_subscription(
        rsa_keypair,
        owner="davies0811",
        plan="premium",
        license_expires_days=-1,
        subscription_expires_days=82,
    )
    validated = licenses.validate_license(row)
    assert validated["valid"] is True
    assert validated["license_key_valid"] is False
    assert validated["license_key_reason"] == "expired"
    assert validated["subscription_valid"] is True
    assert validated["plan"] == "premium"

    saved = licenses.save_local_license(row)
    assert saved["valid"] is True

    client = TestClient(app)
    with patch("api.runtime_bridge.setup_start_services", return_value={"ok": True}):
        response = client.post(
            "/setup/services/start",
            json={"enable_qqq_0dte_live": True},
            headers={"X-MT-Local-Owner": "davies0811"},
        )

    assert response.status_code == 200


def test_local_owner_header_must_match_authenticated_session(isolated_license_store, monkeypatch):
    client = TestClient(app)
    fake_auth = type(
        "FakeAuth",
        (),
        {
            "me": lambda _self, _token: {
                "ok": True,
                "user": {"username": "davies1983", "plan": "free", "role": "user", "is_admin": False},
            }
        },
    )()

    with patch("api.routers.local_owner.get_user_auth_service", return_value=fake_auth):
        response = client.get(
            "/setup/config",
            headers={
                "Authorization": "Bearer fake-session-token",
                "X-MT-Local-Owner": "davies",
            },
        )

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["error"] == "local_owner_session_mismatch"
    assert detail["session_owner"] == "davies1983"
    assert detail["requested_owner"] == "davies"


def test_matching_local_owner_header_still_gets_removed_auto_trading_endpoint(isolated_license_store):
    client = TestClient(app)
    fake_auth = type(
        "FakeAuth",
        (),
        {
            "me": lambda _self, _token: {
                "ok": True,
                "user": {"username": "davies", "plan": "free", "role": "admin", "is_admin": True},
            }
        },
    )()

    with patch("api.routers.local_owner.get_user_auth_service", return_value=fake_auth):
        response = client.post(
            "/auto-trading/options-0dte/start",
            json={},
            headers={
                "Authorization": "Bearer fake-session-token",
                "X-MT-Local-Owner": "davies",
            },
        )

    assert response.status_code == 410
    assert response.json()["detail"]["reason"] == "auto_trading_removed"


def test_qqq_live_service_start_requires_option_auto_trading_entitlement(isolated_license_store):
    client = TestClient(app)

    with patch("api.runtime_bridge.setup_start_services", side_effect=AssertionError("should_not_start")):
        response = client.post(
            "/setup/services/start",
            json={"enable_qqq_0dte_live": True},
            headers={"X-MT-Local-Owner": "alice"},
        )

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["error"] == "plan_required"
    assert detail["feature"] == "option_auto_trading"
