param(
    [string]$RepoRoot = "",
    [string]$PythonExe = "",
    [int]$LookbackMinutes = 15,
    [int]$SafetyLagMinutes = 2,
    [int]$BucketMinutes = 1,
    [int]$RequestTimeoutSeconds = 120,
    [int]$RetryCount = 1,
    [int]$RetrySleepSeconds = 20,
    [int]$BetweenChunksSleepSeconds = 1,
    [int]$MaxRunMinutes = 20,
    [int]$NotifySummaryMinutes = 60,
    [string[]]$Symbols = @(),
    [switch]$NoNotify
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$modulePath = Join-Path $PSScriptRoot "lib\TickDataOps.psm1"
Import-Module $modulePath -Force

$paths = Get-TickDataPaths -RepoRoot $RepoRoot
Initialize-TickDataRuntime -Paths $paths
$python = Get-TickDataPython -RepoRoot $paths.RepoRoot -PythonExe $PythonExe
$logPath = $paths.ShortRepairLog
$lockPath = $paths.ShortRepairLockFile
$dailyScript = Join-Path $paths.RunRoot "tick_daily_overlap_backfill.ps1"
$script:lockHeld = $false

if ($LookbackMinutes -le 0) { throw "LookbackMinutes must be greater than 0" }
if ($SafetyLagMinutes -lt 0) { throw "SafetyLagMinutes must be zero or greater" }
if ($BucketMinutes -le 0) { throw "BucketMinutes must be greater than 0" }
if ($RequestTimeoutSeconds -le 0) { throw "RequestTimeoutSeconds must be greater than 0" }
if ($RetryCount -le 0) { throw "RetryCount must be greater than 0" }
if ($MaxRunMinutes -le 0) { throw "MaxRunMinutes must be greater than 0" }
if (-not (Test-Path -LiteralPath $dailyScript)) { throw "Daily overlap script not found: $dailyScript" }

function Format-UtcIso {
    param([Parameter(Mandatory = $true)][datetime]$Value)

    return $Value.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
}

function Send-ShortRepairDiscord {
    param(
        [Parameter(Mandatory = $true)][string]$Level,
        [Parameter(Mandatory = $true)][string]$Title,
        [Parameter(Mandatory = $true)][string]$Conclusion,
        [string]$Action = "",
        [object[]]$Details = @(),
        [object[]]$Technical = @(),
        [string]$ThrottleKey = "tick-short-overlap-summary",
        [int]$ThrottleSeconds = 3600
    )

    if ($NoNotify) {
        return
    }

    $oldPayload = $env:TICK_SHORT_REPAIR_NOTIFY_PAYLOAD
    $payload = @{
        level = $Level
        title = $Title
        conclusion = $Conclusion
        action = $Action
        details = $Details
        technical = $Technical
        throttle_key = $ThrottleKey
        throttle_seconds = $ThrottleSeconds
    } | ConvertTo-Json -Depth 8 -Compress

    $env:TICK_SHORT_REPAIR_NOTIFY_PAYLOAD = $payload
    $code = @'
import json
import os

from data_provider.tick_data.notify import flush_notifications, notify_tick_report

payload = json.loads(os.environ["TICK_SHORT_REPAIR_NOTIFY_PAYLOAD"])
notify_tick_report(
    payload["level"],
    payload["title"],
    conclusion=payload["conclusion"],
    action=payload.get("action") or None,
    details=payload.get("details") or None,
    technical=payload.get("technical") or None,
    throttle_key=payload.get("throttle_key"),
    throttle_seconds=int(payload.get("throttle_seconds") or 300),
)
flush_notifications()
'@

    Push-Location $paths.RepoRoot
    try {
        $output = $code | & $python - 2>&1
        $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
    }
    finally {
        Pop-Location
        $env:TICK_SHORT_REPAIR_NOTIFY_PAYLOAD = $oldPayload
    }

    if ($exitCode -ne 0) {
        Write-TickDataOpsLog -LogPath $logPath -Level "WARN" -Message "short repair Discord notify failed: $($output -join ' | ')" -Quiet
    }
}

function Enter-ShortRepairLock {
    $nowUtc = (Get-Date).ToUniversalTime()
    if (Test-Path -LiteralPath $lockPath) {
        $raw = Get-Content -LiteralPath $lockPath -Raw -ErrorAction SilentlyContinue
        $lockedAt = $null
        try {
            $payload = $raw | ConvertFrom-Json
            if ($null -ne $payload.started_at_utc) {
                [datetime]::TryParse([string]$payload.started_at_utc, [ref]$lockedAt) | Out-Null
            }
        }
        catch {
            $lockedAt = $null
        }

        if ($null -ne $lockedAt) {
            $ageMinutes = ($nowUtc - $lockedAt.ToUniversalTime()).TotalMinutes
            if ($ageMinutes -lt $MaxRunMinutes) {
                Write-TickDataOpsLog -LogPath $logPath -Message ("short repair skipped: another run started {0:N1} minute(s) ago" -f $ageMinutes) -Quiet
                return $false
            }
        }

        Write-TickDataOpsLog -LogPath $logPath -Level "WARN" -Message "short repair removing stale lock: $lockPath"
        Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
    }

    @{
        started_at_utc = $nowUtc.ToString("o")
        pid = $PID
        host = $env:COMPUTERNAME
    } | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $lockPath -Encoding UTF8
    $script:lockHeld = $true
    return $true
}

try {
    if (-not (Enter-ShortRepairLock)) {
        exit 0
    }

    $toUtc = (Get-Date).ToUniversalTime().AddMinutes(-1 * $SafetyLagMinutes)
    $fromUtc = $toUtc.AddMinutes(-1 * $LookbackMinutes)
    if ($fromUtc -ge $toUtc) {
        throw "Invalid short repair window: from=$fromUtc to=$toUtc"
    }

    $fromText = Format-UtcIso -Value $fromUtc
    $toText = Format-UtcIso -Value $toUtc
    $runStartedUtc = (Get-Date).ToUniversalTime()

    Write-TickDataOpsLog -LogPath $logPath -Message "short repair requested window=$fromText->$toText lookback_minutes=$LookbackMinutes safety_lag_minutes=$SafetyLagMinutes single_session=1"

    $psExe = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"
    $argsList = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $dailyScript,
        "-RepoRoot", $paths.RepoRoot,
        "-PythonExe", $python,
        "-From", $fromText,
        "-To", $toText,
        "-ChunkHours", "1",
        "-BucketMinutes", [string]$BucketMinutes,
        "-RequestTimeoutSeconds", [string]$RequestTimeoutSeconds,
        "-RetryCount", [string]$RetryCount,
        "-RetrySleepSeconds", [string]$RetrySleepSeconds,
        "-BetweenChunksSleepSeconds", [string]$BetweenChunksSleepSeconds,
        "-SingleSession",
        "-SkipSymbolSync",
        "-SkipActivityProfile",
        "-NoNotify"
    )
    if ($Symbols.Count -gt 0) {
        $argsList += "-Symbols"
        $argsList += $Symbols
    }

    $output = & $psExe @argsList 2>&1
    $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
    Add-Content -LiteralPath $logPath -Value ($output | ForEach-Object { "[daily] $_" })

    if ($exitCode -ne 0) {
        throw "short overlap backfill failed with exit code $exitCode"
    }

    $reportLine = $output | Where-Object { $_ -match "^Report JSON:\s*(?<path>.+)$" } | Select-Object -Last 1
    if (-not $reportLine -or $reportLine -notmatch "^Report JSON:\s*(?<path>.+)$") {
        throw "short repair completed but report path was not found in output"
    }

    $reportPath = $matches.path.Trim()
    $report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
    $elapsedSec = [int]((Get-Date).ToUniversalTime() - $runStartedUtc).TotalSeconds

    Write-TickDataOpsLog -LogPath $logPath -Message ("short repair completed historical_added={0} new_buckets={1} elapsed={2}s report={3}" -f `
        $report.totals.historical_added,
        $report.totals.new_buckets_any,
        $elapsedSec,
        $reportPath)

    Write-Host ""
    Write-Host "== SHORT OVERLAP REPAIR =="
    Write-Host "Window UTC: $($report.from_utc) -> $($report.to_utc)"
    Write-Host ("Historical added: {0:N0}" -f $report.totals.historical_added)
    Write-Host ("New {0}m buckets: {1:N0}" -f $report.bucket_minutes, $report.totals.new_buckets_any)
    Write-Host "Report JSON: $reportPath"

    $summaryThrottle = [Math]::Max(300, $NotifySummaryMinutes * 60)
    Send-ShortRepairDiscord `
        -Level "INFO" `
        -Title "Tick short overlap repair summary" `
        -Conclusion ("Short overlap repair is running. Net-new historical tick rows added in the latest run: {0:N0}. Newly covered {1}m buckets: {2:N0}." -f `
            $report.totals.historical_added,
            $report.bucket_minutes,
            $report.totals.new_buckets_any) `
        -Action "No action is needed unless this summary repeatedly shows failures in the ops log or the checker stays WARNING." `
        -Details @(
            @("Window UTC", "$($report.from_utc) -> $($report.to_utc)"),
            @("Historical added", ("{0:N0}" -f $report.totals.historical_added)),
            @("BID added", ("{0:N0}" -f $report.totals.historical_bid_added)),
            @("ASK added", ("{0:N0}" -f $report.totals.historical_ask_added)),
            @("New buckets", ("{0:N0}" -f $report.totals.new_buckets_any)),
            @("Elapsed", "${elapsedSec}s")
        ) `
        -Technical @(
            @("Mode", "single-session historical overlap"),
            @("Report", $reportPath),
            @("Log", $logPath)
        ) `
        -ThrottleKey "tick-short-overlap-summary" `
        -ThrottleSeconds $summaryThrottle
}
catch {
    $err = $_.Exception.Message
    Write-TickDataOpsLog -LogPath $logPath -Level "ERROR" -Message "short repair failed: $err"
    Send-ShortRepairDiscord `
        -Level "ERROR" `
        -Title "Tick short overlap repair failed" `
        -Conclusion "The short overlap repair job failed. Recent live gaps may not be repaired until the next successful repair or daily overlap run." `
        -Action "Open tick_short_overlap_repair.log and ctrader_ftmo_tick.log. Check cTrader connectivity before manually rerunning." `
        -Details @(
            @("Error", $err),
            @("Lookback minutes", $LookbackMinutes),
            @("Safety lag minutes", $SafetyLagMinutes)
        ) `
        -Technical @(
            @("Log", $logPath)
        ) `
        -ThrottleKey "tick-short-overlap-error" `
        -ThrottleSeconds 900
    exit 1
}
finally {
    if ($script:lockHeld) {
        Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
    }
}
