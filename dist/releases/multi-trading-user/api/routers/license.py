from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Header, HTTPException

from api.routers.local_owner import require_local_identity
from api.services.local_license_service import (
    delete_local_license,
    get_local_license,
    preview_local_license_import,
    save_local_license,
)


router = APIRouter(tags=["license"])


@router.get("/license/local")
def license_local_get(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    identity = require_local_identity(authorization, x_local_owner, x_api_key)
    license_data = get_local_license(identity.owner_id)
    return {
        "ok": True,
        "owner_id": identity.owner_id,
        "license": license_data,
        "valid": bool(license_data and license_data.get("valid")),
        "reason": (license_data or {}).get("validation_reason") if isinstance(license_data, dict) else "not_found",
    }


@router.post("/license/local/preview")
def license_local_preview(
    body: dict[str, Any] = Body(...),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    identity = require_local_identity(authorization, x_local_owner, x_api_key)
    raw_license = body.get("license") if isinstance(body.get("license"), dict) else body
    if not isinstance(raw_license, dict):
        raise HTTPException(status_code=400, detail="license_required")

    requested_owner = str(raw_license.get("owner_id") or raw_license.get("ownerId") or identity.owner_id).strip().lower()
    if requested_owner and requested_owner != identity.owner_id and not identity.is_admin:
        raise HTTPException(status_code=403, detail="owner_mismatch")

    return preview_local_license_import(raw_license, identity.owner_id)


@router.put("/license/local")
def license_local_put(
    body: dict[str, Any] = Body(...),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    identity = require_local_identity(authorization, x_local_owner, x_api_key)
    raw_license = body.get("license") if isinstance(body.get("license"), dict) else body
    if not isinstance(raw_license, dict):
        raise HTTPException(status_code=400, detail="license_required")

    requested_owner = str(raw_license.get("owner_id") or raw_license.get("ownerId") or identity.owner_id).strip().lower()
    if requested_owner and requested_owner != identity.owner_id and not identity.is_admin:
        raise HTTPException(status_code=403, detail="owner_mismatch")

    saved = save_local_license(raw_license, identity.owner_id)
    if not saved.get("valid"):
        reason = str(saved.get("validation_reason") or "")
        import_rejected = reason.endswith("_rejected")
        raise HTTPException(
            status_code=409 if import_rejected else 400,
            detail={
                "error": "license_import_rejected" if import_rejected else "invalid_license",
                "reason": reason,
                "signature_status": saved.get("signature_status"),
            },
        )
    return {"ok": True, "owner_id": saved.get("owner_id"), "license": saved}


@router.delete("/license/local")
def license_local_delete(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_local_owner: str | None = Header(default=None, alias="X-MT-Local-Owner"),
) -> dict[str, Any]:
    identity = require_local_identity(authorization, x_local_owner, x_api_key)
    return {"ok": True, "owner_id": identity.owner_id, "deleted": delete_local_license(identity.owner_id)}
