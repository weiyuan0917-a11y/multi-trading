param(
    [switch]$SkipFrontend
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Find-Python {
    $candidates = @(
        Join-Path $Root ".venv\Scripts\python.exe",
        Join-Path $Root ".openbb-venv\Scripts\python.exe",
        "python",
        "py"
    )
    foreach ($candidate in $candidates) {
        try {
            $null = & $candidate --version 2>$null
            if ($LASTEXITCODE -eq 0) {
                return $candidate
            }
        } catch {
        }
    }
    throw "No usable Python interpreter found. Recreate .venv or install Python 3.12+."
}

$Python = Find-Python
Write-Host "[verify] Python: $Python"
& $Python -m compileall -q api mcp_server tests launcher.py runtime_process_utils.py backend_uvicorn_spec.py

$missing = @()
foreach ($module in @("fastapi", "httpx", "longbridge", "pydantic", "tzdata")) {
    & $Python -c "import $module" 2>$null
    if ($LASTEXITCODE -ne 0) {
        $missing += $module
    }
}
if ($missing.Count -gt 0) {
    Write-Host "[verify] Missing Python modules: $($missing -join ', ')"
    Write-Host "[verify] Install dependencies with: pip install -r requirements-dev.txt"
    exit 1
}

$pytestAvailable = $false
try {
    & $Python -c "import pytest" 2>$null
    $pytestAvailable = ($LASTEXITCODE -eq 0)
} catch {
    $pytestAvailable = $false
}

if ($pytestAvailable) {
    Write-Host "[verify] Running pytest"
    & $Python -m pytest -q
} else {
    Write-Host "[verify] pytest not installed; falling back to unittest discover"
    & $Python -m unittest discover -s tests -v
}

if (-not $SkipFrontend) {
    Write-Host "[verify] Frontend typecheck"
    Push-Location (Join-Path $Root "frontend")
    try {
        & cmd /c npm run lint
    } finally {
        Pop-Location
    }
}
