<#
.SYNOPSIS
  启动 Next.js 开发服务器（npm run dev），日志追加到 logs/frontend-*.log。
#>
param(
  [switch]$Dev,
  [ValidateRange(512, 4096)]
  [int]$NodeMemoryMb = 1536
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
if ($env:NODE_OPTIONS -notmatch "--max-old-space-size") {
  $env:NODE_OPTIONS = (($env:NODE_OPTIONS + " --max-old-space-size=$NodeMemoryMb").Trim())
}
$script = if (-not $Dev -and (Test-Path ".next\BUILD_ID")) { "start" } else { "dev" }
Write-Host "Frontend mode: $script; Node memory limit: ${NodeMemoryMb}MB"
npm run $script 2>&1 | Tee-Object -FilePath $LogFile -Append
