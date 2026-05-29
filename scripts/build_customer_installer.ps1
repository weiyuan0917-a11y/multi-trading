param(
  [string]$Version = "1.0.16",
  [string]$ReleaseRoot = "dist\customer",
  [switch]$SkipInno,
  [switch]$ReuseBackend,
  [switch]$ReuseFrontend,
  [switch]$SkipSetupExe
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repo

function Copy-Dir($Source, $Destination) {
  if (Test-Path $Destination) {
    Remove-Item -LiteralPath $Destination -Recurse -Force
  }
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
  Copy-Item -LiteralPath $Source -Destination $Destination -Recurse -Force
}

function Require-File($Path, $Message) {
  if (-not (Test-Path $Path)) {
    throw "${Message}: $Path"
  }
}

function Stop-ListenersOnPort([int]$Port) {
  try {
    $connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($conn in $connections) {
      if (-not $conn.OwningProcess) { continue }
      $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
      if (-not $proc) { continue }
      Write-Host "[INFO] Stopping process on port ${Port}: $($proc.ProcessName) ($($proc.Id))"
      Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    }
  } catch {
    Write-Host "[WARN] Could not inspect/stop listeners on port ${Port}: $($_.Exception.Message)"
  }
}

function Assert-NotExists($Path, $Message) {
  if (Test-Path $Path) {
    throw "${Message}: $Path"
  }
}

function Resolve-LicensePublicKeyPath {
  foreach ($candidate in @(
    (Join-Path $repo ".secrets\local_license_public.pem"),
    (Join-Path $repo "config\local_license_public.pem")
  )) {
    if (Test-Path $candidate) {
      return (Resolve-Path $candidate).Path
    }
  }
  foreach ($envName in @("LOCAL_LICENSE_PUBLIC_KEY_PATH", "CONVEX_LOCAL_LICENSE_PUBLIC_KEY_PATH")) {
    $raw = [string][Environment]::GetEnvironmentVariable($envName)
    if ([string]::IsNullOrWhiteSpace($raw)) { continue }
    if (Test-Path $raw) {
      return (Resolve-Path $raw).Path
    }
  }
  throw "License public key was not found. Expected .secrets\local_license_public.pem or LOCAL_LICENSE_PUBLIC_KEY_PATH."
}

function Invoke-WithCustomerFrontendExclusions([scriptblock]$Action) {
  $stashRoot = Join-Path $repo ".customer-build-excluded"
  $moved = @()
  $exclusions = @(
    @{ Source = (Join-Path $frontendDir "app\admin"); Stash = (Join-Path $stashRoot "app-admin"); Label = "app\admin" },
    @{ Source = (Join-Path $frontendDir "app\api\admin"); Stash = (Join-Path $stashRoot "app-api-admin"); Label = "app\api\admin" }
  )
  if (Test-Path $stashRoot) {
    Remove-Item -LiteralPath $stashRoot -Recurse -Force
  }
  try {
    foreach ($item in $exclusions) {
      if (-not (Test-Path $item.Source)) { continue }
      New-Item -ItemType Directory -Force -Path $stashRoot | Out-Null
      Move-Item -LiteralPath $item.Source -Destination $item.Stash
      $moved += $item
      Write-Host "[INFO] Customer frontend excludes admin routes: $($item.Label)"
    }
    & $Action
  } finally {
    foreach ($item in $moved) {
      if (-not (Test-Path $item.Stash)) { continue }
      New-Item -ItemType Directory -Force -Path (Split-Path -Parent $item.Source) | Out-Null
      Move-Item -LiteralPath $item.Stash -Destination $item.Source
    }
    if (Test-Path $stashRoot) {
      Remove-Item -LiteralPath $stashRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
  }
}

function Assert-CustomerPackageContentsLegacy {
  $frontendOut = Join-Path $appDir "frontend"
  Assert-NotExists (Join-Path $frontendOut ".next\server\app\admin") "Customer package must not include admin pages"
  Assert-NotExists (Join-Path $frontendOut ".next\server\app\api\admin") "Customer package must not include admin API routes"

  $staticMatches = @()
  if (Test-Path (Join-Path $frontendOut ".next")) {
    $staticMatches = Get-ChildItem -LiteralPath (Join-Path $frontendOut ".next") -Recurse -File -ErrorAction SilentlyContinue |
      Where-Object {
        $_.FullName -match "admin[\\/](orders|licenses)" -or
        $_.Name -match "admin.*(orders|licenses)" -or
        $_.Name -match "(orders|licenses).*admin"
      }
  }
  if ($staticMatches.Count -gt 0) {
    throw "Customer package contains admin orders/license assets: $($staticMatches[0].FullName)"
  }

  $quoteCollectorHit = $false
  $searchRoots = @(
    Join-Path $frontendOut ".next",
    Join-Path $frontendOut "server.js"
  )
  foreach ($rootPath in $searchRoots) {
    if (-not (Test-Path $rootPath)) { continue }
    $hits = Select-String -Path $rootPath -Pattern "quote-collector|真实报价采集|QQQ 0DTE" -SimpleMatch -Recurse -ErrorAction SilentlyContinue
    if ($hits) {
      $quoteCollectorHit = $true
      break
    }
  }
  if (-not $quoteCollectorHit) {
    throw "Customer package frontend does not contain QQQ 0DTE quote collector UI."
  }

  $tradeConfirmationHit = $false
  $tradeChunkRoots = @(
    (Join-Path $frontendOut ".next\server\chunks\ssr"),
    (Join-Path $frontendOut ".next\server\app\trade"),
    (Join-Path $frontendOut ".next\static\chunks")
  )
  foreach ($rootPath in $tradeChunkRoots) {
    if (-not (Test-Path $rootPath)) { continue }
    $files = Get-ChildItem -LiteralPath $rootPath -Recurse -File -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -match "trade|app_trade_page" -or $_.FullName -match "[\\/]trade[\\/]" }
    $hits = $files | Select-String -Pattern "confirmation_token" -SimpleMatch -ErrorAction SilentlyContinue
    if ($hits) {
      $tradeConfirmationHit = $true
      break
    }
  }
  if (-not $tradeConfirmationHit) {
    throw "Customer package frontend does not contain stock trade confirmation_token UI."
  }
}

function Assert-CustomerPackageContents {
  $frontendOut = Join-Path $appDir "frontend"
  Assert-NotExists (Join-Path $frontendOut ".next\server\app\admin") "Customer package must not include admin pages"
  Assert-NotExists (Join-Path $frontendOut ".next\server\app\api\admin") "Customer package must not include admin API routes"

  $adminMatches = @()
  if (Test-Path (Join-Path $frontendOut ".next")) {
    $adminMatches = Get-ChildItem -LiteralPath (Join-Path $frontendOut ".next") -Recurse -File -ErrorAction SilentlyContinue |
      Where-Object {
        $_.FullName -match "admin[\\/](orders|licenses)" -or
        $_.FullName -match "app[\\/]api[\\/]admin" -or
        $_.Name -match "admin.*(orders|licenses)" -or
        $_.Name -match "(orders|licenses).*admin"
      }
  }
  if ($adminMatches.Count -gt 0) {
    throw "Customer package contains admin orders/license assets: $($adminMatches[0].FullName)"
  }

  $quoteCollectorHit = $false
  foreach ($rootPath in @((Join-Path $frontendOut ".next"), (Join-Path $frontendOut "server.js"))) {
    if (-not (Test-Path $rootPath)) { continue }
    $rootItem = Get-Item -LiteralPath $rootPath
    $files = if ($rootItem.PSIsContainer) {
      Get-ChildItem -LiteralPath $rootPath -Recurse -File -ErrorAction SilentlyContinue
    } else {
      @($rootItem)
    }
    $hits = $files | Select-String -Pattern "quote-collector", "QQQ 0DTE" -SimpleMatch -ErrorAction SilentlyContinue
    if ($hits) {
      $quoteCollectorHit = $true
      break
    }
  }
  if (-not $quoteCollectorHit) {
    throw "Customer package frontend does not contain QQQ 0DTE quote collector UI."
  }

  $tradeConfirmationHit = $false
  $tradeChunkRoots = @(
    (Join-Path $frontendOut ".next\server\chunks\ssr"),
    (Join-Path $frontendOut ".next\server\app\trade"),
    (Join-Path $frontendOut ".next\static\chunks")
  )
  foreach ($rootPath in $tradeChunkRoots) {
    if (-not (Test-Path $rootPath)) { continue }
    $files = Get-ChildItem -LiteralPath $rootPath -Recurse -File -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -match "trade|app_trade_page" -or $_.FullName -match "[\\/]trade[\\/]" }
    $hits = $files | Select-String -Pattern "confirmation_token" -SimpleMatch -ErrorAction SilentlyContinue
    if ($hits) {
      $tradeConfirmationHit = $true
      break
    }
  }
  if (-not $tradeConfirmationHit) {
    throw "Customer package frontend does not contain stock trade confirmation_token UI."
  }
}

