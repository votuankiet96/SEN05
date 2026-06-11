param(
    [string]$RepoRoot = "",
    [string]$TaskPath = "\SEN05\",
    [string]$SupervisorTaskName = "SEN05_TickLive_Supervisor",
    [string]$WatchdogTaskName = "SEN05_TickLive_Watchdog",
    [string]$CheckerTaskName = "SEN05_TickData_Checker",
    [string]$PythonExe = ""
)

Set-StrictMode -Version Latest

$modulePath = Join-Path $PSScriptRoot "lib\TickDataOps.psm1"
Import-Module $modulePath -Force

$paths = Get-TickDataPaths -RepoRoot $RepoRoot
Initialize-TickDataRuntime -Paths $paths
$python = Get-TickDataPython -RepoRoot $paths.RepoRoot -PythonExe $PythonExe

Write-Host ""
Write-Host "== TICK_DATA OPS STATUS ==" -ForegroundColor Cyan
Write-Host ("Repo     : {0}" -f $paths.RepoRoot)
Write-Host ("App log  : {0}" -f $paths.AppLog)
Write-Host ("Ops logs : {0}" -f $paths.RuntimeLogs)
Write-Host ("Python   : {0}" -f $python)

Write-Host ""
Write-Host "== TASKS ==" -ForegroundColor Cyan
foreach ($name in @($SupervisorTaskName, $WatchdogTaskName, $CheckerTaskName)) {
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
Write-Host "== TICK LIVE PROCESSES ==" -ForegroundColor Cyan
$processes = @(Get-TickLiveProcesses)
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
Get-TickDataPidFileStatus -Paths $paths | Format-List

Write-Host ""
Write-Host "== DB LOCK ==" -ForegroundColor Cyan
Get-TickDataDbLockStatus -Paths $paths -PythonExe $python | Format-List

Write-Host ""
Write-Host "== SUPERVISOR HEARTBEAT ==" -ForegroundColor Cyan
$heartbeat = Get-TickDataSupervisorHeartbeat -Paths $paths
if ($null -eq $heartbeat) {
    Write-Host "(no heartbeat)"
}
else {
    $heartbeat | Format-List
}

Write-Host ""
Write-Host "== TICK CHECK ==" -ForegroundColor Cyan
Push-Location $paths.RepoRoot
try {
    & $python -m $paths.TickAppModule check
}
finally {
    Pop-Location
}
