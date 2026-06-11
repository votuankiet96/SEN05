param(
    [string]$RepoRoot = "",
    [string]$PythonExe = "",
    [int]$Port = 8061,
    [switch]$NoBrowser
)

Set-StrictMode -Version Latest

$modulePath = Join-Path $PSScriptRoot "lib\TickDataOps.psm1"
Import-Module $modulePath -Force

$paths = Get-TickDataPaths -RepoRoot $RepoRoot
Initialize-TickDataRuntime -Paths $paths
$python = Get-TickDataPython -RepoRoot $paths.RepoRoot -PythonExe $PythonExe
$env:CTRADER_FTMO_TICK_DASHBOARD_PORT = [string]$Port
$url = "http://127.0.0.1:$Port/"

Write-Host "Tick dashboard: $url" -ForegroundColor Cyan
Write-Host "App log       : $($paths.AppLog)"
Write-Host ""

if (-not $NoBrowser) {
    Start-Process $url
}

Push-Location $paths.RepoRoot
try {
    & $python -m $paths.TickDashboardModule *>> $paths.DashboardLog
}
finally {
    Pop-Location
}
