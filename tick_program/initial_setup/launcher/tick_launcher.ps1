$ErrorActionPreference = "Stop"

$AppRoot  = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$PythonExe = (Get-Command python -ErrorAction Stop).Source

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Write-Header {
    Clear-Host
    $svc = Get-SupervisorStatus
    $svcLine = if ($svc.active) {
        "RUNNING  (PID $($svc.pid)  started $($svc.started))"
    } else {
        "STOPPED"
    }
    Write-Host "============================================================"
    Write-Host " SEN05 Tick Engine"
    Write-Host "============================================================"
    Write-Host ("Root    : {0}" -f $AppRoot)
    Write-Host ("Python  : {0}" -f $PythonExe)
    Write-Host ("Service : {0}" -f $svcLine)
    Write-Host ""
}

function Write-Menu {
    Write-Host "[Pre-check / Health]"
    Write-Host "  1. System Health Check"
    Write-Host "  2. Show Config"
    Write-Host "  3. Token Status"
    Write-Host "  4. Tick Data Viewer  (http://127.0.0.1:8060)"
    Write-Host ""
    Write-Host "[cTrader OAuth]"
    Write-Host "  5. OAuth Login  (browser)"
    Write-Host "  6. Refresh Token"
    Write-Host "  7. Account List + Auth Check"
    Write-Host "  8. Symbol Sync  (preview)"
    Write-Host "  9. Symbol Sync  (apply)"
    Write-Host ""
    Write-Host "[Tick Engine Runtime]"
    Write-Host " 10. Start Tick Engine 24/7"
    Write-Host " 11. Graceful Stop Runtime Jobs"
    Write-Host ""
    Write-Host "[Manual Data Operations]"
    Write-Host "  Note: stop the service (11) before running manual jobs."
    Write-Host " 12. Batched Backfill From Date -> Now"
    Write-Host " 13. History Depth Probe From Date -> Now"
    Write-Host " 14. Maintenance Sweep"
    Write-Host " 15. Reset Tick Data  (danger)"
    Write-Host ""
    Write-Host "[Logs / Runtime]"
    Write-Host " 16. Tail System Log"
    Write-Host " 17. Tail Operation Log  (mode 10)"
    Write-Host " 18. Tail Manual Log     (mode 12/13)"
    Write-Host " 19. Tail All Logs"
    Write-Host " 20. Clear Logs"
    Write-Host ""
    Write-Host "  0. Exit"
    Write-Host ""
}

function Pause-Tick {
    Write-Host ""
    Read-Host "Press Enter to continue" | Out-Null
}

function Quote-Str {
    param([AllowEmptyString()][string]$Value)
    return "'" + ($Value -replace "'", "''") + "'"
}

function New-PythonCommand {
    param([string[]]$PythonArgs)
    $effectiveArgs = @()
    if ($PythonArgs -notcontains "-u") { $effectiveArgs += "-u" }
    if ($PythonArgs -notcontains "-B") { $effectiveArgs += "-B" }
    $effectiveArgs += $PythonArgs
    $parts = @((Quote-Str $PythonExe)) + ($effectiveArgs | ForEach-Object { Quote-Str $_ })
    return "& " + ($parts -join " ")
}

function Add-TickLogLine {
    param(
        [string]$Path,
        [string]$Value = ""
    )
    if (-not $Path) { return }
    Add-Content -LiteralPath $Path -Encoding utf8 -Value $Value
}

function Write-TickLogRunStart {
    param(
        [string]$Path,
        [string]$RunType,
        [string]$Command,
        [string]$Title = ""
    )
    if (-not $Path) { return }
    $stamp = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
    Add-TickLogLine -Path $Path -Value ""
    Add-TickLogLine -Path $Path -Value ("=" * 88)
    Add-TickLogLine -Path $Path -Value ("Run started  : {0}" -f $stamp)
    Add-TickLogLine -Path $Path -Value ("Run type     : {0}" -f $RunType)
    if ($Title) {
        Add-TickLogLine -Path $Path -Value ("Window title : {0}" -f $Title)
    }
    Add-TickLogLine -Path $Path -Value ("Command      : {0}" -f $Command)
    Add-TickLogLine -Path $Path -Value ("-" * 88)
    Add-TickLogLine -Path $Path -Value "Output"
    Add-TickLogLine -Path $Path -Value ("-" * 88)
}

function Write-TickLogRunEnd {
    param(
        [string]$Path,
        [int]$ExitCode
    )
    if (-not $Path) { return }
    $stamp = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
    $result = $(if ($ExitCode -eq 0) { "OK" } else { "Failed" })
    Add-TickLogLine -Path $Path -Value ("-" * 88)
    Add-TickLogLine -Path $Path -Value ("Run finished : {0}" -f $stamp)
    Add-TickLogLine -Path $Path -Value ("Result       : {0} (exit code {1})" -f $result, $ExitCode)
    Add-TickLogLine -Path $Path -Value ("=" * 88)
}

