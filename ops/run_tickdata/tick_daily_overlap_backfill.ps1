param(
    [string]$RepoRoot = "",
    [string]$PythonExe = "",
    [int]$LookbackHours = 48,
    [int]$ChunkHours = 6,
    [int]$SafetyLagMinutes = 5,
    [int]$BucketMinutes = 1,
    [int]$RequestTimeoutSeconds = 180,
    [int]$RetryCount = 3,
    [int]$RetrySleepSeconds = 45,
    [int]$BetweenChunksSleepSeconds = 8,
    [string[]]$Symbols = @(),
    [string]$From = "",
    [string]$To = "",
    [switch]$SkipSymbolSync,
    [switch]$NoNotify
)

Set-StrictMode -Version Latest

$modulePath = Join-Path $PSScriptRoot "lib\TickDataOps.psm1"
Import-Module $modulePath -Force

$paths = Get-TickDataPaths -RepoRoot $RepoRoot
Initialize-TickDataRuntime -Paths $paths
$python = Get-TickDataPython -RepoRoot $paths.RepoRoot -PythonExe $PythonExe

$logPath = Join-Path $paths.RuntimeLogs "tick_daily_overlap_backfill.log"
$reportJsonlPath = Join-Path $paths.RuntimeLogs "tick_daily_overlap_report.jsonl"
$reportDir = Join-Path $paths.RuntimeRoot "reports"
New-Item -ItemType Directory -Force -Path $reportDir | Out-Null

$defaultSymbols = @("FR40", "DE40", "HK50", "J225", "SP35", "UK100", "US500", "US100", "US30", "GOLD", "BTCUSD")
if ($Symbols.Count -eq 0) {
    $Symbols = $defaultSymbols
}
$Symbols = @($Symbols | ForEach-Object { $_.Trim().ToUpperInvariant() } | Where-Object { $_ })

if ($LookbackHours -le 0) { throw "LookbackHours must be greater than 0" }
if ($ChunkHours -le 0) { throw "ChunkHours must be greater than 0" }
if ($BucketMinutes -le 0) { throw "BucketMinutes must be greater than 0" }
if ($RequestTimeoutSeconds -le 0) { throw "RequestTimeoutSeconds must be greater than 0" }
if ($RetryCount -le 0) { throw "RetryCount must be greater than 0" }
if ($SafetyLagMinutes -lt 0) { throw "SafetyLagMinutes must be zero or greater" }

function Convert-ToUtcDateTime {
    param([Parameter(Mandatory = $true)][string]$Value)

    return [System.DateTimeOffset]::Parse(
        $Value,
        [System.Globalization.CultureInfo]::InvariantCulture,
        [System.Globalization.DateTimeStyles]::AssumeUniversal
    ).UtcDateTime
}

function Format-UtcIso {
    param([Parameter(Mandatory = $true)][datetime]$Value)

    return $Value.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
}

if ($To) {
    $toUtc = Convert-ToUtcDateTime -Value $To
}
else {
    $toUtc = (Get-Date).ToUniversalTime().AddMinutes(-1 * $SafetyLagMinutes)
}

if ($From) {
    $fromUtc = Convert-ToUtcDateTime -Value $From
}
else {
    $fromUtc = $toUtc.AddHours(-1 * $LookbackHours)
}

if ($fromUtc -ge $toUtc) {
    throw "Invalid overlap window: from=$fromUtc must be before to=$toUtc"
}

$fromText = Format-UtcIso -Value $fromUtc
$toText = Format-UtcIso -Value $toUtc
$runId = [guid]::NewGuid().ToString("N").Substring(0, 8)
$beforePath = Join-Path $reportDir "tick_overlap_${runId}_before.json"
$afterPath = Join-Path $reportDir "tick_overlap_${runId}_after.json"
$reportPath = Join-Path $reportDir "tick_overlap_${runId}_report.json"

