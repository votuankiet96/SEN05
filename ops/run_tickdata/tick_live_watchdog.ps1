param(
    [string]$RepoRoot = "",
    [string]$TaskName = "SEN05_TickLive_Supervisor",
    [string]$TaskPath = "\SEN05\",
    [int]$NoProcessGraceSec = 60
)

Set-StrictMode -Version Latest

$modulePath = Join-Path $PSScriptRoot "lib\TickDataOps.psm1"
Import-Module $modulePath -Force

$TaskName = $TaskName.Trim("'`"")
$TaskPath = $TaskPath.Trim("'`"")
if (-not $TaskPath.StartsWith("\")) {
    $TaskPath = "\" + $TaskPath
}
if (-not $TaskPath.EndsWith("\")) {
    $TaskPath = $TaskPath + "\"
}

$paths = Get-TickDataPaths -RepoRoot $RepoRoot
Initialize-TickDataRuntime -Paths $paths
$logPath = $paths.WatchdogLog

try {
    $task = Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -ErrorAction Stop
}
catch {
    Write-TickDataOpsLog -LogPath $logPath -Level "ERROR" -Message "supervisor task not found: $TaskPath$TaskName"
    exit 2
}

$live = @(Get-TickLiveProcesses)

if ($task.State -ne "Running") {
    Write-TickDataOpsLog -LogPath $logPath -Level "WARN" -Message "supervisor task state=$($task.State); starting $TaskPath$TaskName"
    Start-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath
    exit 0
}

if ($live.Count -gt 0) {
    if (Test-Path -LiteralPath $paths.NoProcessSinceFile) {
        Remove-Item -LiteralPath $paths.NoProcessSinceFile -Force -ErrorAction SilentlyContinue
    }
    $ids = ($live | Select-Object -ExpandProperty ProcessId) -join ", "
    Write-TickDataOpsLog -LogPath $logPath -Message "healthy: supervisor running; tick live process(es): $ids" -Quiet
    exit 0
}

$nowUtc = (Get-Date).ToUniversalTime()
if (-not (Test-Path -LiteralPath $paths.NoProcessSinceFile)) {
    $nowUtc.ToString("o") | Set-Content -LiteralPath $paths.NoProcessSinceFile -Encoding ASCII
    Write-TickDataOpsLog -LogPath $logPath -Level "WARN" -Message "supervisor running but no tick live process detected; grace window started"
    exit 0
}

$sinceRaw = Get-Content -LiteralPath $paths.NoProcessSinceFile -ErrorAction SilentlyContinue | Select-Object -First 1
$sinceUtc = $null
if (-not [datetime]::TryParse($sinceRaw, [ref]$sinceUtc)) {
    $sinceUtc = $nowUtc
    $sinceUtc.ToString("o") | Set-Content -LiteralPath $paths.NoProcessSinceFile -Encoding ASCII
}

$ageSec = [int]($nowUtc - $sinceUtc.ToUniversalTime()).TotalSeconds
if ($ageSec -lt $NoProcessGraceSec) {
    Write-TickDataOpsLog -LogPath $logPath -Level "WARN" -Message "no tick live process for ${ageSec}s; waiting until ${NoProcessGraceSec}s grace"
    exit 0
}

Write-TickDataOpsLog -LogPath $logPath -Level "WARN" -Message "no tick live process for ${ageSec}s; restarting supervisor task"
try {
    Stop-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
    Start-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath
    Remove-Item -LiteralPath $paths.NoProcessSinceFile -Force -ErrorAction SilentlyContinue
}
catch {
    Write-TickDataOpsLog -LogPath $logPath -Level "ERROR" -Message "could not restart supervisor task: $($_.Exception.Message)"
    exit 1
}