function Start-TickWindow {
    param(
        [string]$Title,
        [string[]]$PythonArgs,
        [hashtable]$ExtraEnv = @{}
    )
    $safeTitle = $Title -replace "'", "''"
    $safeRoot  = $AppRoot -replace "'", "''"
    $cmdLine   = New-PythonCommand -PythonArgs $PythonArgs
    $safeCmd   = $cmdLine -replace "'", "''"
    $envLines  = ""
    $jobLog = $null
    if (-not $ExtraEnv.ContainsKey("PYTHONUNBUFFERED")) {
        $ExtraEnv["PYTHONUNBUFFERED"] = "1"
    }
    if ($ExtraEnv.ContainsKey("TICK_ENGINE_JOB_LOG")) {
        $jobLog = [string]$ExtraEnv["TICK_ENGINE_JOB_LOG"]
        $ExtraEnv["TICK_ENGINE_DISABLE_FILE_LOG"] = "1"
    }
    foreach ($key in $ExtraEnv.Keys) {
        $safeKey = $key -replace "'", "''"
        $safeVal = [string]$ExtraEnv[$key] -replace "'", "''"
        $envLines += "`$env:$safeKey = '$safeVal'`r`n"
    }
    $logLines = ""
    if ($jobLog) {
        $safeLog = $jobLog -replace "'", "''"
        $logLines = @"
`$jobLog = '$safeLog'
New-Item -ItemType Directory -Path (Split-Path `$jobLog -Parent) -Force | Out-Null
`$stamp = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
Add-Content -LiteralPath `$jobLog -Encoding utf8 -Value ''
Add-Content -LiteralPath `$jobLog -Encoding utf8 -Value ('=' * 88)
Add-Content -LiteralPath `$jobLog -Encoding utf8 -Value ('Run started  : ' + `$stamp)
Add-Content -LiteralPath `$jobLog -Encoding utf8 -Value 'Run type     : Separate window'
Add-Content -LiteralPath `$jobLog -Encoding utf8 -Value ('Window title : ' + `$windowTitle)
Add-Content -LiteralPath `$jobLog -Encoding utf8 -Value ('Command      : ' + `$commandText)
Add-Content -LiteralPath `$jobLog -Encoding utf8 -Value ('-' * 88)
Add-Content -LiteralPath `$jobLog -Encoding utf8 -Value 'Output'
Add-Content -LiteralPath `$jobLog -Encoding utf8 -Value ('-' * 88)
Invoke-Expression `$commandText 2>&1 | ForEach-Object {
    `$line = [string]`$_
    Write-Host `$line
    Add-Content -LiteralPath `$jobLog -Encoding utf8 -Value `$line
}
"@
        $logExitLines = @"
`$stamp = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
`$result = if (`$exitCode -eq 0) { 'OK' } else { 'Failed' }
Add-Content -LiteralPath `$jobLog -Encoding utf8 -Value ('-' * 88)
Add-Content -LiteralPath `$jobLog -Encoding utf8 -Value ('Run finished : ' + `$stamp)
Add-Content -LiteralPath `$jobLog -Encoding utf8 -Value ('Result       : ' + `$result + ' (exit code ' + `$exitCode + ')')
Add-Content -LiteralPath `$jobLog -Encoding utf8 -Value ('=' * 88)
"@
    } else {
        $logLines = "Invoke-Expression `$commandText"
        $logExitLines = ""
    }
    $script = @"
`$windowTitle = '$safeTitle'
`$commandText = '$safeCmd'
`$Host.UI.RawUI.WindowTitle = `$windowTitle
Set-Location -LiteralPath '$safeRoot'
$envLines
Write-Host ''
Write-Host ("SEN05 Tick Engine - {0}" -f `$windowTitle)
Write-Host ''
Write-Host ("Command: {0}" -f `$commandText)
Write-Host ''
$logLines
`$exitCode = if (`$null -ne `$LASTEXITCODE) { `$LASTEXITCODE } else { 0 }
$logExitLines
Write-Host ''
Write-Host ("Process exited with code {0}." -f `$exitCode)
Read-Host 'Press Enter to close this window' | Out-Null
exit `$exitCode
"@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script))
    Start-Process powershell.exe -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", $encoded
    )
}

function Invoke-TickInline {
    param(
        [string[]]$PythonArgs,
        [hashtable]$ExtraEnv = @{},
        [switch]$NoPause
    )
    $code = 0
    Push-Location $AppRoot
    $oldErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $effectiveArgs = @()
        if ($PythonArgs -notcontains "-u") { $effectiveArgs += "-u" }
        if ($PythonArgs -notcontains "-B") { $effectiveArgs += "-B" }
        $effectiveArgs += $PythonArgs
        if (-not $ExtraEnv.ContainsKey("PYTHONUNBUFFERED")) {
            $ExtraEnv["PYTHONUNBUFFERED"] = "1"
        }
        $jobLog = $null
        if ($ExtraEnv.ContainsKey("TICK_ENGINE_JOB_LOG")) {
            $jobLog = [string]$ExtraEnv["TICK_ENGINE_JOB_LOG"]
            $ExtraEnv["TICK_ENGINE_DISABLE_FILE_LOG"] = "1"
            New-Item -ItemType Directory -Path (Split-Path $jobLog -Parent) -Force | Out-Null
            Write-TickLogRunStart `
                -Path $jobLog `
                -RunType "Inline command" `
                -Command (($effectiveArgs) -join " ")
        }
        $oldEnv = @{}
        foreach ($key in $ExtraEnv.Keys) {
            $oldEnv[$key] = [Environment]::GetEnvironmentVariable($key, "Process")
            [Environment]::SetEnvironmentVariable($key, [string]$ExtraEnv[$key], "Process")
        }
        & $PythonExe @effectiveArgs 2>&1 | ForEach-Object {
            $line = [string]$_
            Write-Host $line
            if ($jobLog) {
                Add-Content -LiteralPath $jobLog -Encoding utf8 -Value $line
            }
        }
        $code = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 }
        if ($jobLog) {
            Write-TickLogRunEnd -Path $jobLog -ExitCode $code
        }
        Write-Host ""
        Write-Host ("Exit code: {0}" -f $code)
    } finally {
        $ErrorActionPreference = $oldErrorActionPreference
        if ($null -ne $oldEnv) {
            foreach ($key in $oldEnv.Keys) {
                [Environment]::SetEnvironmentVariable($key, $oldEnv[$key], "Process")
            }
        }
        Pop-Location
    }
    if (-not $NoPause) {
        Pause-Tick
        return
    }
    return $code
}

function Invoke-TickQuiet {
    param([string[]]$PythonArgs)
    Push-Location $AppRoot
    try {
        $effectiveArgs = @()
        if ($PythonArgs -notcontains "-u") { $effectiveArgs += "-u" }
        if ($PythonArgs -notcontains "-B") { $effectiveArgs += "-B" }
        $effectiveArgs += $PythonArgs
        & $PythonExe @effectiveArgs
        return $(if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 })
    } finally {
        Pop-Location
    }
}

function Start-TickServiceBackground {
    $oldUnbuffered = [Environment]::GetEnvironmentVariable("PYTHONUNBUFFERED", "Process")
    $oldConsoleLog = [Environment]::GetEnvironmentVariable("TICK_ENGINE_SERVICE_CONSOLE_LOG", "Process")
    try {
        [Environment]::SetEnvironmentVariable("PYTHONUNBUFFERED", "1", "Process")
        [Environment]::SetEnvironmentVariable("TICK_ENGINE_SERVICE_CONSOLE_LOG", $null, "Process")
        return Start-Process `
            -FilePath $PythonExe `
            -ArgumentList @("-B", "-m", "tick_engine", "service") `
            -WorkingDirectory $AppRoot `
            -WindowStyle Hidden `
            -PassThru
    } finally {
        [Environment]::SetEnvironmentVariable("PYTHONUNBUFFERED", $oldUnbuffered, "Process")
        [Environment]::SetEnvironmentVariable("TICK_ENGINE_SERVICE_CONSOLE_LOG", $oldConsoleLog, "Process")
    }
}