function Copy-CustomerKlineSeedCaches {
  $sourceDir = Join-Path $repo "data\klines"
  $destDir = Join-Path $appDir "data\klines"
  New-Item -ItemType Directory -Force -Path $destDir | Out-Null
  if (-not (Test-Path $sourceDir)) {
    Write-Host "[WARN] K-line seed cache source is missing: $sourceDir"
    return
  }

  $seedFiles = @(
    "QQQ_US__1m__d60.json",
    "QQQ_US__1m__d120.json",
    "QQQ_US__1m__d180.json",
    "QQQ_US__1d__d60.json",
    "QQQ_US__1d__d120.json",
    "QQQ_US__1d__d180.json",
    "QQQ_US__1d__d260.json"
  )
  $copied = 0
  foreach ($name in $seedFiles) {
    $src = Join-Path $sourceDir $name
    if (-not (Test-Path $src)) {
      Write-Host "[WARN] K-line seed cache not found: $name"
      continue
    }
    Copy-Item -LiteralPath $src -Destination (Join-Path $destDir $name) -Force
    $copied += 1
  }
  Require-File (Join-Path $destDir "QQQ_US__1m__d180.json") "Customer package must include QQQ 1m Lab seed cache"
  Write-Host "[INFO] Included $copied QQQ K-line seed cache file(s)."
}

