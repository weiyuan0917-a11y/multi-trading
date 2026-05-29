# Engineering Health Checklist

This project is a local trading workstation. Optimize it in this order:

1. Test runnable state
2. Live-trading safety
3. Backend module boundaries
4. Sensitive-data hygiene
5. Dependency pinning
6. Documentation encoding
7. Frontend decomposition

## Local Verification

Use the project verification script before changing trading code:

```powershell
.\scripts\verify-local.ps1
```

If Python dependencies are missing, install the development set first:

```powershell
pip install -r requirements-dev.txt
```

The script uses `pytest` when available and falls back to `unittest discover`.
It also runs the frontend type check through `cmd /c npm run lint`, which avoids
PowerShell execution-policy failures on `npm.ps1`.

## Live-Trading Safety Switches

The shared broker submit path supports these environment variables:

- `TRADE_KILL_SWITCH=1`: block all live order submissions.
- `LIVE_TRADING_DISABLED=1`: alias for the kill switch.
- `TRADE_DRY_RUN=1`: return a synthetic `DRYRUN-*` order id without submitting.
- `LIVE_TRADING_DRY_RUN=1`: alias for dry run.
- `TRADE_IDEMPOTENCY_WINDOW_SECONDS=20`: block identical orders inside this window.
- `TRADE_AUDIT_LOG_FILE=logs/trade_audit.jsonl`: override the audit log path.

The default audit path is `logs/trade_audit.jsonl`.

## Sensitive Runtime Data

Do not commit local account, auth, PID, cache, ledger, or log files. The root
`.gitignore` covers the expected local paths, including `data/accounts/*.json`,
`data/auth/*.json`, strategy ledgers, K-line caches, local SQLite databases,
and generated logs.

## Backend Boundary Direction

Keep `api/main.py` as the application composition root. New business behavior
should live in `api/services/*`, `api/routers/*`, `api/engine/*`, or broker
adapters. Shared live-order checks belong in `api/services/trade_safety.py`.
