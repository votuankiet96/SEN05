param(
    [string]$RepoRoot = "",
    [string]$PythonExe = "",
    [int]$RestartDelaySec = 30,
    [int]$SettleSeconds = 5,
    [switch]$SkipDbLockRepair
)

Set-StrictMode -Version Latest

$modulePath = Join-Path $PSScriptRoot "lib\WsLiveOps.psm1"
Import-Module $modulePath -Force

$paths = Get-WsLivePaths -RepoRoot $RepoRoot
Initialize-WsLiveRuntime -Paths $paths

$python = Get-WsLivePython -RepoRoot $paths.RepoRoot -PythonExe $PythonExe
$consoleLog = $paths.ConsoleLog
$supervisorLog = $paths.SupervisorLog
$env:PYTHONUNBUFFERED = "1"

Write-WsLiveOpsLog -LogPath $supervisorLog -Message "supervisor online: repo=$($paths.RepoRoot) python=$python restart_delay=${RestartDelaySec}s"

while ($true) {
    Update-WsLiveSupervisorHeartbeat -Paths $paths -State "pre-start"

    Repair-WsLiveStaleRuntime `
        -Paths $paths `
        -LogPath $supervisorLog `
        -Reason "pre-start" `
        -SettleSeconds 0 `
        -SkipDbLockRepair:$SkipDbLockRepair `
        -PythonExe $python | Out-Null

    if (-not (Test-Path -LiteralPath $paths.WsLiveScript)) {
        Write-WsLiveOpsLog -LogPath $supervisorLog -Level "ERROR" -Message "ws_live script not found: $($paths.WsLiveScript)"
        Start-Sleep -Seconds $RestartDelaySec
        continue
    }

    $startedAt = Get-Date
    Write-WsLiveOpsLog -LogPath $supervisorLog -Message "ws_live START"
    Update-WsLiveSupervisorHeartbeat -Paths $paths -State "running"

    Push-Location $paths.RepoRoot
    try {
        & $python $paths.WsLiveScript *>> $consoleLog
        $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { $LASTEXITCODE }
    }
    catch {
        $exitCode = 997
        Write-WsLiveOpsLog -LogPath $supervisorLog -Level "ERROR" -Message "ws_live launch failed: $($_.Exception.Message)"
    }
    finally {
        Pop-Location
    }

    $duration = [int]((Get-Date) - $startedAt).TotalSeconds
    Write-WsLiveOpsLog -LogPath $supervisorLog -Message "ws_live EXIT code=$exitCode duration=${duration}s"
    Update-WsLiveSupervisorHeartbeat -Paths $paths -State "exited" -ChildExitCode $exitCode

    Repair-WsLiveStaleRuntime `
        -Paths $paths `
        -LogPath $supervisorLog `
        -Reason "post-exit code=$exitCode" `
        -SettleSeconds $SettleSeconds `
        -SkipDbLockRepair:$SkipDbLockRepair `
        -PythonExe $python | Out-Null

    Write-WsLiveOpsLog -LogPath $supervisorLog -Message "restart in ${RestartDelaySec}s"
    Start-Sleep -Seconds $RestartDelaySec
}
