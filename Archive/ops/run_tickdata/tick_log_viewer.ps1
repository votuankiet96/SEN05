param(
    [string]$RepoRoot = "",
    [int]$Tail = 200
)

Set-StrictMode -Version Latest

$modulePath = Join-Path $PSScriptRoot "lib\TickDataOps.psm1"
Import-Module $modulePath -Force

$paths = Get-TickDataPaths -RepoRoot $RepoRoot
Initialize-TickDataRuntime -Paths $paths

if (-not (Test-Path -LiteralPath $paths.AppLog)) {
    New-Item -ItemType File -Force -Path $paths.AppLog | Out-Null
}

Write-Host "Tick live app log: $($paths.AppLog)" -ForegroundColor Cyan
Write-Host "Closing this viewer does not stop tick live." -ForegroundColor Yellow
Write-Host ""

Get-Content -LiteralPath $paths.AppLog -Tail $Tail -Wait
