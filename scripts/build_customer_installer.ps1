param(
  [string]$Version = "1.0.0",
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
  @'
import importlib.util

required = ["akshare", "tushare", "baostock", "mootdx", "tdxpy", "prettytable"]
missing = [name for name in required if importlib.util.find_spec(name) is None]
if missing:
    raise SystemExit("missing customer data-source packages: " + ", ".join(missing))

for name in ["mcp", "google.genai"]:
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

Write-Host "[1/9] Cleaning old customer build..."
cmd.exe /c "taskkill /F /IM MultiTradingLauncher.exe >nul 2>nul"
cmd.exe /c "taskkill /F /IM Backend.exe >nul 2>nul"
cmd.exe /c "taskkill /F /IM CustomerLauncher.exe >nul 2>nul"
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
  Push-Location $frontendDir
  $env:MT_BUILD_TARGET = "customer"
  $env:NEXT_PUBLIC_MT_BUILD_TARGET = "customer"
  $env:NEXT_TELEMETRY_DISABLED = "1"
  npm install
  npm run build
  Pop-Location
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

Write-Host "[6/9] Bundling Node runtime..."
$nodePath = (Get-Command node.exe -ErrorAction Stop).Source
$nodeDestDir = Join-Path $appDir "runtime\node"
New-Item -ItemType Directory -Force -Path $nodeDestDir | Out-Null
Copy-Item -LiteralPath $nodePath -Destination (Join-Path $nodeDestDir "node.exe") -Force

Write-Host "[7/9] Writing customer config and Inno script..."
New-Item -ItemType Directory -Force -Path (Join-Path $appDir "data") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $appDir "logs") | Out-Null
Remove-Item -LiteralPath (Join-Path $appDir "data\user_env") -Recurse -Force -ErrorAction SilentlyContinue
@"
MT_BUILD_TARGET=customer
NEXT_PUBLIC_MT_BUILD_TARGET=customer
LONGPORT_API_PORT=8010
LONGPORT_WEB_PORT=3010
LOCAL_AGENT_ALLOW_USER_OWNERS=true

# Optional but recommended for customer payment orders:
# point this to your admin Next.js route or Convex public order route.
# BILLING_PUBLIC_ORDER_API_URL=https://your-admin-domain.example/api/billing/manual-orders

# Required after purchase: paste the public key used by the local backend to verify licenses.
# LOCAL_LICENSE_PUBLIC_KEY_PEM=-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----
"@ | Set-Content -Encoding UTF8 -Path (Join-Path $appDir ".env.example")

$billingPublicOrderApiUrl = [string]$env:BILLING_PUBLIC_ORDER_API_URL
if (-not [string]::IsNullOrWhiteSpace($billingPublicOrderApiUrl)) {
  @"
MT_BUILD_TARGET=customer
NEXT_PUBLIC_MT_BUILD_TARGET=customer
BILLING_PUBLIC_ORDER_API_URL=$billingPublicOrderApiUrl
"@ | Set-Content -Encoding UTF8 -Path (Join-Path $appDir ".env")
}

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
- Customers do not need to install Python, Node.js, or npm.
- To sync customer payment orders to the admin order center, configure BILLING_PUBLIC_ORDER_API_URL in .env.
"@ | Set-Content -Encoding UTF8 -Path (Join-Path $appDir "README_CUSTOMER.txt")

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
Source: "$appDirEsc\\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

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
