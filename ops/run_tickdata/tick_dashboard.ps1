param(
    [string]$RepoRoot = "",
    [string]$PythonExe = "",
    [string]$BindHost = "127.0.0.1",
    [string]$OpenHost = "",
    [int]$Port = 8061,
    [switch]$NoBrowser
)

Set-StrictMode -Version Latest

$modulePath = Join-Path $PSScriptRoot "lib\TickDataOps.psm1"
Import-Module $modulePath -Force

$paths = Get-TickDataPaths -RepoRoot $RepoRoot
Initialize-TickDataRuntime -Paths $paths
$python = Get-TickDataPython -RepoRoot $paths.RepoRoot -PythonExe $PythonExe
$env:CTRADER_FTMO_TICK_DASHBOARD_HOST = $BindHost
$env:CTRADER_FTMO_TICK_DASHBOARD_PORT = [string]$Port
$displayHost = if ($OpenHost) { $OpenHost } elseif ($BindHost -eq "0.0.0.0") { "127.0.0.1" } else { $BindHost }
$url = "http://$displayHost`:$Port/"
$sessionStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$dashboardLog = Join-Path $paths.RuntimeLogs "tick_dashboard_$sessionStamp`_$PID.log"

Write-Host "Tick dashboard: $url" -ForegroundColor Cyan
Write-Host "Bind host    : $BindHost"
Write-Host "App log       : $($paths.AppLog)"
Write-Host "Server log    : $dashboardLog"
Write-Host ""

if (-not $NoBrowser) {
    Write-Host "Browser will open in a few seconds. Keep this window open while using the dashboard." -ForegroundColor DarkGray
    Start-Job -ScriptBlock {
        param([string]$TargetUrl)
        Start-Sleep -Seconds 3
        Start-Process $TargetUrl
    } -ArgumentList $url | Out-Null
}

Push-Location $paths.RepoRoot
try {
    & $python -m $paths.TickDashboardModule *>> $dashboardLog
}
finally {
    Pop-Location
}