function Send-TickOverlapDiscord {
    param(
        [ValidateSet("INFO", "WARNING", "ERROR")][string]$Level,
        [Parameter(Mandatory = $true)][string]$Title,
        [Parameter(Mandatory = $true)][string]$Conclusion,
        [string]$Action = "",
        [object[]]$Details = @(),
        [object[]]$Technical = @()
    )

    if ($NoNotify) {
        return
    }

    $oldLevel = $env:TICK_NOTIFY_LEVEL
    $oldTitle = $env:TICK_NOTIFY_TITLE
    $oldConclusion = $env:TICK_NOTIFY_CONCLUSION
    $oldAction = $env:TICK_NOTIFY_ACTION
    $oldDetails = $env:TICK_NOTIFY_DETAILS_JSON
    $oldTechnical = $env:TICK_NOTIFY_TECHNICAL_JSON

    $env:TICK_NOTIFY_LEVEL = $Level
    $env:TICK_NOTIFY_TITLE = $Title
    $env:TICK_NOTIFY_CONCLUSION = $Conclusion
    $env:TICK_NOTIFY_ACTION = $Action
    $env:TICK_NOTIFY_DETAILS_JSON = ($Details | ConvertTo-Json -Compress -Depth 8)
    $env:TICK_NOTIFY_TECHNICAL_JSON = ($Technical | ConvertTo-Json -Compress -Depth 8)

    $code = @'
from __future__ import annotations

import json
import os

from data_provider.tick_data.notify import flush_notifications, notify_tick_report


def rows_from_env(name: str) -> list[tuple[str, str]]:
    raw = os.environ.get(name, "[]")
    try:
        items = json.loads(raw)
    except Exception:
        items = []
    rows: list[tuple[str, str]] = []
    for item in items:
        if isinstance(item, dict):
            label = str(item.get("label", item.get("key", "")))
            value = str(item.get("value", ""))
            rows.append((label, value))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            rows.append((str(item[0]), str(item[1])))
        else:
            rows.append(("", str(item)))
    return rows


notify_tick_report(
    os.environ["TICK_NOTIFY_LEVEL"],
    os.environ["TICK_NOTIFY_TITLE"],
    conclusion=os.environ["TICK_NOTIFY_CONCLUSION"],
    action=os.environ.get("TICK_NOTIFY_ACTION") or None,
    details=rows_from_env("TICK_NOTIFY_DETAILS_JSON"),
    technical=rows_from_env("TICK_NOTIFY_TECHNICAL_JSON"),
    throttle_key=None,
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
        $env:TICK_NOTIFY_LEVEL = $oldLevel
        $env:TICK_NOTIFY_TITLE = $oldTitle
        $env:TICK_NOTIFY_CONCLUSION = $oldConclusion
        $env:TICK_NOTIFY_ACTION = $oldAction
        $env:TICK_NOTIFY_DETAILS_JSON = $oldDetails
        $env:TICK_NOTIFY_TECHNICAL_JSON = $oldTechnical
    }

    if ($exitCode -ne 0) {
        Write-TickDataOpsLog -LogPath $logPath -Level "WARN" -Message "discord notify failed level=$Level title=$Title output=$($output -join ' | ')" -Quiet
    }
}

function Invoke-TickCli {
    param([Parameter(Mandatory = $true)][string[]]$ArgsList)

    Push-Location $paths.RepoRoot
    try {
        & $python -m $paths.TickAppModule @ArgsList *>> $logPath
        $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
        return $exitCode
    }
    finally {
        Pop-Location
    }
}