function Start-LogTailWindow {
    param([string]$RelativePath, [string]$Label)
    $path      = Join-Path $AppRoot $RelativePath
    $safeTitle = $Label -replace "'", "''"
    $safePath  = $path  -replace "'", "''"
    $script = @"
`$Host.UI.RawUI.WindowTitle = '$safeTitle'
Write-Host ''
Write-Host '$Label'
Write-Host 'Path: $safePath'
Write-Host ''
if (-not (Test-Path -LiteralPath '$safePath')) {
    Write-Host 'Log file not found. Waiting for it to be created...'
    while (-not (Test-Path -LiteralPath '$safePath')) { Start-Sleep -Seconds 2 }
}
Get-Content -LiteralPath '$safePath' -Tail 200 -Wait
"@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script))
    Start-Process powershell.exe -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", $encoded
    )
}

function Format-LogSize {
    param([long]$Bytes)
    if ($Bytes -ge 1GB) { return ("{0:N2} GB" -f ($Bytes / 1GB)) }
    if ($Bytes -ge 1MB) { return ("{0:N2} MB" -f ($Bytes / 1MB)) }
    if ($Bytes -ge 1KB) { return ("{0:N2} KB" -f ($Bytes / 1KB)) }
    return ("{0} B" -f $Bytes)
}

function Get-LogTargets {
    return @(
        [pscustomobject]@{
            Key = "SYSTEM"
            Group = "SYSTEM"
            Label = "System modes, auth, dashboard, health"
            RelativePath = "runtime\logs\system.log"
        },
        [pscustomobject]@{
            Key = "OPERATION"
            Group = "OPERATION"
            Label = "Mode 10 service and scheduled jobs"
            RelativePath = "runtime\logs\operation.log"
        },
        [pscustomobject]@{
            Key = "MANUAL"
            Group = "MANUAL"
            Label = "Mode 12/13 manual data jobs"
            RelativePath = "runtime\logs\manual.log"
        }
    )
}

function Clear-OneLogFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType Directory -Path (Split-Path $Path -Parent) -Force | Out-Null
        New-Item -ItemType File -Path $Path -Force | Out-Null
        return "created-empty"
    }

    try {
        $stream = [System.IO.File]::Open(
            $Path,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::ReadWrite
        )
        try {
            $stream.SetLength(0)
        } finally {
            $stream.Close()
        }
        return "cleared"
    } catch {
        Clear-Content -LiteralPath $Path -Force
        return "cleared"
    }
}

function Clear-TickLogs {
    $targets = Get-LogTargets

    Write-Host ""
    Write-Host "Clear logs" -ForegroundColor Cyan
    Write-Host "This clears log contents only. It does not touch config, token cache, DB, spool, or runtime state."
    Write-Host ""
    Write-Host "Current log files:"
    foreach ($target in $targets) {
        $path = Join-Path $AppRoot $target.RelativePath
        if (Test-Path -LiteralPath $path) {
            $item = Get-Item -LiteralPath $path
            Write-Host ("  {0,-9} {1,-58} {2,10}" -f $target.Key, $target.RelativePath, (Format-LogSize $item.Length))
        } else {
            Write-Host ("  {0,-9} {1,-58} {2,10}" -f $target.Key, $target.RelativePath, "missing")
        }
    }
    Write-Host ""
    Write-Host "  ALL        Clear all logs"
    Write-Host "  SYSTEM     Clear system.log"
    Write-Host "  OPERATION  Clear operation.log"
    Write-Host "  MANUAL     Clear manual.log"
    Write-Host "  C          Cancel"
    Write-Host ""
    $choiceRaw = Read-Host "Clear which logs?"
    $choice = $(if ($null -eq $choiceRaw) { "" } else { $choiceRaw.Trim().ToUpperInvariant() })
    if (-not $choice -or $choice -eq "C") {
        Write-Host "Cancelled." -ForegroundColor Yellow
        Pause-Tick
        return
    }

    $selected = @()
    if ($choice -eq "ALL") {
        $selected = $targets
    } else {
        $selected = @($targets | Where-Object { $_.Key -eq $choice })
    }

    if ($selected.Count -eq 0) {
        Write-Host "Unknown log selection." -ForegroundColor Yellow
        Pause-Tick
        return
    }

    Write-Host ""
    Write-Host "The following log contents will be cleared:" -ForegroundColor Yellow
    foreach ($target in $selected) {
        Write-Host ("  {0}" -f $target.RelativePath)
    }
    Write-Host ""
    $confirmRaw = Read-Host "Type CLEAR to confirm"
    $confirm = $(if ($null -eq $confirmRaw) { "" } else { $confirmRaw.Trim() })
    if ($confirm -ne "CLEAR") {
        Write-Host "Cancelled." -ForegroundColor Yellow
        Pause-Tick
        return
    }

    foreach ($target in $selected) {
        $path = Join-Path $AppRoot $target.RelativePath
        try {
            $result = Clear-OneLogFile -Path $path
            Write-Host ("{0,-58} {1}" -f $target.RelativePath, $result) -ForegroundColor Green
        } catch {
            Write-Host ("{0,-58} failed: {1}" -f $target.RelativePath, $_.Exception.Message) -ForegroundColor Red
        }
    }
    Pause-Tick
}

# ---------------------------------------------------------------------------
# Supervisor status (reads runtime\run\supervisor.pid)
# ---------------------------------------------------------------------------

function Get-SupervisorStatus {
    $pidFile = Join-Path $AppRoot "runtime\run\supervisor.pid"
    if (-not (Test-Path -LiteralPath $pidFile)) {
        return [pscustomobject]@{ active = $false; pid = $null; started = $null }
    }
    try {
        $pidVal = [int](Get-Content -LiteralPath $pidFile -Raw).Trim()
        $proc   = Get-Process -Id $pidVal -ErrorAction SilentlyContinue
        if ($proc) {
            $started = $proc.StartTime.ToString("yyyy-MM-dd HH:mm:ss")
            return [pscustomobject]@{ active = $true; pid = $pidVal; started = $started }
        }
    } catch {}
    return [pscustomobject]@{ active = $false; pid = $null; started = $null }
}

function Get-HistoryLockStatus {
    $lockFile = Join-Path $AppRoot "runtime\run\locks\ctrader-history.lock"
    if (-not (Test-Path -LiteralPath $lockFile)) {
        return [pscustomobject]@{ exists = $false; activeLocal = $false; remote = $false; pid = $null; host = $null; owner = $null; started = $null; path = $lockFile }
    }
    try {
        $payload = Get-Content -LiteralPath $lockFile -Raw | ConvertFrom-Json
        $pidVal = [int]$payload.pid
        $lockHost = [string]$payload.host
        $remote = ($lockHost -and ($lockHost.ToLowerInvariant() -ne $env:COMPUTERNAME.ToLowerInvariant()))
        $proc = $null
        if (-not $remote) {
            $proc = Get-Process -Id $pidVal -ErrorAction SilentlyContinue
        }
        return [pscustomobject]@{
            exists = $true
            activeLocal = [bool]$proc
            remote = [bool]$remote
            pid = $pidVal
            host = $lockHost
            owner = [string]$payload.label
            started = [string]$payload.started_at_utc
            path = $lockFile
        }
    } catch {
        return [pscustomobject]@{ exists = $true; activeLocal = $false; remote = $false; pid = $null; host = $null; owner = $null; started = $null; path = $lockFile }
    }
}

function Write-CancelFile {
    param([string]$Name, [string]$Reason)
    $cancelFile = Join-Path $AppRoot ("runtime\run\cancel\{0}.cancel" -f $Name)
    New-Item -ItemType Directory -Path (Split-Path $cancelFile -Parent) -Force | Out-Null
    $payload = [ordered]@{
        reason = $Reason
        requested_at_utc = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffffffK")
        requested_by = "tick_launcher"
        requested_by_pid = $PID
    }
    $payload | ConvertTo-Json -Compress | Out-File -LiteralPath $cancelFile -Encoding utf8 -Force
    return $cancelFile
}

# ---------------------------------------------------------------------------
# Graceful stop — drop the supervisor.stop sentinel
# ---------------------------------------------------------------------------

function Get-LocalTickRuntimeProcesses {
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -match '^python' -and
            $_.CommandLine -like '*tick_engine*' -and
            $_.CommandLine -notlike '*datacheck*'
        } |
        Select-Object ProcessId, Name, CommandLine
}

function Stop-LocalTickRuntimeProcesses {
    param([object[]]$Jobs)
    $seen = @{}
    foreach ($job in $Jobs) {
        if (-not $job.ProcessId) { continue }
        $key = [string]$job.ProcessId
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        Stop-Process -Id $job.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-GracefulStop {
    $svc = Get-SupervisorStatus
    $history = Get-HistoryLockStatus
    $hadWork = $svc.active -or $history.exists

    Write-Host ""
    if ($svc.active) {
        $stopFile = Join-Path $AppRoot "runtime\run\supervisor.stop"
        Write-Host ("Sending graceful stop to service PID {0}..." -f $svc.pid) -ForegroundColor Yellow
        "" | Out-File -LiteralPath $stopFile -Encoding utf8 -Force
    } else {
        Write-Host "Backfill service is not running." -ForegroundColor Yellow
    }

    $cancelNames = @(
        "manual-backfill",
        "scheduled-startup-catchup-backfill",
        "scheduled-frequent-backfill",
        "scheduled-hourly-repair",
        "scheduled-daily-deep-repair",
        "scheduled-spool-drain",
        "scheduled-build-activity-profile"
    )
    foreach ($name in $cancelNames) {
        $path = Write-CancelFile -Name $name -Reason "operator graceful stop"
        Write-Host ("Cancel sentinel: {0}" -f $path)
    }
    $localJobsAtCancel = @(Get-LocalTickRuntimeProcesses)
    if ($localJobsAtCancel.Count -gt 0) {
        Write-Host ""
        Write-Host ("Detected {0} local tick_engine runtime process(es)." -f $localJobsAtCancel.Count) -ForegroundColor Yellow
        Write-Host "If these were started before cancel support was added, they may require FORCE after the wait."
        foreach ($job in $localJobsAtCancel) {
            Write-Host ("  PID {0}: {1}" -f $job.ProcessId, $job.CommandLine)
        }
    }

    $hadWork = $hadWork -or ($localJobsAtCancel.Count -gt 0)
    if (-not $hadWork) {
        Write-Host "No active service or history lock was detected." -ForegroundColor Green
        Pause-Tick
        return
    }

    $graceSeconds = 45
    Write-Host ("Waiting for service/manual history jobs to exit gracefully (max {0}s)..." -f $graceSeconds)
    $deadline = (Get-Date).AddSeconds($graceSeconds)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 2
        $still = Get-SupervisorStatus
        $history = Get-HistoryLockStatus
        if (-not $still.active -and -not $history.exists) {
            Write-Host "Runtime jobs stopped cleanly." -ForegroundColor Green
            Pause-Tick
            return
        }
        Write-Host -NoNewline "."
    }
    Write-Host ""
    Write-Host ("Runtime did not stop within {0}s. Forcing local tick_engine runtime now..." -f $graceSeconds) -ForegroundColor Yellow
    $history = Get-HistoryLockStatus
    $localJobs = @(Get-LocalTickRuntimeProcesses)
    if ($localJobs.Count -eq 0 -and $history.exists -and $history.activeLocal -and $history.pid) {
        $localJobs = @(
            [pscustomobject]@{
                ProcessId = $history.pid
                Name = "python.exe"
                CommandLine = "history lock owner"
            }
        )
    }
    if ($history.exists) {
        Write-Host ("History lock remains: owner={0} pid={1} host={2}" -f $history.owner, $history.pid, $history.host) -ForegroundColor Yellow
    }
    if ($localJobs.Count -gt 0) {
        Write-Host "Stopping local tick_engine runtime processes:" -ForegroundColor Yellow
        foreach ($job in $localJobs) {
            Write-Host ("  PID {0}: {1}" -f $job.ProcessId, $job.CommandLine)
        }
        Stop-LocalTickRuntimeProcesses -Jobs $localJobs
        Start-Sleep -Seconds 2
    } else {
        Write-Host "No local tick_engine runtime process was found to force-stop." -ForegroundColor Yellow
    }
    $historyAfterForce = Get-HistoryLockStatus
    if ($historyAfterForce.exists -and -not $historyAfterForce.remote -and -not $historyAfterForce.activeLocal) {
        Remove-Item -LiteralPath $historyAfterForce.path -Force -ErrorAction SilentlyContinue
        Write-Host "Removed stale local history lock." -ForegroundColor Yellow
    }
    $remainingJobs = @(Get-LocalTickRuntimeProcesses)
    $remainingHistory = Get-HistoryLockStatus
    if ($remainingJobs.Count -eq 0 -and (-not $remainingHistory.exists -or (-not $remainingHistory.activeLocal -and -not $remainingHistory.remote))) {
        Write-Host "Runtime jobs are stopped." -ForegroundColor Green
        try {
            Invoke-TickQuiet -PythonArgs @("-m", "tick_engine", "repair-stale-runs") | Out-Null
        } catch {
            Write-Host "Stale run repair could not be completed; run Maintenance Sweep later." -ForegroundColor Yellow
        }
    } elseif ($remainingHistory.remote) {
        Write-Host ("A remote history lock remains on host {0}; run mode 11 inside that machine." -f $remainingHistory.host) -ForegroundColor Red
    } else {
        Write-Host "Some runtime process may still be running. Re-run mode 1 to inspect." -ForegroundColor Red
    }
    Write-Host "Check manual.log and operation.log for final status."
    Pause-Tick
}

# ---------------------------------------------------------------------------
# Guard: warn if service is running before manual job
# ---------------------------------------------------------------------------

function Confirm-ManualReady {
    param([string]$Title)
    $svc = Get-SupervisorStatus
    $history = Get-HistoryLockStatus
    if (-not $svc.active -and -not $history.exists) { return $true }

    Write-Host ""
    if ($svc.active) {
        Write-Host ("WARNING: Tick Engine service is running (PID {0})." -f $svc.pid) -ForegroundColor Yellow
    }
    if ($history.exists) {
        Write-Host ("WARNING: History job lock exists owner={0} pid={1} host={2} started={3}." -f $history.owner, $history.pid, $history.host, $history.started) -ForegroundColor Yellow
    }
    Write-Host ("Requested : {0}" -f $Title)
    Write-Host ""
    Write-Host "Manual jobs compete for the cTrader history lock."
    Write-Host "Recommended: stop active runtime jobs first (option 11), then run this job."
    Write-Host ""
    Write-Host "  S  Skip this request and leave the service running"
    Write-Host "  G  Graceful stop the service, then run this job"
    Write-Host "  F  Force run anyway (risk: lock contention)"
    $answerRaw = Read-Host "Choice"
    $answer = $(if ($null -eq $answerRaw) { "" } else { $answerRaw.Trim().ToUpperInvariant() })

    if ($answer -eq "G") {
        Invoke-GracefulStop
        $still = Get-SupervisorStatus
        $historyAfter = Get-HistoryLockStatus
        if ($still.active -or $historyAfter.exists) {
            Write-Host "Runtime still active. Aborting." -ForegroundColor Red
            Pause-Tick
            return $false
        }
        return $true
    }
    if ($answer -eq "F") {
        Write-Host "Proceeding with service still running." -ForegroundColor Yellow
        return $true
    }
    Write-Host "Skipped." -ForegroundColor Yellow
    Pause-Tick
    return $false
}

# ---------------------------------------------------------------------------
# Date range helpers
# ---------------------------------------------------------------------------

function Get-UtcNowCliValue {
    return [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss'Z'")
}

function Convert-OperatorUtcDate {
    param([string]$Value)
    $text = $(if ($null -eq $Value) { "" } else { $Value.Trim() })
    if (-not $text) {
        throw "date is required"
    }
    if ($text -match "Z$|[+-]\d{2}:\d{2}$") {
        return ([DateTimeOffset]::Parse(
            $text,
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::AssumeUniversal
        )).UtcDateTime
    }
    return [DateTime]::Parse(
        $text,
        [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::AssumeUniversal -bor [Globalization.DateTimeStyles]::AdjustToUniversal
    )
}

function Get-StartDateInput {
    param([string]$Prompt)
    $fromRaw = Read-Host $Prompt
    $from = $(if ($null -eq $fromRaw) { "" } else { $fromRaw.Trim() })
    if (-not $from) {
        Write-Host "Start date is required. Cancelled." -ForegroundColor Yellow
        Pause-Tick
        return $null
    }
    try {
        [void](Convert-OperatorUtcDate $from)
    } catch {
        Write-Host ("Invalid start date: {0}" -f $_.Exception.Message) -ForegroundColor Yellow
        Write-Host "Use YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ." -ForegroundColor Yellow
        Pause-Tick
        return $null
    }
    return $from
}

# ---------------------------------------------------------------------------
# Backfill: ask start date only; end is latest UTC now
# ---------------------------------------------------------------------------

function Start-BackfillFlow {
    if (-not (Confirm-ManualReady -Title "Backfill Range")) { return }

    Write-Host ""
    Write-Host "Backfill range: start date -> latest UTC now"
    Write-Host "Format: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ"
    $from = Get-StartDateInput "--from"
    if (-not $from) { return }
    $to = Get-UtcNowCliValue
    Write-Host ("--to   {0}  (auto)" -f $to) -ForegroundColor Cyan

    $batchRaw = Read-Host "Batch minutes (blank = 60)"
    $batchText = $(if ($null -eq $batchRaw) { "" } else { $batchRaw.Trim() })
    if (-not $batchText) { $batchText = "60" }
    try {
        $batchMinutes = [int]$batchText
        if ($batchMinutes -le 0) { throw "Batch minutes must be greater than 0." }
    } catch {
        Write-Host "Invalid batch minutes. Cancelled." -ForegroundColor Yellow
        Pause-Tick
        return
    }

    $overlapRaw = Read-Host "Overlap seconds (blank = 60)"
    $overlapText = $(if ($null -eq $overlapRaw) { "" } else { $overlapRaw.Trim() })
    if (-not $overlapText) { $overlapText = "60" }
    try {
        $overlapSeconds = [int]$overlapText
        if ($overlapSeconds -lt 0) { throw "Overlap seconds must be >= 0." }
    } catch {
        Write-Host "Invalid overlap seconds. Cancelled." -ForegroundColor Yellow
        Pause-Tick
        return
    }

    $symRaw = Read-Host "Symbols (blank = all, e.g. US30 or US30,GOLD)"
    $sym    = $(if ($null -eq $symRaw) { "" } else { $symRaw.Trim() })

    $args = @(
        "-m", "tick_engine", "backfill-batched",
        "--from", $from,
        "--to", $to,
        "--batch-minutes", "$batchMinutes",
        "--overlap-seconds", "$overlapSeconds",
        "--wait-lock-seconds", "300",
        "--sleep-seconds", "1",
        "--notify-summary"
    )
    if ($sym) { $args += @("--symbols") + ($sym -split "[,\s]+") }

    $title = "Batched Backfill $from -> $to"
    $jobLog = Join-Path $AppRoot "runtime\logs\manual.log"
    $cancelFile = Join-Path $AppRoot "runtime\run\cancel\manual-backfill.cancel"
    Remove-Item -LiteralPath $cancelFile -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path (Split-Path $jobLog -Parent) -Force | Out-Null
    New-Item -ItemType Directory -Path (Split-Path $cancelFile -Parent) -Force | Out-Null
    Write-Host ("Log    {0}" -f $jobLog) -ForegroundColor Cyan
    Write-Host ("Cancel {0}" -f $cancelFile) -ForegroundColor Cyan
    Start-TickWindow -Title $title -PythonArgs $args -ExtraEnv @{
        TICK_ENGINE_JOB_LOG = $jobLog
        TICK_ENGINE_CANCEL_FILE = $cancelFile
    }
    Start-LogTailWindow "runtime\logs\manual.log" "Manual Log"
}

function Start-HistoryDepthFlow {
    if (-not (Confirm-ManualReady -Title "History Depth Probe")) { return }

    Write-Host ""
    Write-Host "History depth probe: start date -> latest UTC now"
    Write-Host "Format: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ"
    $from = Get-StartDateInput "--from"
    if (-not $from) { return }

    $toUtc = [DateTime]::UtcNow
    try {
        $fromUtc = Convert-OperatorUtcDate $from
    } catch {
        Write-Host ("Invalid start date: {0}" -f $_.Exception.Message) -ForegroundColor Yellow
        Pause-Tick
        return
    }
    if ($fromUtc -ge $toUtc) {
        Write-Host "Start date must be earlier than current UTC time. Cancelled." -ForegroundColor Yellow
        Pause-Tick
        return
    }

    $maxDays = [Math]::Max(1, [int][Math]::Ceiling(($toUtc - $fromUtc).TotalDays))
    $to = $toUtc.ToString("yyyy-MM-ddTHH:mm:ss'Z'")
    Write-Host ("--to       {0}  (auto)" -f $to) -ForegroundColor Cyan
    Write-Host ("--max-days {0}  (auto from start date)" -f $maxDays) -ForegroundColor Cyan

    $symRaw = Read-Host "Symbols (blank = all, e.g. US30 or US30,GOLD)"
    $sym    = $(if ($null -eq $symRaw) { "" } else { $symRaw.Trim() })

    $args = @("-m", "tick_engine", "history-depth", "--to", $to, "--max-days", "$maxDays")
    if ($sym) { $args += @("--symbols") + ($sym -split "[,\s]+") }

    $jobLog = Join-Path $AppRoot "runtime\logs\manual.log"
    $cancelFile = Join-Path $AppRoot "runtime\run\cancel\manual-backfill.cancel"
    Remove-Item -LiteralPath $cancelFile -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path (Split-Path $jobLog -Parent) -Force | Out-Null
    New-Item -ItemType Directory -Path (Split-Path $cancelFile -Parent) -Force | Out-Null
    Write-Host ("Log        {0}" -f $jobLog) -ForegroundColor Cyan
    Write-Host ("Cancel     {0}" -f $cancelFile) -ForegroundColor Cyan
    Invoke-TickInline -PythonArgs $args -ExtraEnv @{
        TICK_ENGINE_JOB_LOG = $jobLog
        TICK_ENGINE_CANCEL_FILE = $cancelFile
    }
}

function Start-MaintenanceSweep {
    if (-not (Confirm-ManualReady -Title "Maintenance Sweep")) { return }

    $systemLog = Join-Path $AppRoot "runtime\logs\system.log"
    New-Item -ItemType Directory -Path (Split-Path $systemLog -Parent) -Force | Out-Null

    $steps = @(
        [pscustomobject]@{
            Name = "Spool Status"
            Args = @("-m", "tick_engine", "spool-status")
            Log = $systemLog
        },
        [pscustomobject]@{
            Name = "Spool Drain"
            Args = @("-m", "tick_engine", "spool-drain")
            Log = $systemLog
        },
        [pscustomobject]@{
            Name = "Repair Stale Runs"
            Args = @("-m", "tick_engine", "repair-stale-runs")
            Log = $systemLog
        },
        [pscustomobject]@{
            Name = "Build Activity Profile"
            Args = @("-m", "tick_engine", "build-activity-profile")
            Log = $systemLog
        }
    )

    Write-Host ""
    Write-Host "Maintenance sweep will run:" -ForegroundColor Cyan
    Write-Host "  1. Spool Status"
    Write-Host "  2. Spool Drain"
    Write-Host "  3. Repair Stale Runs"
    Write-Host "  4. Build Activity Profile"

    foreach ($step in $steps) {
        Write-Host ""
        Write-Host ("== {0} ==" -f $step.Name) -ForegroundColor Cyan
        $code = Invoke-TickInline -PythonArgs $step.Args -ExtraEnv @{ TICK_ENGINE_JOB_LOG = $step.Log } -NoPause
        if ($code -ne 0) {
            Write-Host ""
            Write-Host ("Maintenance sweep stopped at {0}; exit code {1}." -f $step.Name, $code) -ForegroundColor Red
            Pause-Tick
            return
        }
    }

    Write-Host ""
    Write-Host "Maintenance sweep completed." -ForegroundColor Green
    Pause-Tick
}

function Invoke-ResetTickData {
    if (-not (Confirm-ManualReady -Title "Reset Tick Data")) { return }

    $manualLog = Join-Path $AppRoot "runtime\logs\manual.log"
    New-Item -ItemType Directory -Path (Split-Path $manualLog -Parent) -Force | Out-Null

    Write-Host ""
    Write-Host "DANGER: this will delete all rows from the 11 tick tables." -ForegroundColor Red
    Write-Host "It also resets tick.IngestState and clears the SQLite spool buffer."
    Write-Host "It keeps SymbolMap, IngestRun audit history, config, and OAuth token cache."
    Write-Host ""
    Write-Host "Previewing current tick data first..." -ForegroundColor Cyan

    $previewCode = Invoke-TickInline `
        -PythonArgs @("-m", "tick_engine", "reset-tick-data", "--dry-run") `
        -ExtraEnv @{ TICK_ENGINE_JOB_LOG = $manualLog } `
        -NoPause
    if ($previewCode -ne 0) {
        Write-Host ""
        Write-Host ("Reset preview failed or was blocked; exit code {0}." -f $previewCode) -ForegroundColor Red
        Pause-Tick
        return
    }

    Write-Host ""
    Write-Host "To permanently delete tick rows, type RESET_TICK_DATA." -ForegroundColor Yellow
    $confirmRaw = Read-Host "Confirm"
    $confirm = $(if ($null -eq $confirmRaw) { "" } else { $confirmRaw.Trim() })
    if ($confirm -ne "RESET_TICK_DATA") {
        Write-Host "Cancelled." -ForegroundColor Yellow
        Pause-Tick
        return
    }

    $resetCode = Invoke-TickInline `
        -PythonArgs @("-m", "tick_engine", "reset-tick-data", "--confirm", "RESET_TICK_DATA") `
        -ExtraEnv @{ TICK_ENGINE_JOB_LOG = $manualLog } `
        -NoPause
    if ($resetCode -eq 0) {
        Write-Host "Tick data reset completed." -ForegroundColor Green
    } else {
        Write-Host ("Tick data reset failed; exit code {0}." -f $resetCode) -ForegroundColor Red
    }
    Pause-Tick
}

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

while ($true) {
    Write-Header
    Write-Menu
    $choiceRaw = Read-Host "Choose an option"
    $choice = $(if ($null -eq $choiceRaw) { "0" } else { $choiceRaw.Trim() })

    switch ($choice) {

        # ── Pre-check / Health ──────────────────────────────────────────────
        "1" {
            $systemLog = Join-Path $AppRoot "runtime\logs\system.log"
            Invoke-TickInline -PythonArgs @("-m", "tick_engine", "check") -ExtraEnv @{ TICK_ENGINE_JOB_LOG = $systemLog }
        }
        "2" {
            $systemLog = Join-Path $AppRoot "runtime\logs\system.log"
            Invoke-TickInline -PythonArgs @("-m", "tick_engine", "show-config") -ExtraEnv @{ TICK_ENGINE_JOB_LOG = $systemLog }
        }
        "3" {
            $authLog = Join-Path $AppRoot "runtime\logs\system.log"
            Invoke-TickInline -PythonArgs @("-m", "tick_engine", "token-status") -ExtraEnv @{ TICK_ENGINE_JOB_LOG = $authLog }
        }
        "4" { Start-TickWindow -Title "Tick Data Viewer" -PythonArgs @("-m", "tick_engine", "datacheck", "--open-browser") -ExtraEnv @{ TICK_ENGINE_DISABLE_FILE_LOG = "1" } }

        # ── cTrader OAuth ───────────────────────────────────────────────────
        "5" {
            $authLog = Join-Path $AppRoot "runtime\logs\system.log"
            Start-TickWindow -Title "OAuth Login" -PythonArgs @("-m", "tick_engine", "oauth-login") -ExtraEnv @{ TICK_ENGINE_JOB_LOG = $authLog }
        }
        "6" {
            $authLog = Join-Path $AppRoot "runtime\logs\system.log"
            Invoke-TickInline -PythonArgs @("-m", "tick_engine", "refresh-token", "--save") -ExtraEnv @{ TICK_ENGINE_JOB_LOG = $authLog }
        }
        "7" {
            $authLog = Join-Path $AppRoot "runtime\logs\system.log"
            $accountCode = Invoke-TickInline `
                -PythonArgs @("-m", "tick_engine", "account-list") `
                -ExtraEnv @{ TICK_ENGINE_JOB_LOG = $authLog } `
                -NoPause
            if ($accountCode -eq 0) {
                Write-Host ""
                Write-Host "Checking whether the selected account can authenticate for tick history..." -ForegroundColor Cyan
                Invoke-TickInline `
                    -PythonArgs @("-m", "tick_engine", "auth-check") `
                    -ExtraEnv @{ TICK_ENGINE_JOB_LOG = $authLog }
            } else {
                Pause-Tick
            }
        }
        "8" {
            $systemLog = Join-Path $AppRoot "runtime\logs\system.log"
            Invoke-TickInline -PythonArgs @("-m", "tick_engine", "symbol-sync") -ExtraEnv @{ TICK_ENGINE_JOB_LOG = $systemLog }
        }
        "9" {
            Write-Host ""
            Write-Host "WARNING: symbol-sync --apply will modify tick.SymbolMap in the database." -ForegroundColor Yellow
            $confirmRaw = Read-Host "Type APPLY to confirm"
            $confirm = $(if ($null -eq $confirmRaw) { "" } else { $confirmRaw.Trim() })
            if ($confirm -eq "APPLY") {
                $systemLog = Join-Path $AppRoot "runtime\logs\system.log"
                Invoke-TickInline -PythonArgs @("-m", "tick_engine", "symbol-sync", "--apply") -ExtraEnv @{ TICK_ENGINE_JOB_LOG = $systemLog }
            } else {
                Write-Host "Cancelled." -ForegroundColor Yellow
                Pause-Tick
            }
        }

        # ── Runtime ─────────────────────────────────────────────────────────
        "10" {
            $svc = Get-SupervisorStatus
            if ($svc.active) {
                Write-Host ""
                Write-Host ("Service already running (PID {0})." -f $svc.pid) -ForegroundColor Yellow
                Pause-Tick
            } else {
                $proc = Start-TickServiceBackground
                Write-Host ""
                Write-Host ("Service start requested in background (launcher PID {0})." -f $proc.Id) -ForegroundColor Green
                Write-Host "Use mode 1 for runtime status or mode 17 to tail operation.log."
                $deadline = (Get-Date).AddSeconds(12)
                do {
                    Start-Sleep -Seconds 1
                    $svc = Get-SupervisorStatus
                    if ($svc.active) {
                        Write-Host ("Backfill service is running (PID {0})." -f $svc.pid) -ForegroundColor Green
                        break
                    }
                } while ((Get-Date) -lt $deadline)
                if (-not $svc.active) {
                    Write-Host "Service did not publish a PID within 12 seconds. Check operation.log." -ForegroundColor Yellow
                }
                Pause-Tick
            }
        }
        "11" { Invoke-GracefulStop }

        # ── Manual Data Operations ───────────────────────────────────────────
        "12" { Start-BackfillFlow }
        "13" { Start-HistoryDepthFlow }
        "14" { Start-MaintenanceSweep }
        "15" { Invoke-ResetTickData }

        # ── Logs ─────────────────────────────────────────────────────────────
        "16" { Start-LogTailWindow "runtime\logs\system.log"                      "System Log" }
        "17" { Start-LogTailWindow "runtime\logs\operation.log"                   "Operation Log" }
        "18" { Start-LogTailWindow "runtime\logs\manual.log"                      "Manual Log" }
        "19" {
            Start-LogTailWindow "runtime\logs\system.log"                         "System Log"
            Start-LogTailWindow "runtime\logs\operation.log"                      "Operation Log"
            Start-LogTailWindow "runtime\logs\manual.log"                         "Manual Log"
        }
        "20" { Clear-TickLogs }

        "0"  { exit 0 }
        default { Write-Host "Unknown option."; Start-Sleep -Seconds 1 }
    }
}
