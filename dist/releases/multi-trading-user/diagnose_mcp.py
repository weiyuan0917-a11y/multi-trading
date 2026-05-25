"""
MCP connection diagnostic for the current Multi-Trading workspace.

This probes the same stdio JSON-RPC flow used by OpenClaw/Cursor:
initialize -> tools/list -> tools/call(list_strategies).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MCP_DIR = ROOT / "mcp_server"
CONFIG_PATH = MCP_DIR / "mcp_config.json"
SERVER_PATH = MCP_DIR / "broker_mcp_server.py"
DEFAULT_PYTHON = Path(r"C:\Users\17852\AppData\Local\Python\bin\python.exe")


def _load_server_config() -> dict:
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        servers = cfg.get("mcpServers") if isinstance(cfg, dict) else {}
        broker = servers.get("broker-trading") if isinstance(servers, dict) else {}
        return broker if isinstance(broker, dict) else {}
    except Exception:
        return {}


def _print_check(ok: bool, label: str, detail: str = "") -> None:
    mark = "[OK]" if ok else "[ERR]"
    suffix = f" - {detail}" if detail else ""
    print(f"{mark} {label}{suffix}")


def _jsonrpc_probe(command: str, args: list[str], env: dict[str, str]) -> tuple[int, list[dict], str]:
    payload = "\n".join(
        [
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "diagnose_mcp", "version": "1.0"},
                    },
                }
            ),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "list_strategies", "arguments": {}},
                }
            ),
            "",
        ]
    )
    proc = subprocess.run(
        [command, *args],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ROOT),
        env=env,
        timeout=30,
        check=False,
    )
    messages: list[dict] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            messages.append({"parse_error": line})
            continue
        if isinstance(obj, dict):
            messages.append(obj)
    return proc.returncode, messages, proc.stderr or ""


def main() -> int:
    print("=" * 60)
    print("MCP Server Diagnostic")
    print("=" * 60)

    cfg = _load_server_config()
    command = str(cfg.get("command") or DEFAULT_PYTHON)
    args = [str(x) for x in (cfg.get("args") or [str(SERVER_PATH)])]
    env = os.environ.copy()
    for k, v in (cfg.get("env") or {}).items():
        env[str(k)] = str(v)
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.setdefault("MCP_SERVER_NAME", "broker-trading")
    env.setdefault("OPENCLAW_MCP_TOOL_COMPAT", "standard")
    env.setdefault("OPENCLAW_MCP_SINGLE_INSTANCE", "false")
    env.setdefault("OPENCLAW_MCP_START_BACKGROUND_ON_TOOL_CALL", "false")

    print("\n[1/4] Files")
    _print_check(SERVER_PATH.exists(), "broker_mcp_server.py", str(SERVER_PATH))
    _print_check(CONFIG_PATH.exists(), "mcp_config.json", str(CONFIG_PATH))

    print("\n[2/4] Python")
    try:
        py = subprocess.run(
            [command, "-c", "import sys; import mcp; print(sys.executable)"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        _print_check(py.returncode == 0, "Python can import mcp", (py.stdout or py.stderr).strip())
    except Exception as exc:
        _print_check(False, "Python can import mcp", str(exc))
        return 1

    print("\n[3/4] JSON-RPC probe")
    try:
        rc, messages, stderr = _jsonrpc_probe(command, args, env)
    except Exception as exc:
        _print_check(False, "stdio probe", str(exc))
        return 1
    _print_check(rc == 0, "process exit", f"code={rc}")
    _print_check(len(messages) >= 3, "JSON-RPC responses", f"{len(messages)} message(s)")
    if stderr.strip():
        print("[WARN] stderr output detected:")
        print(stderr.strip()[:2000])

    init = next((m for m in messages if m.get("id") == 1), {})
    tools_msg = next((m for m in messages if m.get("id") == 2), {})
    call_msg = next((m for m in messages if m.get("id") == 3), {})
    tools = (((tools_msg.get("result") or {}).get("tools")) or [])
    _print_check(bool(init.get("result")), "initialize")
    _print_check(isinstance(tools, list) and len(tools) > 0, "tools/list", f"{len(tools)} tool(s)")
    _print_check(bool((call_msg.get("result") or {}).get("content")), "tools/call list_strategies")

    print("\n[4/4] Summary")
    if rc == 0 and init.get("result") and tools and (call_msg.get("result") or {}).get("content"):
        print("[OK] MCP basic flow is healthy.")
        return 0
    print("[ERR] MCP basic flow failed. Check the details above.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