function Invoke-TickSnapshot {
    param([Parameter(Mandatory = $true)][string]$OutPath)

    $oldSymbols = $env:TICK_OVERLAP_SYMBOLS
    $oldFrom = $env:TICK_OVERLAP_FROM_UTC
    $oldTo = $env:TICK_OVERLAP_TO_UTC
    $oldBucket = $env:TICK_OVERLAP_BUCKET_MINUTES
    $oldOut = $env:TICK_OVERLAP_OUT_PATH

    $env:TICK_OVERLAP_SYMBOLS = ($Symbols -join ",")
    $env:TICK_OVERLAP_FROM_UTC = $fromText
    $env:TICK_OVERLAP_TO_UTC = $toText
    $env:TICK_OVERLAP_BUCKET_MINUTES = [string]$BucketMinutes
    $env:TICK_OVERLAP_OUT_PATH = $OutPath

    $code = @'
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

from modules.db_connector import get_connection


def parse_utc(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value).astimezone(timezone.utc).replace(tzinfo=None)


def as_int(value) -> int:
    return int(value or 0)


symbols = [item.strip().upper() for item in os.environ["TICK_OVERLAP_SYMBOLS"].split(",") if item.strip()]
from_dt = parse_utc(os.environ["TICK_OVERLAP_FROM_UTC"])
to_dt = parse_utc(os.environ["TICK_OVERLAP_TO_UTC"])
bucket_minutes = int(os.environ["TICK_OVERLAP_BUCKET_MINUTES"])
out_path = os.environ["TICK_OVERLAP_OUT_PATH"]

if bucket_minutes <= 0:
    raise ValueError("bucket_minutes must be greater than 0")

payload = {
    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    "from_utc": os.environ["TICK_OVERLAP_FROM_UTC"],
    "to_utc": os.environ["TICK_OVERLAP_TO_UTC"],
    "bucket_minutes": bucket_minutes,
    "symbols": {},
}

conn = get_connection()
try:
    cur = conn.cursor()
    for symbol in symbols:
        if not re.fullmatch(r"[A-Z0-9_]+", symbol):
            raise ValueError(f"unsafe symbol: {symbol!r}")

        table = f"tick.[{symbol}]"
        row = cur.execute(
            f"""
            SELECT
                COUNT_BIG(1) AS TotalRows,
                SUM(CASE WHEN SourceMode = 'HISTORICAL' THEN 1 ELSE 0 END) AS HistoricalRows,
                SUM(CASE WHEN SourceMode = 'HISTORICAL' AND QuoteType = 'BID' THEN 1 ELSE 0 END) AS HistoricalBidRows,
                SUM(CASE WHEN SourceMode = 'HISTORICAL' AND QuoteType = 'ASK' THEN 1 ELSE 0 END) AS HistoricalAskRows,
                SUM(CASE WHEN SourceMode = 'LIVE' THEN 1 ELSE 0 END) AS LiveRows
            FROM {table} WITH (NOLOCK)
            WHERE TickTimeUtc >= ? AND TickTimeUtc < ?;
            """,
            from_dt,
            to_dt,
        ).fetchone()

        buckets_by_quote = {"BID": set(), "ASK": set()}
        bucket_rows = cur.execute(
            f"""
            SELECT QuoteType, BucketIndex
            FROM (
                SELECT
                    QuoteType,
                    CONVERT(BIGINT, DATEDIFF_BIG(MINUTE, ?, TickTimeUtc) / ?) AS BucketIndex
                FROM {table} WITH (NOLOCK)
                WHERE TickTimeUtc >= ? AND TickTimeUtc < ?
                  AND QuoteType IN ('BID', 'ASK')
            ) AS b
            GROUP BY QuoteType, BucketIndex;
            """,
            from_dt,
            bucket_minutes,
            from_dt,
            to_dt,
        ).fetchall()

        for quote_type, bucket_index in bucket_rows:
            quote = str(quote_type).upper()
            if quote in buckets_by_quote:
                buckets_by_quote[quote].add(int(bucket_index))

        any_buckets = buckets_by_quote["BID"] | buckets_by_quote["ASK"]
        payload["symbols"][symbol] = {
            "total_rows": as_int(row.TotalRows),
            "historical_rows": as_int(row.HistoricalRows),
            "historical_bid_rows": as_int(row.HistoricalBidRows),
            "historical_ask_rows": as_int(row.HistoricalAskRows),
            "live_rows": as_int(row.LiveRows),
            "buckets": {
                "any": sorted(any_buckets),
                "BID": sorted(buckets_by_quote["BID"]),
                "ASK": sorted(buckets_by_quote["ASK"]),
            },
        }
finally:
    conn.close()

os.makedirs(os.path.dirname(out_path), exist_ok=True)
with open(out_path, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, indent=2, sort_keys=True)

print(json.dumps({"ok": True, "out_path": out_path}))
'@

    Push-Location $paths.RepoRoot
    try {
        $output = $code | & $python - 2>&1
        $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
    }
    finally {
        Pop-Location
        $env:TICK_OVERLAP_SYMBOLS = $oldSymbols
        $env:TICK_OVERLAP_FROM_UTC = $oldFrom
        $env:TICK_OVERLAP_TO_UTC = $oldTo
        $env:TICK_OVERLAP_BUCKET_MINUTES = $oldBucket
        $env:TICK_OVERLAP_OUT_PATH = $oldOut
    }

    if ($exitCode -ne 0) {
        $message = $output -join "`n"
        Write-TickDataOpsLog -LogPath $logPath -Level "ERROR" -Message "snapshot failed: $message"
        throw "tick snapshot failed with exit code $exitCode"
    }
}

