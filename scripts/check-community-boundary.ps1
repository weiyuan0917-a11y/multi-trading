[CmdletBinding()]
param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [switch]$Strict
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path -LiteralPath $Root).Path

$proOnlyPaths = @(
    @{ Path = "api/brokers"; Category = "真实券商适配器" },
    @{ Path = "api/services/account_registry.py"; Category = "账户注册" },
    @{ Path = "api/services/broker_context.py"; Category = "券商上下文" },
    @{ Path = "api/services/broker_client_service.py"; Category = "券商客户端" },
    @{ Path = "api/services/credential_vault.py"; Category = "凭证保管" },
    @{ Path = "api/services/trade_permissions.py"; Category = "实盘权限" },
    @{ Path = "api/routers/options_trade.py"; Category = "真实交易路由" },
    @{ Path = "api/routers/setup.py"; Category = "券商 Setup 路由" },
    @{ Path = "api/routers/license.py"; Category = "Pro 授权路由" },
    @{ Path = "mcp_server/broker_mcp_server.py"; Category = "券商 MCP" },
    @{ Path = "mcp_server/longport_mcp_server.py"; Category = "券商 MCP" },
    @{ Path = "mcp_server/options_service.py"; Category = "期权订单服务" },
    @{ Path = "frontend/app/setup"; Category = "券商凭证 UI" },
    @{ Path = "frontend/app/trade"; Category = "真实交易 UI" },
    @{ Path = "frontend/app/options"; Category = "期权交易 UI" },
    @{ Path = "frontend/app/billing"; Category = "付费 UI" },
    @{ Path = "frontend/app/admin"; Category = "Pro 管理 UI" },
    @{ Path = "frontend/convex"; Category = "商业授权服务" },
    @{ Path = "launcher_customer_go"; Category = "Pro 启动器" },
    @{ Path = "launcher_customer_native"; Category = "Pro 启动器" },
    @{ Path = "installer_customer_go"; Category = "Pro 安装器" },
    @{ Path = "scripts/build_customer_installer.ps1"; Category = "客户安装构建" },
    @{ Path = "build_customer_installer.bat"; Category = "客户安装构建" },
    @{ Path = "Backend.spec"; Category = "闭源后端打包" },
    @{ Path = "CustomerLauncher.spec"; Category = "闭源启动器打包" },
    @{ Path = "MultiTradingLauncher.spec"; Category = "闭源启动器打包" }
)

$forbiddenMarkers = @(
    "submit_stock_order",
    "LONGPORT_APP_SECRET",
    "LONGPORT_ACCESS_TOKEN",
    "FUTU_OPEND_",
    "TIGER_PRIVATE_KEY",
    "FSOPENAPI_CLIENT_PRIVATE_KEY",
    "USMART_CLIENT_PRIVATE_KEY",
    "LOCAL_LICENSE_SIGNING_SECRET",
    "STRIPE_SECRET_KEY",
    "MultiTradingSetup",
    "PyInstaller",
    "Inno Setup"
)

$skipSegments = @(".git", "node_modules", ".next", ".venv", "venv", "dist", "build", "__pycache__")
$sourceExtensions = @(".py", ".pyi", ".ts", ".tsx", ".js", ".cjs", ".mjs", ".go", ".cs", ".ps1", ".bat", ".cmd", ".md", ".json", ".yml", ".yaml", ".toml")
$selfExclusions = @(
    "scripts/check-community-boundary.ps1",
    "docs/community-hardening-checklist.md"
)

function Get-RelativePath([string]$Path) {
    return $Path.Substring($Root.Length).TrimStart([char[]]"\\/")
}

function Is-ScannableFile($File) {
    $relative = Get-RelativePath $File.FullName
    if ($selfExclusions -contains $relative.Replace("\", "/")) {
        return $false
    }
    foreach ($segment in $skipSegments) {
        if ($relative -split "[\\/]" -contains $segment) {
            return $false
        }
    }
    return $sourceExtensions -contains $File.Extension.ToLowerInvariant() -or $File.Name -like ".env.example"
}

$pathFindings = @()
foreach ($rule in $proOnlyPaths) {
    $candidate = Join-Path $Root $rule.Path
    if (Test-Path -LiteralPath $candidate) {
        $pathFindings += [PSCustomObject]@{
            Category = $rule.Category
            Path = $rule.Path
        }
    }
}

$files = Get-ChildItem -LiteralPath $Root -Recurse -File -Force | Where-Object { Is-ScannableFile $_ }
$markerFindings = @()
foreach ($marker in $forbiddenMarkers) {
    $matches = @()
    foreach ($file in $files) {
        try {
            if (Select-String -LiteralPath $file.FullName -Pattern $marker -SimpleMatch -Quiet -ErrorAction Stop) {
                $matches += (Get-RelativePath $file.FullName)
            }
        } catch {
            # A non-text or inaccessible file is ignored; it is outside the source scan set.
        }
    }
    if ($matches.Count -gt 0) {
        $markerFindings += [PSCustomObject]@{
            Marker = $marker
            Files = $matches
        }
    }
}

Write-Host "Community boundary report: $Root"
Write-Host ""
Write-Host "Pro-only paths found: $($pathFindings.Count)"
if ($pathFindings.Count -gt 0) {
    $pathFindings | Sort-Object Category, Path | Format-Table -AutoSize
}

Write-Host "Forbidden markers found: $($markerFindings.Count)"
foreach ($finding in $markerFindings) {
    Write-Host "  $($finding.Marker): $($finding.Files.Count) file(s)"
    foreach ($path in $finding.Files | Sort-Object) {
        Write-Host "    $path"
    }
}

$blockerCount = $pathFindings.Count + $markerFindings.Count
if ($blockerCount -eq 0) {
    Write-Host "PASS: no configured Pro paths or real-execution markers were found."
    exit 0
}

Write-Host "BLOCKED: $blockerCount configured Community boundary finding(s)."
Write-Host "See docs/community-hardening-checklist.md for the migration plan."
if ($Strict) {
    exit 1
}
