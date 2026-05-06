param(
    [string]$TaskName = "SEN05_SignalWatcher"
)

$ErrorActionPreference = "SilentlyContinue"

$Task = Get-ScheduledTask -TaskName $TaskName
$TaskInfo = Get-ScheduledTaskInfo -TaskName $TaskName
$WatcherProcesses = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
    Where-Object { $_.CommandLine -like "*core_python.notify.signal_watcher*" } |
    Select-Object ProcessId, ParentProcessId, CommandLine

[pscustomobject]@{
    TaskName = $Task.TaskName
    TaskState = $Task.State
    LastRunTime = $TaskInfo.LastRunTime
    LastTaskResult = $TaskInfo.LastTaskResult
    NextRunTime = $TaskInfo.NextRunTime
    WatcherProcessCount = @($WatcherProcesses).Count
}

$WatcherProcesses