function New-TickOverlapReport {
    $oldBefore = $env:TICK_OVERLAP_BEFORE_PATH
    $oldAfter = $env:TICK_OVERLAP_AFTER_PATH
    $oldReport = $env:TICK_OVERLAP_REPORT_PATH
    $oldRunId = $env:TICK_OVERLAP_RUN_ID

    $env:TICK_OVERLAP_BEFORE_PATH = $beforePath
    $env:TICK_OVERLAP_AFTER_PATH = $afterPath
    $env:TICK_OVERLAP_REPORT_PATH = $reportPath
    $env:TICK_OVERLAP_RUN_ID = $runId

    $code = @'
from __future__ import annotations

import json
import os
from datetime import datetime, timezone


before_path = os.environ["TICK_OVERLAP_BEFORE_PATH"]
after_path = os.environ["TICK_OVERLAP_AFTER_PATH"]
report_path = os.environ["TICK_OVERLAP_REPORT_PATH"]
run_id = os.environ["TICK_OVERLAP_RUN_ID"]

with open(before_path, "r", encoding="utf-8") as fh:
    before = json.load(fh)
with open(after_path, "r", encoding="utf-8") as fh:
    after = json.load(fh)

symbols = []
totals = {
    "historical_added": 0,
    "historical_bid_added": 0,
    "historical_ask_added": 0,
    "new_buckets_any": 0,
    "new_buckets_bid": 0,
    "new_buckets_ask": 0,
}

for symbol in sorted(after["symbols"]):
    b = before["symbols"].get(symbol, {})
    a = after["symbols"][symbol]

    def delta(name: str) -> int:
        return int(a.get(name, 0) or 0) - int(b.get(name, 0) or 0)

    b_buckets = b.get("buckets", {})
    a_buckets = a.get("buckets", {})

    before_any = set(b_buckets.get("any", []))
    after_any = set(a_buckets.get("any", []))
    before_bid = set(b_buckets.get("BID", []))
    after_bid = set(a_buckets.get("BID", []))
    before_ask = set(b_buckets.get("ASK", []))
    after_ask = set(a_buckets.get("ASK", []))

    row = {
        "symbol": symbol,
        "historical_added": delta("historical_rows"),
        "historical_bid_added": delta("historical_bid_rows"),
        "historical_ask_added": delta("historical_ask_rows"),
        "new_buckets_any": len(after_any - before_any),
        "new_buckets_bid": len(after_bid - before_bid),
        "new_buckets_ask": len(after_ask - before_ask),
        "buckets_any_after": len(after_any),
        "buckets_bid_after": len(after_bid),
        "buckets_ask_after": len(after_ask),
    }
    symbols.append(row)
    for key in totals:
        totals[key] += int(row[key])

report = {
    "run_id": run_id,
    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    "from_utc": after["from_utc"],
    "to_utc": after["to_utc"],
    "bucket_minutes": after["bucket_minutes"],
    "totals": totals,
    "symbols": symbols,
}

os.makedirs(os.path.dirname(report_path), exist_ok=True)
with open(report_path, "w", encoding="utf-8") as fh:
    json.dump(report, fh, indent=2, sort_keys=True)

print(json.dumps(report, sort_keys=True))
'@

    Push-Location $paths.RepoRoot
    try {
        $output = $code | & $python - 2>&1
        $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
    }
    finally {
        Pop-Location
        $env:TICK_OVERLAP_BEFORE_PATH = $oldBefore
        $env:TICK_OVERLAP_AFTER_PATH = $oldAfter
        $env:TICK_OVERLAP_REPORT_PATH = $oldReport
        $env:TICK_OVERLAP_RUN_ID = $oldRunId
    }

    if ($exitCode -ne 0) {
        $message = $output -join "`n"
        Write-TickDataOpsLog -LogPath $logPath -Level "ERROR" -Message "report failed: $message"
        throw "tick overlap report failed with exit code $exitCode"
    }

    return (($output | Select-Object -Last 1) | ConvertFrom-Json)
}

