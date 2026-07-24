param(
    [string]$RepoRoot = "",
    [string]$TaskName = "SEN05_WsLive_Supervisor",
    [string]$TaskPath = "\SEN05\",
    [int]$NoProcessGraceSec = 90
)

Set-StrictMode -Version Latest

$modulePath = Join-Path $PSScriptRoot "lib\WsLiveOps.psm1"
Import-Module $modulePath -Force

$paths = Get-WsLivePaths -RepoRoot $RepoRoot
Initialize-WsLiveRuntime -Paths $paths
$logPath = $paths.WatchdogLog

try {
    $task = Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -ErrorAction Stop
}
catch {
    Write-WsLiveOpsLog -LogPath $logPath -Level "ERROR" -Message "supervisor task not found: $TaskPath$TaskName"
    exit 2
}

$live = @(Get-WsLiveProcesses)

if ($task.State -ne "Running") {
    Write-WsLiveOpsLog -LogPath $logPath -Level "WARN" -Message "supervisor task state=$($task.State); starting $TaskPath$TaskName"
    Start-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath
    exit 0
}

if ($live.Count -gt 0) {
    if (Test-Path -LiteralPath $paths.NoProcessSinceFile) {
        Remove-Item -LiteralPath $paths.NoProcessSinceFile -Force -ErrorAction SilentlyContinue
    }
    $ids = ($live | Select-Object -ExpandProperty ProcessId) -join ", "
    Write-WsLiveOpsLog -LogPath $logPath -Message "healthy: supervisor running; ws_live process(es): $ids" -Quiet
    exit 0
}

$nowUtc = (Get-Date).ToUniversalTime()
if (-not (Test-Path -LiteralPath $paths.NoProcessSinceFile)) {
    $nowUtc.ToString("o") | Set-Content -LiteralPath $paths.NoProcessSinceFile -Encoding ASCII
    Write-WsLiveOpsLog -LogPath $logPath -Level "WARN" -Message "supervisor running but no ws_live process detected; grace window started"
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
    Write-WsLiveOpsLog -LogPath $logPath -Level "WARN" -Message "no ws_live process for ${ageSec}s; waiting until ${NoProcessGraceSec}s grace"
    exit 0
}

Write-WsLiveOpsLog -LogPath $logPath -Level "WARN" -Message "no ws_live process for ${ageSec}s; restarting supervisor task"
try {
    Stop-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
    Start-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath
    Remove-Item -LiteralPath $paths.NoProcessSinceFile -Force -ErrorAction SilentlyContinue
}
catch {
    Write-WsLiveOpsLog -LogPath $logPath -Level "ERROR" -Message "could not restart supervisor task: $($_.Exception.Message)"
    exit 1
}
