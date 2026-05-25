"""
按登录用户隔离的「设置页 / .env 等价」密钥存储。

- 每个用户一份文件：data/user_env/<urlquote(username)>.env
- 首次升级：将项目根目录 .env 中的变量迁移到用户 `davies`，并写入占位根 .env（不再存放密钥）。
- 进程启动时：将用户 `davies` 的文件合并进 os.environ，供 live_settings / 后台任务在无登录上下文时使用。
- 用户登录或访问设置页时：将当前用户的文件合并进 os.environ（覆盖托管变量），实现多账号切换下的运行时一致（单机常用场景）。
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

# 历史根目录 .env 中的密钥在首次迁移时归并到该登录名（小写）
LEGACY_MIGRATION_USERNAME = "davies"

# 与 api.main.ENV_VAR_MAP 的 value 集合保持一致（设置页读写的环境变量名）
USER_ENV_MANAGED_KEYS: frozenset[str] = frozenset(
    {
        "BROKER_PROVIDER",
        "DEFAULT_ACCOUNT_ID",
        "LONGPORT_APP_KEY",
        "LONGPORT_APP_SECRET",
        "LONGPORT_ACCESS_TOKEN",
        "FEISHU_APP_ID",
        "FEISHU_APP_SECRET",
        "FEISHU_SCHEDULED_CHAT_ID",
        "FINNHUB_API_KEY",
        "TIINGO_API_KEY",
        "FRED_API_KEY",
        "COINGECKO_API_KEY",
        "OPENCLAW_MCP_MAX_LEVEL",
        "OPENCLAW_MCP_ALLOW_L3",
        "OPENCLAW_MCP_L3_CONFIRMATION_TOKEN",
        "OPENBB_ENABLED",
        "OPENBB_BASE_URL",
        "OPENBB_TIMEOUT_SECONDS",
        "OPENBB_AUTO_START",
        "CN_MARKET_DATA_PROVIDER_ORDER",
        "CN_MARKET_MOOTDX_ENABLED",
        "CN_MARKET_TENCENT_ENABLED",
        "CN_MARKET_AKSHARE_ENABLED",
        "CN_MARKET_TUSHARE_ENABLED",
        "CN_MARKET_BAOSTOCK_ENABLED",
        "TUSHARE_TOKEN",
        "PUBLIC_MARKET_DATA_PROVIDER_ORDER",
        "PUBLIC_MARKET_MOOTDX_ENABLED",
        "PUBLIC_MARKET_EASTMONEY_ENABLED",
        "PUBLIC_MARKET_YAHOO_ENABLED",
        "PUBLIC_MARKET_AKSHARE_ENABLED",
        "PUBLIC_MARKET_STOOQ_ENABLED",
        "PUBLIC_MARKET_CN_LOCAL_CACHE_ENABLED",
        "PUBLIC_MARKET_DATA_TIMEOUT_SECONDS",
        "PUBLIC_MARKET_DATA_ONLY",
        "TRADINGAGENTS_ENABLED",
        "TRADINGAGENTS_TIMEOUT_SECONDS",
        "TRADINGAGENTS_MAX_SYMBOLS",
        "TRADINGAGENTS_LLM_PROVIDER",
        "TRADINGAGENTS_DEEP_MODEL",
        "TRADINGAGENTS_QUICK_MODEL",
        "TRADINGAGENTS_OUTPUT_LANGUAGE",
        "TRADINGAGENTS_MAX_DEBATE_ROUNDS",
        "TRADINGAGENTS_MAX_RISK_DISCUSS_ROUNDS",
        "TRADINGAGENTS_CHECKPOINT_ENABLED",
        "TRADINGAGENTS_DATA_SOURCE",
        "TRADINGAGENTS_PUBLIC_MARKET_SOURCE",
        "TRADINGAGENTS_SCORE_WEIGHT",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "XAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENROUTER_API_KEY",
        "DASHSCOPE_API_KEY",
        "ZHIPUAI_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
    }
)

# 与 .env.example「性能与稳定性优化」一致；新用户或缺键时自动写入各用户 data/user_env/<用户>.env
# 用户文件中显式设置的值优先（覆盖下列默认）。
USER_ENV_PERFORMANCE_DEFAULTS: dict[str, str] = {
    "FEISHU_BOT_USE_API_PROXY": "true",
    "FEISHU_BOT_API_BASE_URL": "http://127.0.0.1:8010",
    "FEISHU_BOT_API_TIMEOUT_SECONDS": "8",
    "LONGPORT_WATCHDOG_HEALTH_TIMEOUT": "15",
    "LONGPORT_WATCHDOG_CONFIRM_TIMEOUT": "45",
    "LONGPORT_WATCHDOG_FAILS_BEFORE_RESTART": "40",
    "LONGPORT_WATCHDOG_RESTART_COOLDOWN": "300",
    "LONGPORT_WATCHDOG_STARTUP_GRACE": "120",
    "LONGPORT_CONNECTION_LIMIT": "15",
    "LONGPORT_HISTORY_MAX_CONCURRENCY": "3",
    "AUTO_TRADER_WORKER_USE_API_PROXY": "true",
    "AUTO_TRADER_API_BASE_URL": "http://127.0.0.1:8010",
    "AUTO_TRADER_API_PROXY_TIMEOUT_SECONDS": "15",
    "CORS_ALLOW_ORIGINS": "http://127.0.0.1:3000,http://localhost:3000",
    "LONGPORT_USE_SERVER_KLINE_CACHE": "1",
    "LONGPORT_DIRECT_FALLBACK": "0",
    "CN_MARKET_DATA_PROVIDER_ORDER": "mootdx,local_cache,akshare,tushare,baostock",
    "CN_MARKET_MOOTDX_ENABLED": "true",
    "CN_MARKET_TENCENT_ENABLED": "true",
    "PUBLIC_MARKET_DATA_PROVIDER_ORDER": "mootdx,eastmoney,akshare,cn_local_cache,yahoo,stooq",
    "PUBLIC_MARKET_MOOTDX_ENABLED": "true",
    "PUBLIC_MARKET_EASTMONEY_ENABLED": "true",
    "PUBLIC_MARKET_YAHOO_ENABLED": "true",
    "PUBLIC_MARKET_AKSHARE_ENABLED": "true",
    "PUBLIC_MARKET_STOOQ_ENABLED": "true",
    "PUBLIC_MARKET_CN_LOCAL_CACHE_ENABLED": "true",
    "PUBLIC_MARKET_DATA_TIMEOUT_SECONDS": "2.5",
    "PUBLIC_MARKET_DATA_ONLY": "false",
    "OPENBB_AUTO_START": "1",
    "TRADINGAGENTS_DATA_SOURCE": "auto",
    "TRADINGAGENTS_PUBLIC_MARKET_SOURCE": "auto",
}

_ROOT_ENV_STUB = """# Multi-Trading：API 密钥已按登录用户隔离，保存在 data/user_env/<用户名>.env
# 若你曾使用根目录 .env，升级时已将其迁移到用户 davies 的专属文件。
# 请勿在此文件存放密钥；通过 Web 设置页或编辑上述 per-user 文件配置。
"""


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def user_env_dir(root: Path | None = None) -> Path:
    return (root or project_root()) / "data" / "user_env"


def user_env_file_path(username: str, root: Path | None = None) -> Path:
    name = str(username or "").strip().lower()
    safe = quote(name, safe="")
    return user_env_dir(root) / f"{safe}.env"


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    try:
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            key = k.strip()
            if not key:
                continue
            out[key] = v.strip().strip("\"'")
    except Exception:
        return {}
    return out


def _write_env_file(path: Path, data: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={v}" for k, v in sorted(data.items())]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def migrate_legacy_root_env(root: Path | None = None) -> bool:
    """
    若根 .env 仍有键值且用户 davies 的专属文件不存在或为空，则迁移并写入根占位文件。
    返回是否执行了迁移写盘。
    """
    root = root or project_root()
    legacy = root / ".env"
    target = user_env_file_path(LEGACY_MIGRATION_USERNAME, root)
    legacy_vals = _parse_env_file(legacy)
    if not legacy_vals:
        return False
    existing = _parse_env_file(target)
    if existing:
        return False
    _write_env_file(target, legacy_vals)
    legacy.write_text(_ROOT_ENV_STUB, encoding="utf-8")
    return True


def load_user_env(username: str, root: Path | None = None) -> dict[str, str]:
    """仅读取磁盘上的用户文件，不注入默认、不写回。"""
    return _parse_env_file(user_env_file_path(username, root))


def resolve_user_env_with_defaults(username: str, root: Path | None = None) -> dict[str, str]:
    """
    合并性能/稳定性默认项与用户文件；若用户文件中缺少任一默认键，则写回磁盘（补全）。
    返回值用于 os.environ 与设置页加载。
    """
    root = root or project_root()
    path = user_env_file_path(username, root)
    raw = _parse_env_file(path)
    merged = {**USER_ENV_PERFORMANCE_DEFAULTS, **raw}
    if any(k not in raw for k in USER_ENV_PERFORMANCE_DEFAULTS):
        _write_env_file(path, merged)
    return merged


def save_user_env(username: str, data: dict[str, str], root: Path | None = None) -> None:
    _write_env_file(user_env_file_path(username, root), dict(data))


def merge_user_env_into_os_environ(username: str, root: Path | None = None) -> None:
    """将某用户文件写入 os.environ：托管键缺失则清空；文件中额外键也会写入（兼容未来变量）。"""
    root = root or project_root()
    data = resolve_user_env_with_defaults(username, root)
    for key in USER_ENV_MANAGED_KEYS:
        os.environ[key] = str(data.get(key, "") or "").strip()
    for k, v in data.items():
        if k not in USER_ENV_MANAGED_KEYS:
            os.environ[k] = str(v or "").strip()


def bootstrap_process_env_from_davies(root: Path | None = None) -> None:
    """进程启动：用 davies 用户文件填充托管环境变量（若文件不存在则清空托管键）。"""
    merge_user_env_into_os_environ(LEGACY_MIGRATION_USERNAME, root)


def apply_light_session_env_for_user(username: str, root: Path | None = None) -> None:
    """合并用户密钥到进程环境并刷新 live_settings / 默认账户镜像（不断开已有连接）。"""
    merge_user_env_into_os_environ(username, root)
    from config.live_settings import live_settings

    live_settings.__init__()
    try:
        from api import main as m

        if hasattr(m, "refresh_default_account_registry"):
            m.refresh_default_account_registry()
    except Exception:
        pass


def apply_full_session_env_for_user(username: str, root: Path | None = None) -> None:
    """保存设置或登录成功：在轻量同步基础上重置全局连接状态（与旧版保存 .env 行为一致）。"""
    apply_light_session_env_for_user(username, root)
    try:
        from api import main as m

        m.reset_contexts()
    except Exception:
        pass


def combined_env_for_cli(root: Path | None = None) -> dict[str, str]:
    """
    供 launcher 等无登录上下文使用：根 .env（多为占位）与 davies 用户文件合并，后者优先。
    """
    root = root or project_root()
    migrate_legacy_root_env(root)
    base = _parse_env_file(root / ".env")
    user = resolve_user_env_with_defaults(LEGACY_MIGRATION_USERNAME, root)
    merged = {**base, **user}
    return merged