trap {
    $err = $_.Exception.Message
    Write-TickDataOpsLog -LogPath $logPath -Level "ERROR" -Message "daily overlap failed run=$runId error=$err"
    Send-TickOverlapDiscord `
        -Level "ERROR" `
        -Title "Tick daily overlap backfill failed" `
        -Conclusion "Daily overlap backfill did not finish. Tick history may still have an unrepaired recent gap." `
        -Action "Open the tick daily overlap log, fix the failing symbol/window, then re-run the same overlap window." `
        -Details @(
            @("Run", $runId),
            @("Window UTC", "$fromText -> $toText"),
            @("Symbols", ($Symbols -join ",")),
            @("Error", $err)
        ) `
        -Technical @(
            @("Log", $logPath),
            @("Before snapshot", $beforePath),
            @("After snapshot", $afterPath)
        )
    exit 1
}

Write-TickDataOpsLog -LogPath $logPath -Message "daily overlap requested run=$runId symbols=$($Symbols -join ',') window=$fromText->$toText lookback_hours=$LookbackHours chunk_hours=$ChunkHours bucket_minutes=$BucketMinutes request_timeout=${RequestTimeoutSeconds}s"

Send-TickOverlapDiscord `
    -Level "INFO" `
    -Title "Tick daily overlap backfill started" `
    -Conclusion "Daily overlap backfill is starting. The job will re-fetch recent cTrader tick history and only net-new ticks will remain in SQL." `
    -Details @(
        @("Run", $runId),
        @("Window UTC", "$fromText -> $toText"),
        @("Symbols", ($Symbols -join ",")),
        @("Chunk hours", $ChunkHours),
        @("Bucket minutes", $BucketMinutes)
    ) `
    -Technical @(
        @("Log", $logPath),
        @("Request timeout", "${RequestTimeoutSeconds}s"),
        @("Retries", $RetryCount)
    )

if (-not $SkipSymbolSync) {
    Write-Host "Running symbol-sync --apply..." -ForegroundColor Cyan
    $code = Invoke-TickCli -ArgsList @("symbol-sync", "--apply")
    if ($code -ne 0) {
        throw "symbol-sync failed with exit code $code"
    }
}

Write-Host "Capturing before snapshot..." -ForegroundColor Cyan
Invoke-TickSnapshot -OutPath $beforePath

foreach ($symbol in $Symbols) {
    $cursor = $fromUtc
    while ($cursor -lt $toUtc) {
        $chunkEnd = $cursor.AddHours($ChunkHours)
        if ($chunkEnd -gt $toUtc) {
            $chunkEnd = $toUtc
        }

        $chunkFrom = Format-UtcIso -Value $cursor
        $chunkTo = Format-UtcIso -Value $chunkEnd
        $ok = $false

        for ($attempt = 1; $attempt -le $RetryCount; $attempt++) {
            Write-Host "Backfill $symbol $chunkFrom -> $chunkTo attempt $attempt/$RetryCount" -ForegroundColor Cyan
            Write-TickDataOpsLog -LogPath $logPath -Message "backfill start run=$runId symbol=$symbol from=$chunkFrom to=$chunkTo attempt=$attempt"

            $exitCode = Invoke-TickCli -ArgsList @(
                "backfill",
                "--symbols", $symbol,
                "--from", $chunkFrom,
                "--to", $chunkTo,
                "--request-timeout", [string]$RequestTimeoutSeconds
            )

            if ($exitCode -eq 0) {
                $ok = $true
                Write-TickDataOpsLog -LogPath $logPath -Message "backfill done run=$runId symbol=$symbol from=$chunkFrom to=$chunkTo attempt=$attempt"
                break
            }

            Write-TickDataOpsLog -LogPath $logPath -Level "WARN" -Message "backfill failed run=$runId symbol=$symbol from=$chunkFrom to=$chunkTo attempt=$attempt exit_code=$exitCode"
            if ($attempt -lt $RetryCount) {
                Start-Sleep -Seconds $RetrySleepSeconds
            }
        }

        if (-not $ok) {
            Write-TickDataOpsLog -LogPath $logPath -Level "ERROR" -Message "backfill exhausted retries run=$runId symbol=$symbol from=$chunkFrom to=$chunkTo"
            throw "Backfill failed after retries: $symbol $chunkFrom -> $chunkTo"
        }

        if ($BetweenChunksSleepSeconds -gt 0) {
            Start-Sleep -Seconds $BetweenChunksSleepSeconds
        }
        $cursor = $chunkEnd
    }
}

Write-Host "Capturing after snapshot..." -ForegroundColor Cyan
Invoke-TickSnapshot -OutPath $afterPath
$report = New-TickOverlapReport

(Get-Content -LiteralPath $reportPath -Raw) | Add-Content -LiteralPath $reportJsonlPath

Write-Host ""
Write-Host "== DAILY OVERLAP TICK REPORT ==" -ForegroundColor Cyan
Write-Host "Run: $($report.run_id)"
Write-Host "Window UTC: $($report.from_utc) -> $($report.to_utc)"
Write-Host "Bucket: $($report.bucket_minutes) minute(s)"
Write-Host ""

$rows = foreach ($item in $report.symbols) {
    [pscustomobject]@{
        Symbol = $item.symbol
        HistAdded = $item.historical_added
        BidAdded = $item.historical_bid_added
        AskAdded = $item.historical_ask_added
        NewBuckets = $item.new_buckets_any
        NewBidBuckets = $item.new_buckets_bid
        NewAskBuckets = $item.new_buckets_ask
    }
}
$rows | Format-Table -AutoSize

Write-Host ""
Write-Host ("Totals: historical_added={0:N0}; bid_added={1:N0}; ask_added={2:N0}; new_{3}m_buckets={4:N0}" -f `
    $report.totals.historical_added,
    $report.totals.historical_bid_added,
    $report.totals.historical_ask_added,
    $report.bucket_minutes,
    $report.totals.new_buckets_any)
