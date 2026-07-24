param(
    [ValidateSet("app", "supervisor", "watchdog", "console")]
    [string]$Log = "app",
    [int]$Tail = 120,
    [string]$RepoRoot = ""
)

Set-StrictMode -Version Latest

$modulePath = Join-Path $PSScriptRoot "lib\WsLiveOps.psm1"
Import-Module $modulePath -Force

$paths = Get-WsLivePaths -RepoRoot $RepoRoot
Initialize-WsLiveRuntime -Paths $paths

$logPath = switch ($Log) {
    "app"        { $paths.AppLog }
    "supervisor" { $paths.SupervisorLog }
    "watchdog"   { $paths.WatchdogLog }
    "console"    { $paths.ConsoleLog }
}

if ($Host.Name -eq "ConsoleHost") {
    $host.UI.RawUI.WindowTitle = "SEN05 ws_live log viewer ($Log)"
}

& (Join-Path $PSScriptRoot "ws_live_status.ps1") -RepoRoot $paths.RepoRoot

Write-Host ""
Write-Host ("== TAIL {0} ==" -f $logPath) -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop this viewer. Closing this window does not stop ws_live." -ForegroundColor DarkGray

if (-not (Test-Path -LiteralPath $logPath)) {
    New-Item -ItemType File -Force -Path $logPath | Out-Null
}

Get-Content -LiteralPath $logPath -Tail $Tail -Wait