function Find-Iscc {
  try {
    return (Get-Command ISCC.exe -ErrorAction Stop).Source
  } catch {}
  foreach ($candidate in @(
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe"
  )) {
    if (Test-Path $candidate) {
      return $candidate
    }
  }
  return $null
}

function Install-CustomerPythonDependencies {
  Write-Host "[INFO] Installing customer data-source dependencies..."
  python -m pip install --upgrade pyinstaller | Write-Host
  python -m pip install --upgrade akshare tushare baostock | Write-Host
  python -m pip install --upgrade mootdx | Write-Host
  Write-Host "[INFO] Installing TradingAgents runtime dependencies for customer Backend.exe..."
  python -m pip install --upgrade -r (Join-Path $repo "requirements-tradingagents.txt") | Write-Host
  python -m pip install --upgrade "httpx>=0.28.1" "tenacity>=9.1.4" | Write-Host
@'
import importlib.util

required = [
    "akshare",
    "tushare",
    "baostock",
    "mootdx",
    "tdxpy",
    "prettytable",
    "tradingagents",
    "langgraph",
    "langchain_core",
    "langchain_openai",
    "openai",
]
missing = [name for name in required if importlib.util.find_spec(name) is None]
if missing:
    raise SystemExit("missing customer data-source packages: " + ", ".join(missing))

for name in ["mcp"]:
    if importlib.util.find_spec(name) is not None:
        print(f"[INFO] {name} is installed in the build env but excluded from the customer Backend.exe")
'@ | python -
}

