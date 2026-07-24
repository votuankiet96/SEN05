param(
    [string]$RepoRoot = "",
    [string]$TaskPath = "\SEN05\",
    [string]$SupervisorTaskName = "SEN05_WsLive_Supervisor",
    [string]$WatchdogTaskName = "SEN05_WsLive_Watchdog"
)

Set-StrictMode -Version Latest

$modulePath = Join-Path $PSScriptRoot "lib\WsLiveOps.psm1"
Import-Module $modulePath -Force

$paths = Get-WsLivePaths -RepoRoot $RepoRoot
Initialize-WsLiveRuntime -Paths $paths

Write-Host ""
Write-Host "== WS_LIVE OPS STATUS ==" -ForegroundColor Cyan
Write-Host ("Repo     : {0}" -f $paths.RepoRoot)
Write-Host ("App log  : {0}" -f $paths.AppLog)
Write-Host ("Ops logs : {0}" -f $paths.RuntimeLogs)

Write-Host ""
Write-Host "== TASKS ==" -ForegroundColor Cyan
foreach ($name in @($SupervisorTaskName, $WatchdogTaskName)) {
    try {
        $task = Get-ScheduledTask -TaskName $name -TaskPath $TaskPath -ErrorAction Stop
        $info = Get-ScheduledTaskInfo -TaskName $name -TaskPath $TaskPath -ErrorAction SilentlyContinue
        [pscustomobject]@{
            TaskName          = $task.TaskName
            State             = $task.State
            MultipleInstances = $task.Settings.MultipleInstances
            LastRunTime       = if ($info) { $info.LastRunTime } else { $null }
            LastTaskResult    = if ($info) { $info.LastTaskResult } else { $null }
            NextRunTime       = if ($info) { $info.NextRunTime } else { $null }
        } | Format-List
    }
    catch {
        Write-Host ("{0}{1}: not installed" -f $TaskPath, $name) -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "== WS_LIVE PROCESSES ==" -ForegroundColor Cyan
$processes = @(Get-WsLiveProcesses)
if ($processes.Count -eq 0) {
    Write-Host "(none)"
}
else {
    $processes |
        Select-Object ProcessId, ParentProcessId, Name, CommandLine |
        Format-List
}

Write-Host ""
Write-Host "== PID FILE ==" -ForegroundColor Cyan
Get-WsLivePidFileStatus -Paths $paths | Format-List

Write-Host ""
Write-Host "== DB LOCK ==" -ForegroundColor Cyan
Get-WsLiveDbLockStatus -Paths $paths | Format-List

Write-Host ""
Write-Host "== SUPERVISOR HEARTBEAT ==" -ForegroundColor Cyan
$heartbeat = Get-WsLiveSupervisorHeartbeat -Paths $paths
if ($null -eq $heartbeat) {
    Write-Host "(no heartbeat)"
}
else {
    $heartbeat | Format-List
}
