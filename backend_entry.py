from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

from backend_uvicorn_spec import DEFAULT_API_PORT, LAUNCHER_UVICORN_HOST


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def main() -> int:
    root = _runtime_root()
    os.environ.setdefault("MULTITRADING_ROOT", str(root))
    os.environ.setdefault("MT_BUILD_TARGET", "customer")
    os.environ.setdefault("LOCAL_AGENT_ALLOW_USER_OWNERS", "true")

    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    worker = ""
    pass_args: list[str] = []
    for arg in sys.argv[1:]:
        raw = str(arg or "").strip()
        if raw.startswith("--worker="):
            worker = raw.split("=", 1)[1].strip().lower()
        elif raw != "--worker":
            pass_args.append(arg)
    if worker:
        modules = {
        }
        mod = modules.get(worker)
        if not mod:
            raise SystemExit(f"unknown worker: {worker}")
        sys.argv = [mod, *pass_args]
        runpy.run_module(mod, run_name="__main__")
        return 0

    host = LAUNCHER_UVICORN_HOST
    port = int(os.getenv("LONGPORT_API_PORT", str(DEFAULT_API_PORT)) or str(DEFAULT_API_PORT))
    for arg in sys.argv[1:]:
        raw = str(arg or "").strip()
        if raw.startswith("--host="):
            host = raw.split("=", 1)[1].strip() or host
        elif raw.startswith("--port="):
            value = raw.split("=", 1)[1].strip()
            if value.isdigit():
                port = int(value)

    import uvicorn

    uvicorn.run("api.main:app", host=host, port=port, reload=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