function Restore-DeveloperPythonDependencies {
  Write-Host "[INFO] Restoring developer MCP / google-genai compatible dependency pins..."
  python -m pip install --upgrade "httpx>=0.28.1" "tenacity>=9.1.4" | Write-Host
}

$releaseDir = Join-Path $repo $ReleaseRoot
$appDir = Join-Path $releaseDir "MultiTrading"
$frontendDir = Join-Path $repo "frontend"
$standalone = Join-Path $frontendDir ".next\standalone"
$static = Join-Path $frontendDir ".next\static"
$public = Join-Path $frontendDir "public"
$launcherIconPath = Join-Path $repo "assets\windows\multitrading-logo.ico"
$licensePublicKeyPath = Resolve-LicensePublicKeyPath

Write-Host "[1/9] Cleaning old customer build..."
cmd.exe /c "taskkill /F /IM MultiTradingLauncher.exe >nul 2>nul"
cmd.exe /c "taskkill /F /IM Backend.exe >nul 2>nul"
cmd.exe /c "taskkill /F /IM CustomerLauncher.exe >nul 2>nul"
Stop-ListenersOnPort 3010
Stop-ListenersOnPort 8010
if (Test-Path $releaseDir) {
  Remove-Item -LiteralPath $releaseDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $appDir | Out-Null
Remove-Item -LiteralPath (Join-Path $repo "dist\customer-launcher") -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "[2/9] Checking build tools..."
$customerPythonDepsInstalled = $false
if (-not $ReuseBackend) {
  Install-CustomerPythonDependencies
  $customerPythonDepsInstalled = $true
}
python (Join-Path $repo "scripts\make_windows_icon.py")

Write-Host "[3/9] Building customer frontend (Next standalone)..."
$standaloneServer = Join-Path $standalone "server.js"
if ($ReuseFrontend -and (Test-Path $standaloneServer) -and (Test-Path $static)) {
  Write-Host "[INFO] Reusing existing frontend\.next\standalone"
} else {
  Invoke-WithCustomerFrontendExclusions {
    Push-Location $frontendDir
    try {
      $env:MT_BUILD_TARGET = "customer"
      $env:NEXT_PUBLIC_MT_BUILD_TARGET = "customer"
      $env:NEXT_TELEMETRY_DISABLED = "1"
      Remove-Item -LiteralPath (Join-Path $frontendDir ".next") -Recurse -Force -ErrorAction SilentlyContinue
      Remove-Item -LiteralPath (Join-Path $frontendDir "tsconfig.tsbuildinfo") -Force -ErrorAction SilentlyContinue
      if (Test-Path (Join-Path $frontendDir ".next\dev")) {
        throw "frontend .next\dev cache is still present; stop the dev server before customer build."
      }
      npm install
      npm run build
    } finally {
      Pop-Location
    }
  }
}

Write-Host "[4/9] Building backend and launcher executables..."
$env:MT_BUILD_TARGET = "customer"
if ($ReuseBackend -and (Test-Path (Join-Path $repo "dist\Backend.exe"))) {
  Write-Host "[INFO] Reusing existing dist\Backend.exe"
} else {
  try {
    python -m PyInstaller --noconfirm Backend.spec
  } finally {
    if ($customerPythonDepsInstalled) {
      Restore-DeveloperPythonDependencies
    }
  }
}
Push-Location (Join-Path $repo "launcher_customer_go")
$env:GOOS = "windows"
$env:GOARCH = "amd64"
$env:CGO_ENABLED = "0"
go build -trimpath -ldflags "-s -w -H=windowsgui" -o (Join-Path $repo "dist\customer-launcher\MultiTradingLauncher.exe") .
Pop-Location
$launcherExe = Join-Path $repo "dist\customer-launcher\MultiTradingLauncher.exe"
if (Test-Path $launcherIconPath) {
  python (Join-Path $repo "scripts\apply_windows_icon.py") --exe $launcherExe --ico $launcherIconPath
}

Write-Host "[5/9] Assembling source-less customer runtime..."
Require-File (Join-Path $repo "dist\Backend.exe") "Backend.exe was not built"
Require-File (Join-Path $repo "dist\customer-launcher\MultiTradingLauncher.exe") "MultiTradingLauncher.exe was not built"
Require-File $standaloneServer "Next standalone server.js was not built"

Copy-Item -LiteralPath (Join-Path $repo "dist\Backend.exe") -Destination (Join-Path $appDir "Backend.exe") -Force
Copy-Item -LiteralPath (Join-Path $repo "dist\customer-launcher\MultiTradingLauncher.exe") -Destination (Join-Path $appDir "MultiTradingLauncher.exe") -Force

Copy-Dir $standalone (Join-Path $appDir "frontend")
foreach ($sourceDir in @("app", "components", "lib", "convex")) {
  Remove-Item -LiteralPath (Join-Path $appDir "frontend\$sourceDir") -Recurse -Force -ErrorAction SilentlyContinue
}
foreach ($sourceFile in @("next.config.js", "package-lock.json", "package.json", "postcss.config.js", "proxy.ts", "tailwind.config.js", "tsconfig.json", "tsconfig.tsbuildinfo")) {
  Remove-Item -LiteralPath (Join-Path $appDir "frontend\$sourceFile") -Force -ErrorAction SilentlyContinue
}
Copy-Dir $static (Join-Path $appDir "frontend\.next\static")
Copy-Dir $public (Join-Path $appDir "frontend\public")
Assert-CustomerPackageContents

Write-Host "[6/9] Bundling Node runtime..."
$nodePath = (Get-Command node.exe -ErrorAction Stop).Source
$nodeDestDir = Join-Path $appDir "runtime\node"
New-Item -ItemType Directory -Force -Path $nodeDestDir | Out-Null
Copy-Item -LiteralPath $nodePath -Destination (Join-Path $nodeDestDir "node.exe") -Force

Write-Host "[7/9] Writing customer config and Inno script..."
New-Item -ItemType Directory -Force -Path (Join-Path $appDir "data") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $appDir "data\option_quotes") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $appDir "data\qqq_0dte_quote_collector") | Out-Null
Copy-CustomerKlineSeedCaches
New-Item -ItemType Directory -Force -Path (Join-Path $appDir "logs") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $appDir "config") | Out-Null
Copy-Item -LiteralPath $licensePublicKeyPath -Destination (Join-Path $appDir "config\local_license_public.pem") -Force
Remove-Item -LiteralPath (Join-Path $appDir "data\user_env") -Recurse -Force -ErrorAction SilentlyContinue
@"
MT_BUILD_TARGET=customer
NEXT_PUBLIC_MT_BUILD_TARGET=customer
LONGPORT_API_PORT=8010
LONGPORT_WEB_PORT=3010
LOCAL_AGENT_ALLOW_USER_OWNERS=true
LOCAL_LICENSE_PUBLIC_KEY_PATH=config\local_license_public.pem
LOCAL_LICENSE_ALLOW_UNSIGNED=false
"@ | Set-Content -Encoding UTF8 -Path (Join-Path $appDir ".env")

