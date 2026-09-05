"""Shared API start settings for the Community edition."""
DEFAULT_API_PORT = 8010
SCRIPT_DEV_HOST = "127.0.0.1"
SCRIPT_SMALL_PROD_HOST = "127.0.0.1"


def build_uvicorn_argv(host: str, port: int, *, reload: bool = False) -> list[str]:
    argv = ["-m", "uvicorn", "api.main:app", "--host", host, "--port", str(port)]
    if reload:
        argv.append("--reload")
    return argv