Write-Host "Report JSON: $reportPath"
Write-Host "Report JSONL: $reportJsonlPath"
Write-TickDataOpsLog -LogPath $logPath -Message "daily overlap completed run=$runId historical_added=$($report.totals.historical_added) new_buckets=$($report.totals.new_buckets_any) report=$reportPath"

$topSymbols = @(
    $report.symbols |
        Sort-Object -Property historical_added -Descending |
        Select-Object -First 8 |
        ForEach-Object {
            [pscustomobject]@{
                label = $_.symbol
                value = ("ticks={0:N0}; bid={1:N0}; ask={2:N0}; new_{3}m_buckets={4:N0}" -f `
                    $_.historical_added,
                    $_.historical_bid_added,
                    $_.historical_ask_added,
                    $report.bucket_minutes,
                    $_.new_buckets_any)
            }
        }
)

Send-TickOverlapDiscord `
    -Level "INFO" `
    -Title "Tick daily overlap backfill completed" `
    -Conclusion ("Daily overlap completed. Net-new historical tick rows added: {0:N0}. Newly covered {1}m buckets: {2:N0}." -f `
        $report.totals.historical_added,
        $report.bucket_minutes,
        $report.totals.new_buckets_any) `
    -Action "No action is needed if this was the scheduled run. Review the report if the new tick count is unusually high or zero for several days." `
    -Details @(
        @("Run", $runId),
        @("Window UTC", "$($report.from_utc) -> $($report.to_utc)"),
        @("Historical added", ("{0:N0}" -f $report.totals.historical_added)),
        @("BID added", ("{0:N0}" -f $report.totals.historical_bid_added)),
        @("ASK added", ("{0:N0}" -f $report.totals.historical_ask_added)),
        @("New buckets", ("{0:N0}" -f $report.totals.new_buckets_any))
    ) `
    -Technical (@(
        @("Report", $reportPath),
        @("JSONL", $reportJsonlPath)
    ) + $topSymbols)