@"
MT_BUILD_TARGET=customer
NEXT_PUBLIC_MT_BUILD_TARGET=customer
LONGPORT_API_PORT=8010
LONGPORT_WEB_PORT=3010
LOCAL_AGENT_ALLOW_USER_OWNERS=true
LOCAL_LICENSE_PUBLIC_KEY_PATH=config\local_license_public.pem
LOCAL_LICENSE_ALLOW_UNSIGNED=false
"@ | Set-Content -Encoding UTF8 -Path (Join-Path $appDir ".env.example")

@"
MultiTrading Customer Edition

How to start:
1. Double-click MultiTradingLauncher.exe.
2. The browser opens http://127.0.0.1:3010.
3. Configure broker, market data, Feishu and other local settings in Setup.
4. Import the local License issued by the administrator in Personal Center.

Notes:
- This customer package does not expose source files, payment-order admin, or license-issuing admin pages.
- Local data is stored under the installed data and logs folders.
- QQQ 0DTE quote snapshots are stored under data\option_quotes by default.
- License verification uses config\local_license_public.pem.
- Customers do not need to install Python, Node.js, or npm.
"@ | Set-Content -Encoding UTF8 -Path (Join-Path $appDir "README_CUSTOMER.txt")

@{
  version = $Version
  built_at = (Get-Date).ToUniversalTime().ToString("o")
  source_root = $repo
  customer_exclusions = @(
    "admin_order_pages",
    "admin_license_pages",
    "admin_manual_order_api_routes",
    "admin_license_delivery_api_routes"
  )
  included_features = @(
    "qqq_0dte_quote_collector",
    "qqq_0dte_quote_collector_frontend_panel",
    "rsa_license_verification_public_key"
  )
} | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 -Path (Join-Path $appDir "customer_build_manifest.json")

