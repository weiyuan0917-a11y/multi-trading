from __future__ import annotations

import os
from pathlib import Path

_LOADED_ENV_FILES: set[str] = set()


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        if not path.exists():
            return values
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            key, value = s.split("=", 1)
            key = key.strip()
            if not key:
                continue
            values[key] = value.strip().strip("\"'")
    except Exception:
        return {}
    return values


def load_project_env(project_root: Path | None = None, override: bool = False) -> dict[str, str]:
    root = (project_root or Path(os.getenv("MULTITRADING_ROOT") or Path(__file__).resolve().parents[1])).resolve()
    env_path = root / ".env"
    cache_key = str(env_path).lower()
    if cache_key in _LOADED_ENV_FILES and not override:
        return {}

    # 根 .env → 用户 davies 迁移；启动时用 davies 的 per-user 文件填充托管变量
    from config.user_env_store import bootstrap_process_env_from_davies, migrate_legacy_root_env

    migrate_legacy_root_env(root)
    bootstrap_process_env_from_davies(root)

    values = parse_env_file(env_path)
    if not values:
        _LOADED_ENV_FILES.add(cache_key)
        return {}

    for key, value in values.items():
        if override or key not in os.environ:
            os.environ[key] = value
    _LOADED_ENV_FILES.add(cache_key)
    return values
