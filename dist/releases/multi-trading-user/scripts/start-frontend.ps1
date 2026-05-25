<#
.SYNOPSIS
  启动 Next.js 开发服务器（npm run dev），日志追加到 logs/frontend-*.log。
#>
param(
  [ValidateSet("user", "admin")]
  [string]$Edition = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$LogDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$ts = Get-Date -Format "yyyyMMdd-HHmmss"
$LogFile = Join-Path $LogDir "frontend-$ts.log"
Set-Location (Join-Path $Root "frontend")

Write-Host "Log: $LogFile"
$env:PORT = if ($env:PORT) { $env:PORT } else { "3010" }
if ($Edition) {
  $env:NEXT_PUBLIC_MT_EDITION = $Edition
}
$editionValue = if ($env:NEXT_PUBLIC_MT_EDITION) { $env:NEXT_PUBLIC_MT_EDITION } else { "user" }
Write-Host "Edition: $editionValue"
npm run dev 2>&1 | Tee-Object -FilePath $LogFile -Append