$issPath = Join-Path $releaseDir "MultiTradingCustomer.iss"
$appDirEsc = $appDir.Replace("\", "\\")
@"
#define MyAppName "MultiTrading"
#define MyAppVersion "$Version"
#define MyAppPublisher "MultiTrading"

[Setup]
AppId={{E21D9D82-0B3A-4AA8-9D0E-MULTITRADING}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\MultiTrading
DefaultGroupName=MultiTrading
DisableProgramGroupPage=yes
OutputDir=$($releaseDir.Replace("\", "\\"))
OutputBaseFilename=MultiTradingSetup-$Version
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
UninstallDisplayIcon={app}\MultiTradingLauncher.exe

[Files]
Source: "$appDirEsc\\*"; DestDir: "{app}"; Excludes: "frontend\.next\server\app\admin\*,frontend\.next\server\app\api\admin\*,frontend\.next\static\chunks\app\admin\*,frontend\.next\static\chunks\app\api\admin\*,frontend\app\admin\*,frontend\app\api\admin\*"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Icons]
Name: "{group}\MultiTrading"; Filename: "{app}\MultiTradingLauncher.exe"
Name: "{commondesktop}\MultiTrading"; Filename: "{app}\MultiTradingLauncher.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\MultiTradingLauncher.exe"; Description: "Launch MultiTrading"; Flags: nowait postinstall skipifsilent
"@ | Set-Content -Encoding UTF8 -Path $issPath

Write-Host "[8/9] Building self-contained customer setup exe..."
if ($SkipSetupExe) {
  Write-Host "[INFO] Skipped embedded setup exe build."
} else {
  $installerDir = Join-Path $repo "installer_customer_go"
  $payloadZip = Join-Path $installerDir "payload.zip"
  New-Item -ItemType Directory -Force -Path $installerDir | Out-Null
  Remove-Item -LiteralPath $payloadZip -Force -ErrorAction SilentlyContinue
  Compress-Archive -Path (Join-Path $appDir "*") -DestinationPath $payloadZip -Force
  Push-Location $installerDir
  $env:GOOS = "windows"
  $env:GOARCH = "amd64"
  $env:CGO_ENABLED = "0"
  go build -trimpath -ldflags "-s -w -H=windowsgui" -o (Join-Path $releaseDir "MultiTradingSetup-$Version.exe") .
  Pop-Location
}

Write-Host "[9/9] Building Inno installer if available..."
$iscc = Find-Iscc
if ($SkipInno -or -not $iscc) {
  Write-Host "[WARN] Inno Setup ISCC.exe not found or skipped. Inno script is ready:"
  Write-Host "       $issPath"
} else {
  & $iscc $issPath
}

Write-Host "[OK] Customer runtime:"
Write-Host "     $appDir"
if (Test-Path (Join-Path $releaseDir "MultiTradingSetup-$Version.exe")) {
  Write-Host "[OK] One-click setup exe:"
  Write-Host "     $(Join-Path $releaseDir "MultiTradingSetup-$Version.exe")"
}
