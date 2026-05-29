# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


def collect_submodules_filtered(package, excluded_prefixes=()):
    try:
        modules = collect_submodules(package)
    except Exception:
        return []
    out = []
    for module in modules:
        if any(module == prefix or module.startswith(prefix + ".") for prefix in excluded_prefixes):
            continue
        out.append(module)
    return out

hiddenimports = []
for package in ("api", "config", "longbridge", "uvicorn", "fastapi"):
    hiddenimports += collect_submodules_filtered(package)

# The backend uses several utility modules under mcp_server for strategy,
# backtest, fee, and risk logic. It does not need the standalone MCP stdio
# server modules in the customer executable; excluding them keeps the package
# compatible with mootdx's older httpx/tenacity constraints.
hiddenimports += collect_submodules_filtered(
    "mcp_server",
    excluded_prefixes=(
        "mcp_server.broker_mcp_server",
        "mcp_server.longport_mcp_server",
        "mcp_server.mcp_extensions",
        "mcp_server.market_mcp_tools",
        "mcp_server.notification_mcp_tools",
    ),
)

for package in (
    "mootdx",
    "tdxpy",
    "prettytable",
    "py_mini_racer",
    "akshare",
    "baostock",
    "tushare",
):
    hiddenimports += collect_submodules_filtered(package)

hiddenimports += [
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]

datas = []
for package in ("api", "config", "mcp_server", "mootdx", "tdxpy", "prettytable", "py_mini_racer", "akshare", "baostock", "tushare"):
    try:
        datas += collect_data_files(package)
    except Exception:
        pass


a = Analysis(
    ["backend_entry.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "frontend",
        "convex",
        "pytest",
        "tests",
        "mcp",
        "google.genai",
        "google_genai",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
