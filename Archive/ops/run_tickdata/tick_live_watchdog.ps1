param(
    [string]$RepoRoot = "",
    [string]$TaskName = "SEN05_TickLive_Supervisor",
    [string]$TaskPath = "\SEN05\",
    [int]$NoProcessGraceSec = 60,
    [int]$StaleFeedRestartSec = 900,
    [int]$StaleCheckEverySec = 300,
    [string]$PythonExe = ""
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
$python = Get-TickDataPython -RepoRoot $paths.RepoRoot -PythonExe $PythonExe

function Test-StaleCheckDue {
    param([datetime]$NowUtc)

    if ($StaleCheckEverySec -le 0) {
        return $true
    }
    if (-not (Test-Path -LiteralPath $paths.StaleFeedCheckAfterFile)) {
        return $true
    }
    $raw = Get-Content -LiteralPath $paths.StaleFeedCheckAfterFile -ErrorAction SilentlyContinue | Select-Object -First 1
    $nextUtc = $null
    if (-not [datetime]::TryParse($raw, [ref]$nextUtc)) {
        return $true
    }
    return $NowUtc -ge $nextUtc.ToUniversalTime()
}

function Set-NextStaleCheck {
    param([datetime]$NowUtc)

    if ($StaleCheckEverySec -le 0) {
        return
    }
    $NowUtc.AddSeconds($StaleCheckEverySec).ToString("o") |
        Set-Content -LiteralPath $paths.StaleFeedCheckAfterFile -Encoding ASCII
}

function Get-StaleHeartbeatCount {
    param([object]$Report)

    $count = 0
    foreach ($finding in @($Report.findings)) {
        if ($null -ne $finding -and
            $null -ne $finding.PSObject.Properties["code"] -and
            $finding.code -eq "stale_heartbeat") {
            $count++
        }
    }
    return $count
}

function Clear-StaleFeedState {
    Remove-Item -LiteralPath $paths.StaleFeedSinceFile -Force -ErrorAction SilentlyContinue
}

function Test-AndRepairStaleFeed {
    param(
        [array]$LiveProcesses,
        [datetime]$NowUtc
    )

    if (-not (Test-StaleCheckDue -NowUtc $NowUtc)) {
        return
    }
    Set-NextStaleCheck -NowUtc $NowUtc

    $report = Get-TickDataCheckReport -Paths $paths -PythonExe $python
    if (-not $report.ok) {
        Write-TickDataOpsLog -LogPath $logPath -Level "WARN" -Message "stale feed check skipped: $($report.error)" -Quiet
        return
    }

    $staleCount = Get-StaleHeartbeatCount -Report $report
    if ($staleCount -le 0) {
        Clear-StaleFeedState
        Write-TickDataOpsLog -LogPath $logPath -Message "feed healthy by checker status=$($report.status)" -Quiet
        return
    }

    if ($StaleFeedRestartSec -le 0) {
        Write-TickDataOpsLog -LogPath $logPath -Level "WARN" -Message "stale feed detected for $staleCount symbol(s), auto restart disabled" -Quiet
        return
    }

    if (-not (Test-Path -LiteralPath $paths.StaleFeedSinceFile)) {
        $NowUtc.ToString("o") | Set-Content -LiteralPath $paths.StaleFeedSinceFile -Encoding ASCII
        Write-TickDataOpsLog -LogPath $logPath -Level "WARN" -Message "stale feed detected for $staleCount symbol(s); restart grace started (${StaleFeedRestartSec}s)"
        return
    }

    $sinceRaw = Get-Content -LiteralPath $paths.StaleFeedSinceFile -ErrorAction SilentlyContinue | Select-Object -First 1
    $sinceUtc = $null
    if (-not [datetime]::TryParse($sinceRaw, [ref]$sinceUtc)) {
        $sinceUtc = $NowUtc
        $sinceUtc.ToString("o") | Set-Content -LiteralPath $paths.StaleFeedSinceFile -Encoding ASCII
    }

    $ageSec = [int]($NowUtc - $sinceUtc.ToUniversalTime()).TotalSeconds
    if ($ageSec -lt $StaleFeedRestartSec) {
        Write-TickDataOpsLog -LogPath $logPath -Level "WARN" -Message "stale feed age=${ageSec}s for $staleCount symbol(s); waiting for ${StaleFeedRestartSec}s grace" -Quiet
        return
    }

    $ids = ($LiveProcesses | Select-Object -ExpandProperty ProcessId) -join ", "
    Write-TickDataOpsLog -LogPath $logPath -Level "WARN" -Message "stale feed age=${ageSec}s for $staleCount symbol(s); requesting graceful live restart; process(es): $ids"
    $shutdown = Request-TickLiveGracefulShutdown -Paths $paths -PythonExe $python
    if ($shutdown.ok) {
        Write-TickDataOpsLog -LogPath $logPath -Level "WARN" -Message "graceful shutdown signal written; supervisor should restart live after clean exit"
    }
    else {
        Write-TickDataOpsLog -LogPath $logPath -Level "ERROR" -Message "could not request graceful shutdown: $($shutdown.error)"
    }
}

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
    $nowUtc = (Get-Date).ToUniversalTime()
    Test-AndRepairStaleFeed -LiveProcesses $live -NowUtc $nowUtc
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
