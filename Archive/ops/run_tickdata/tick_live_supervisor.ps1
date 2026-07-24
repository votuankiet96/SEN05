param(
    [string]$RepoRoot = "",
    [string]$PythonExe = "",
    [int]$RestartDelaySec = 30,
    [int]$SettleSeconds = 5,
    [switch]$SkipDbLockRepair
)

Set-StrictMode -Version Latest

$modulePath = Join-Path $PSScriptRoot "lib\TickDataOps.psm1"
Import-Module $modulePath -Force

$paths = Get-TickDataPaths -RepoRoot $RepoRoot
Initialize-TickDataRuntime -Paths $paths

$python = Get-TickDataPython -RepoRoot $paths.RepoRoot -PythonExe $PythonExe
$consoleLog = $paths.ConsoleLog
$supervisorLog = $paths.SupervisorLog
$env:PYTHONUNBUFFERED = "1"

Write-TickDataOpsLog -LogPath $supervisorLog -Message "supervisor online: repo=$($paths.RepoRoot) python=$python restart_delay=${RestartDelaySec}s"

while ($true) {
    Update-TickDataSupervisorHeartbeat -Paths $paths -State "pre-start"

    Repair-TickDataStaleRuntime `
        -Paths $paths `
        -LogPath $supervisorLog `
        -Reason "pre-start" `
        -SettleSeconds 0 `
        -SkipDbLockRepair:$SkipDbLockRepair `
        -PythonExe $python | Out-Null

    $startedAt = Get-Date
    Write-TickDataOpsLog -LogPath $supervisorLog -Message "tick live START"
    Update-TickDataSupervisorHeartbeat -Paths $paths -State "running"

    Push-Location $paths.RepoRoot
    try {
        & $python -m $paths.TickAppModule live *>> $consoleLog
        $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { $LASTEXITCODE }
    }
    catch {
        $exitCode = 997
        Write-TickDataOpsLog -LogPath $supervisorLog -Level "ERROR" -Message "tick live launch failed: $($_.Exception.Message)"
    }
    finally {
        Pop-Location
    }

    $duration = [int]((Get-Date) - $startedAt).TotalSeconds
    Write-TickDataOpsLog -LogPath $supervisorLog -Message "tick live EXIT code=$exitCode duration=${duration}s"
    Update-TickDataSupervisorHeartbeat -Paths $paths -State "exited" -ChildExitCode $exitCode

    Repair-TickDataStaleRuntime `
        -Paths $paths `
        -LogPath $supervisorLog `
        -Reason "post-exit code=$exitCode" `
        -SettleSeconds $SettleSeconds `
        -SkipDbLockRepair:$SkipDbLockRepair `
        -PythonExe $python | Out-Null

    Write-TickDataOpsLog -LogPath $supervisorLog -Message "restart in ${RestartDelaySec}s"
    Start-Sleep -Seconds $RestartDelaySec
}
