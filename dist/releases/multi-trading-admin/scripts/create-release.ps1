param(
  [ValidateSet("user", "admin")]
  [string]$Edition = "user",
  [string]$OutputRoot = ""
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = [System.IO.Path]::GetFullPath((Join-Path $ScriptDir ".."))
if (-not $OutputRoot) {
  $OutputRoot = Join-Path $Root "dist\releases"
}
$OutputRootFull = [System.IO.Path]::GetFullPath($OutputRoot)
$Target = [System.IO.Path]::GetFullPath((Join-Path $OutputRootFull "multi-trading-$Edition"))

if (-not $Target.StartsWith($OutputRootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "Refusing to write outside OutputRoot: $Target"
}
if ($OutputRootFull -eq $Root -or $Target -eq $Root) {
  throw "Refusing to overwrite repository root"
}

if (Test-Path -LiteralPath $Target) {
  Remove-Item -LiteralPath $Target -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $Target | Out-Null

$excludeDirs = @(
  ".git",
  ".venv",
  "venv",
  "node_modules",
  "frontend\node_modules",
  "frontend\.next",
  "frontend\out",
  "dist",
  "build",
  "logs",
  ".secrets",
  "data\user_env",
  "data\auth",
  "data\accounts",
  "data\klines"
)

$excludeFiles = @(
  ".env",
  ".env.local",
  "frontend\.env.local",
  "*.pid",
  "*.log",
  "*.jsonl",
  "*.sqlite",
  "*.db"
)

$robocopyArgs = @(
  $Root,
  $Target,
  "/E",
  "/XD"
) + ($excludeDirs | ForEach-Object { Join-Path $Root $_ }) + @(
  "/XF"
) + $excludeFiles + @(
  "/NFL",
  "/NDL",
  "/NJH",
  "/NJS",
  "/NP"
)

& robocopy @robocopyArgs | Out-Null
if ($LASTEXITCODE -gt 7) {
  throw "robocopy failed with exit code $LASTEXITCODE"
}

if ($Edition -eq "user") {
  $adminOnlyPaths = @(
    "frontend\app\admin",
    "frontend\app\api\admin",
    "frontend\app\api\billing",
    "frontend\convex",
    "frontend\.convex"
  )
  foreach ($relative in $adminOnlyPaths) {
    $path = Join-Path $Target $relative
    if (Test-Path -LiteralPath $path) {
      Remove-Item -LiteralPath $path -Recurse -Force
    }
  }
}

$editionFile = Join-Path $Target "RELEASE_EDITION.txt"
$lines = @(
  "edition=$Edition",
  "created_at=$(Get-Date -Format o)",
  "source=$Root"
)
Set-Content -LiteralPath $editionFile -Encoding UTF8 -Value $lines

Write-Host "Created $Edition release package:"
Write-Host $Target
