param(
    [string]$RepoRoot = "",
    [string]$PythonExe = ""
)

Set-StrictMode -Version Latest

$modulePath = Join-Path $PSScriptRoot "lib\TickDataOps.psm1"
Import-Module $modulePath -Force

$paths = Get-TickDataPaths -RepoRoot $RepoRoot
Initialize-TickDataRuntime -Paths $paths
$python = Get-TickDataPython -RepoRoot $paths.RepoRoot -PythonExe $PythonExe
$logPath = Join-Path $paths.RuntimeLogs "tick_check.log"

Push-Location $paths.RepoRoot
try {
    & $python -m $paths.TickAppModule check --notify *>> $logPath
    $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { $LASTEXITCODE }
}
finally {
    Pop-Location
}

if ($exitCode -ne 0) {
    Write-TickDataOpsLog -LogPath $logPath -Level "WARN" -Message "tick check exit_code=$exitCode" -Quiet
}

exit $exitCode
