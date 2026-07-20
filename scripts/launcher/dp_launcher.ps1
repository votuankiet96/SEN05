param(
    [switch]$List
)

$ErrorActionPreference = "Stop"

$AppRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$PythonExe = (Get-Command python -ErrorAction Stop).Source
function Write-Header {
    Clear-Host
    Write-Host "============================================================"
    Write-Host " SEN05 DP Program"
    Write-Host "============================================================"
    Write-Host ("Root   : {0}" -f $AppRoot)
    Write-Host ("Python : {0}" -f $PythonExe)
    Write-Host ""
}

function Write-Menu {
    Write-Host "[Pre-check / Health]"
    Write-Host "  1. System Check"
    Write-Host "  2. Chart & Data Health"
    Write-Host "  3. View Core Settings"
    Write-Host "  4. TradingView Auth Check"
    Write-Host "  5. TradingView Login"
    Write-Host ""
    Write-Host "[DP Program Runtime]"
    Write-Host "  6. Start DP Program 24/7"
    Write-Host "  7. Graceful Stop"
    Write-Host ""
    Write-Host "[Manual Data Operations]"
    Write-Host "  Note: normal production should run with mode 6."
    Write-Host "        Before manual jobs, use mode 7 and confirm mode 1 shows no active runtime process."
    Write-Host "        The launcher will block manual jobs if DP Program/live/historical is already active."
    Write-Host "  8. Backfill Missing / Gap Repair"
    Write-Host "  9. Initial Full Pull"
    Write-Host " 10. Replay-assisted Pull"
    Write-Host " 11. Reset OHLCV Data"
    Write-Host " 12. Live Direct Run"
    Write-Host ""
    Write-Host "[Logs / Runtime]"
    Write-Host " 13. Tail System Log"
    Write-Host " 14. Tail Activity Log"
    Write-Host " 15. Tail Auth Log"
    Write-Host " 16. Tail Discord Log"
    Write-Host " 17. Tail Subprocess Debug Log"
    Write-Host " 18. Tail Live Fetching Log"
    Write-Host " 19. Tail Historical Pulling Log"
    Write-Host " 20. Tail Data Warehouse Log"
    Write-Host " 21. Clean Runtime Logs by Retention"
    Write-Host ""
    Write-Host "  0. Exit"
    Write-Host ""
}

function Pause-Dp {
    Write-Host ""
    Read-Host "Press Enter to continue" | Out-Null
}

function Quote-PSString {
    param([AllowEmptyString()][string]$Value)
    return "'" + ($Value -replace "'", "''") + "'"
}

function New-PythonCommand {
    param([string[]]$PythonArgs)
    $parts = @((Quote-PSString $PythonExe)) + ($PythonArgs | ForEach-Object { Quote-PSString $_ })
    return "& " + ($parts -join " ")
}

function Assert-PythonArgs {
    param(
        [string]$Title,
        [string[]]$PythonArgs
    )
    $validArgs = @($PythonArgs | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($validArgs.Count -eq 0) {
        throw ("Launcher command for '{0}' has no Python module arguments. Refusing to open a blank Python shell." -f $Title)
    }
}

function Start-DpWindow {
    param(
        [string]$Title,
        [string[]]$PythonArgs
    )
    $safeTitle = $Title -replace "'", "''"
    $safeRoot = $AppRoot -replace "'", "''"
    Assert-PythonArgs -Title $Title -PythonArgs $PythonArgs
    $cmdLine = New-PythonCommand -PythonArgs $PythonArgs
    $command = @"
`$Host.UI.RawUI.WindowTitle = '$safeTitle'
Set-Location -LiteralPath '$safeRoot'
Write-Host ''
Write-Host 'SEN05 DP Program - $safeTitle'
Write-Host ''
Write-Host 'Command:'
Write-Host '$cmdLine'
Write-Host ''
$cmdLine
`$exitCode = if (`$null -ne `$LASTEXITCODE) { `$LASTEXITCODE } else { 0 }
Write-Host ''
Write-Host ("Process exited with code {0}." -f `$exitCode)
Read-Host 'Press Enter to close this window' | Out-Null
exit `$exitCode
"@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    Start-Process powershell.exe -ArgumentList @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-EncodedCommand",
        $encoded
    )
}

function Invoke-DpInline {
    param([string[]]$PythonArgs)
    Push-Location $AppRoot
    try {
        & $PythonExe @PythonArgs
        $code = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 }
        Write-Host ""
        Write-Host ("Exit code: {0}" -f $code)
    }
    finally {
        Pop-Location
    }
    Pause-Dp
}

function Invoke-DpNoPause {
    param([string[]]$PythonArgs)
    Push-Location $AppRoot
    try {
        & $PythonExe @PythonArgs 2>&1 | ForEach-Object {
            Write-Host $_
        }
        $code = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
        return $code
    }
    finally {
        Pop-Location
    }
}

function Invoke-DpQuiet {
    param([string[]]$PythonArgs)
    Push-Location $AppRoot
    try {
        & $PythonExe @PythonArgs
        return $(if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 })
    }
    finally {
        Pop-Location
    }
}

function Get-ConflictStatus {
    param([ValidateSet("supervisor", "live", "historical")][string]$Kind)
    Push-Location $AppRoot
    try {
        $raw = & $PythonExe -m core_engine conflict-status --kind $Kind --json
        $json = ($raw -join [Environment]::NewLine)
        if (-not $json.Trim()) {
            return [pscustomobject]@{ active = $false; kind = $Kind }
        }
        return $json | ConvertFrom-Json
    }
    catch {
        Write-Host ("Could not inspect {0} status: {1}" -f $Kind, $_.Exception.Message) -ForegroundColor Yellow
        return [pscustomobject]@{ active = $false; kind = $Kind }
    }
    finally {
        Pop-Location
    }
}

function Record-OperatorDecision {
    param(
        [ValidateSet("supervisor", "live", "historical")][string]$Kind,
        [ValidateSet("skip", "replace", "queue")][string]$Decision,
        [string]$Title
    )
    Invoke-DpQuiet @("-m", "core_engine", "operator-decision", "--kind", $Kind, "--decision", $Decision, "--requested-title", $Title) | Out-Null
}

function Resolve-Conflict {
    param(
        [ValidateSet("supervisor", "live", "historical")][string]$Kind,
        [string]$Title,
        [bool]$AllowQueue = $false,
        [bool]$AllowReplace = $true,
        [string]$Note = ""
    )
    $status = Get-ConflictStatus -Kind $Kind
    if (-not $status.active) {
        return "start"
    }

    Write-Host ""
    Write-Host ("A related {0} process is already running." -f $Kind) -ForegroundColor Yellow
    Write-Host ("Requested : {0}" -f $Title)
    Write-Host ("PID       : {0}" -f ($(if ($status.pid) { $status.pid } else { "-" })))
    Write-Host ("Owner     : {0}" -f ($(if ($status.owner) { $status.owner } else { "-" })))
    Write-Host ("Started   : {0}" -f ($(if ($status.started) { $status.started } else { "-" })))
    if ($Note) {
        Write-Host $Note -ForegroundColor Yellow
    }
    Write-Host ""
    Write-Host "Choose what to do:"
    Write-Host "  S. Keep the running process and skip this request"
    if ($AllowReplace) {
        Write-Host "  R. Gracefully stop the running process, then start this request"
    }
    if ($AllowQueue) {
        Write-Host "  Q. Queue this historical request to run after the current one"
    }
    $answerRaw = Read-Host "Choice"
    $answer = $(if ($null -eq $answerRaw) { "" } else { $answerRaw.Trim().ToUpperInvariant() })

    if ($AllowQueue -and $answer -eq "Q") {
        Record-OperatorDecision -Kind $Kind -Decision "queue" -Title $Title
        return "queue"
    }
    if ($AllowReplace -and $answer -eq "R") {
        Record-OperatorDecision -Kind $Kind -Decision "replace" -Title $Title
        Write-Host ""
        if ($Kind -eq "historical") {
            Write-Host "Replacement requested. The running historical job will stop at the next safe checkpoint before this request starts." -ForegroundColor Yellow
            Write-Host "Note: it may finish the current symbol/timeframe and write those rows first; this protects database consistency." -ForegroundColor Yellow
        }
        else {
            Write-Host "Replacement requested. The running process will be stopped gracefully before this request starts." -ForegroundColor Yellow
        }
        return "replace"
    }

    Record-OperatorDecision -Kind $Kind -Decision "skip" -Title $Title
    return "skip"
}

function Queue-HistoricalCommand {
    param(
        [string]$Title,
        [string[]]$PythonArgs
    )
    $queueArgs = @("-m", "core_engine", "queue-historical", "--title", $Title, "--requested-by", "launcher", "--")
    $queueArgs += @($PythonArgs)
    Invoke-DpQuiet $queueArgs | Out-Null
    Write-Host ""
    Write-Host ("Queued: {0}" -f $Title) -ForegroundColor Yellow
}

function Add-ReplaceArg {
    param(
        [ValidateSet("supervisor", "live", "historical")][string]$Kind,
        [string[]]$PythonArgs
    )
    if ($Kind -in @("supervisor", "live")) {
        if ($PythonArgs -notcontains "--replace") {
            return @($PythonArgs + "--replace")
        }
        return $PythonArgs
    }
    if ($Kind -eq "historical") {
        if ($PythonArgs -notcontains "--on-conflict") {
            return @($PythonArgs + @("--on-conflict", "replace"))
        }
    }
    return $PythonArgs
}

function Start-GuardedWindow {
    param(
        [ValidateSet("supervisor", "live", "historical")][string]$Kind,
        [string]$Title,
        [string[]]$PythonArgs,
        [bool]$AllowQueue = $false,
        [bool]$AllowReplace = $true,
        [string]$Note = ""
    )
    $decision = Resolve-Conflict -Kind $Kind -Title $Title -AllowQueue:$AllowQueue -AllowReplace:$AllowReplace -Note $Note
    if ($decision -eq "start") {
        Start-DpWindow -Title $Title -PythonArgs $PythonArgs
        return
    }
    if ($decision -eq "queue") {
        Queue-HistoricalCommand -Title $Title -PythonArgs $PythonArgs
        Pause-Dp
        return
    }
    if ($decision -eq "replace") {
        $args = Add-ReplaceArg -Kind $Kind -PythonArgs $PythonArgs
        Start-DpWindow -Title $Title -PythonArgs $args
        return
    }
    Write-Host ""
    Write-Host "Skipped. Existing process was left unchanged." -ForegroundColor Yellow
    Pause-Dp
}

function Start-SupervisorGuardedWindow {
    param(
        [string]$Title,
        [string[]]$PythonArgs
    )

    $supervisor = Get-ConflictStatus -Kind "supervisor"
    if ($supervisor.active) {
        Start-GuardedWindow -Kind "supervisor" -Title $Title -PythonArgs $PythonArgs -AllowQueue:$false -AllowReplace:$true
        return
    }

    $live = Get-ConflictStatus -Kind "live"
    $historical = Get-ConflictStatus -Kind "historical"
    $active = @()
    if ($live.active) { $active += [pscustomobject]@{ kind = "live"; status = $live } }
    if ($historical.active) { $active += [pscustomobject]@{ kind = "historical"; status = $historical } }

    if ($active.Count -eq 0) {
        Start-DpWindow -Title $Title -PythonArgs $PythonArgs
        return
    }

    Write-Host ""
    Write-Host "DP Program 24/7 needs to own live/historical runtime cleanly." -ForegroundColor Yellow
    Write-Host "The following related process group(s) are already active:"
    foreach ($item in $active) {
        $status = $item.status
        Write-Host ("- {0}: PID {1}, owner {2}, started {3}" -f $item.kind, $(if ($status.pid) { $status.pid } else { "-" }), $(if ($status.owner) { $status.owner } else { "-" }), $(if ($status.started) { $status.started } else { "-" }))
    }
    Write-Host ""
    Write-Host "Choose what to do:"
    Write-Host "  S. Keep the current process(es) and skip Start DP Program"
    Write-Host "  R. Gracefully stop all DP Program processes, then start DP Program 24/7"
    $answerRaw = Read-Host "Choice"
    $answer = $(if ($null -eq $answerRaw) { "" } else { $answerRaw.Trim().ToUpperInvariant() })

    if ($answer -ne "R") {
        foreach ($item in $active) {
            Record-OperatorDecision -Kind $item.kind -Decision "skip" -Title $Title
        }
        Write-Host ""
        Write-Host "Skipped. Existing process(es) were left unchanged." -ForegroundColor Yellow
        Pause-Dp
        return
    }

    foreach ($item in $active) {
        Record-OperatorDecision -Kind $item.kind -Decision "replace" -Title $Title
    }
    Write-Host ""
    Write-Host "Stopping existing DP Program process groups before starting the 24/7 supervisor..." -ForegroundColor Yellow
    $stopCode = Invoke-DpNoPause @("-m", "core_engine", "stop", "--reason", "launcher_start_dp_program", "--wait-sec", "60", "--force-after-grace")
    if ($stopCode -ne 0) {
        Write-Host ""
        Write-Host "Start was blocked because Graceful Stop did not finish cleanly." -ForegroundColor Red
        Pause-Dp
        return
    }

    $stillActive = @()
    foreach ($kind in @("supervisor", "live", "historical")) {
        $status = Get-ConflictStatus -Kind $kind
        if ($status.active) { $stillActive += $kind }
    }
    if ($stillActive.Count -gt 0) {
        Write-Host ""
        Write-Host ("Start was blocked because these group(s) are still active: {0}" -f ($stillActive -join ", ")) -ForegroundColor Red
        Pause-Dp
        return
    }

    Start-DpWindow -Title $Title -PythonArgs $PythonArgs
}

function Get-ActiveRuntimeGroups {
    $active = @()
    foreach ($kind in @("supervisor", "live", "historical")) {
        $status = Get-ConflictStatus -Kind $kind
        if ($status.active) {
            $active += [pscustomobject]@{ kind = $kind; status = $status }
        }
    }
    return $active
}

function Confirm-ManualRuntimeReady {
    param([string]$Title)

    $active = @(Get-ActiveRuntimeGroups)
    if ($active.Count -eq 0) {
        return $true
    }

    Write-Host ""
    Write-Host "Manual data operations require a clean runtime." -ForegroundColor Yellow
    Write-Host ("Requested: {0}" -f $Title)
    Write-Host "The following DP Program process group(s) are active:"
    foreach ($item in $active) {
        $status = $item.status
        Write-Host ("- {0}: PID {1}, owner {2}, started {3}" -f $item.kind, $(if ($status.pid) { $status.pid } else { "-" }), $(if ($status.owner) { $status.owner } else { "-" }), $(if ($status.started) { $status.started } else { "-" }))
    }
    Write-Host ""
    Write-Host "Choose what to do:"
    Write-Host "  S. Keep the running process(es) and skip this manual request"
    Write-Host "  G. Graceful Stop all DP Program runtime processes, then run this manual request"
    $answerRaw = Read-Host "Choice"
    $answer = $(if ($null -eq $answerRaw) { "" } else { $answerRaw.Trim().ToUpperInvariant() })

    if ($answer -ne "G") {
        foreach ($item in $active) {
            Record-OperatorDecision -Kind $item.kind -Decision "skip" -Title $Title
        }
        Write-Host ""
        Write-Host "Skipped. Existing process(es) were left unchanged." -ForegroundColor Yellow
        Pause-Dp
        return $false
    }

    foreach ($item in $active) {
        Record-OperatorDecision -Kind $item.kind -Decision "replace" -Title $Title
    }
    Write-Host ""
    Write-Host "Graceful Stop requested before manual operation..." -ForegroundColor Yellow
    $stopCode = Invoke-DpNoPause @("-m", "core_engine", "stop", "--reason", "launcher_manual_operation", "--wait-sec", "60", "--force-after-grace")
    if ($stopCode -ne 0) {
        Write-Host ""
        Write-Host "Manual operation was blocked because Graceful Stop did not finish cleanly." -ForegroundColor Red
        Pause-Dp
        return $false
    }

    $stillActive = @(Get-ActiveRuntimeGroups)
    if ($stillActive.Count -gt 0) {
        Write-Host ""
        Write-Host "Manual operation was blocked because these group(s) are still active:" -ForegroundColor Red
        foreach ($item in $stillActive) {
            Write-Host ("- {0}" -f $item.kind) -ForegroundColor Red
        }
        Pause-Dp
        return $false
    }

    return $true
}

function Start-ManualDataWindow {
    param(
        [string]$Title,
        [string[]]$PythonArgs
    )
    if (Confirm-ManualRuntimeReady -Title $Title) {
        Start-DpWindow -Title $Title -PythonArgs $PythonArgs
    }
}

function Read-Optional {
    param(
        [string]$Prompt,
        [string]$Example
    )
    $value = Read-Host ("{0} (blank = all, e.g. {1})" -f $Prompt, $Example)
    if ($null -eq $value) {
        return ""
    }
    return $value.Trim()
}

function Add-HistoricalScope {
    param(
        [string[]]$BaseArgs,
        [switch]$AskLookback
    )
    $result = [System.Collections.Generic.List[string]]::new()
    foreach ($item in @($BaseArgs)) {
        if (-not [string]::IsNullOrWhiteSpace($item)) {
            [void]$result.Add([string]$item)
        }
    }
    $symbols = Read-Optional -Prompt "Symbols" -Example "BTCUSD or EURUSD,GOLD"
    if ($symbols) {
        [void]$result.Add("--symbols")
        [void]$result.Add($symbols)
    }

    $timeframes = Read-Optional -Prompt "Timeframes" -Example "M5 or M5,H1"
    if ($timeframes) {
        [void]$result.Add("--timeframes")
        [void]$result.Add($timeframes)
    }

    $assetType = Read-Optional -Prompt "Asset type" -Example "Indice,Metal,Crypto,FOREX"
    if ($assetType) {
        [void]$result.Add("--asset-type")
        [void]$result.Add($assetType)
    }

    if ($AskLookback) {
        $daysRaw = Read-Host "Hole lookback days (blank = config default)"
        $days = $(if ($null -eq $daysRaw) { "" } else { $daysRaw.Trim() })
        if ($days) {
            [void]$result.Add("--hole-lookback-days")
            [void]$result.Add($days)
        }
    }
    return [string[]]$result.ToArray()
}

function Confirm-AllScopeRisk {
    param([string]$Action)
    Write-Host ""
    Write-Host ("WARNING: {0} can be very heavy if you leave scope blank." -f $Action) -ForegroundColor Yellow
    $confirmRaw = Read-Host "Type RUN to continue"
    $confirm = $(if ($null -eq $confirmRaw) { "" } else { $confirmRaw.Trim() })
    return ($confirm -eq "RUN")
}

function Start-LogTailWindow {
    param(
        [string]$RelativePath,
        [string]$Label
    )
    $path = Join-Path $AppRoot $RelativePath
    $safeTitle = $Label -replace "'", "''"
    $safePath = $path -replace "'", "''"
    $command = @"
`$Host.UI.RawUI.WindowTitle = '$safeTitle'
Write-Host ''
Write-Host '$Label'
Write-Host 'Path: $safePath'
Write-Host ''
if (-not (Test-Path -LiteralPath '$safePath')) {
    Write-Host 'Log file does not exist yet. Waiting for it to be created...'
    while (-not (Test-Path -LiteralPath '$safePath')) { Start-Sleep -Seconds 2 }
}
Get-Content -LiteralPath '$safePath' -Tail 200 -Wait
"@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    Start-Process powershell.exe -ArgumentList @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-EncodedCommand",
        $encoded
    )
}

function Start-LiveLogTableWindow {
    param(
        [string]$RelativePath,
        [string]$Label
    )
    $path = Join-Path $AppRoot $RelativePath
    $safeTitle = $Label -replace "'", "''"
    $safePath = $path -replace "'", "''"
    $command = @"
`$Host.UI.RawUI.WindowTitle = '$safeTitle'
`$path = '$safePath'
`$label = '$Label'
function Test-LiveLogLine {
    param([AllowEmptyString()][string]`$Line)
    if (`$null -eq `$Line) { return `$false }
    return (
        `$Line -match '\|\s+CANDLE\s+\|' -or
        `$Line -match '^LOG UTC\s+\|' -or
        `$Line -match '\[OPERATION\]' -or
        `$Line -match '\[CANDLE FLOW' -or
        `$Line -match '\[CANDLES' -or
        `$Line -match '\[BATCH SUMMARY' -or
        `$Line -match '\|\s*$' -or
        `$Line -match '\|\s*-{20,}\s*$' -or
        `$Line -match '\|\s*={20,}\s*$' -or
        `$Line -match '^-{20,}\s*$' -or
        `$Line -match '^={20,}\s*$' -or
        `$Line -match '\|\s+(WARNING|ERROR)\s+\|' -or
        `$Line -match '^\s+(Status|Sessions|Accepted|Missing|Backlog|Runtime|Database|Queue|Retry|By symbol|By TF|Top pairs|Details|Result|Issues)\s+:'
    )
}

function Write-LiveLogLine {
    param([AllowEmptyString()][string]`$Line)
    Write-Host `$Line
}

Write-Host ''
Write-Host `$label
Write-Host "Path: `$path"
Write-Host 'Mode: streaming tail. This window no longer refreshes the whole screen.'
Write-Host 'Close this window when you no longer need to watch the log.'
Write-Host ''
while (-not (Test-Path -LiteralPath `$path)) {
    Write-Host 'Log file does not exist yet. Waiting for it to be created...'
    Start-Sleep -Seconds 2
}
try {
    Get-Content -LiteralPath `$path -Tail 250 -Wait -ErrorAction Stop | ForEach-Object {
        if (Test-LiveLogLine `$_) {
            Write-LiveLogLine `$_
        }
    }
} catch {
    Write-Host "Could not read log: `$(`$_.Exception.Message)" -ForegroundColor Yellow
}
"@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    Start-Process powershell.exe -ArgumentList @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-EncodedCommand",
        $encoded
    )
}

function Invoke-AuthCheck {
    Write-Host ""
    Write-Host "[TradingView Auth Check]"
    Write-Host ""
    Write-Host "Step 1/2: cached credential status"
    Write-Host ""
    $statusCode = Invoke-DpNoPause @("-m", "core_engine", "auth", "status")
    Write-Host ""
    Write-Host ("Auth status exit code: {0}" -f $statusCode)
    Write-Host ""
    Write-Host "Step 2/2: connectivity diagnose"
    Write-Host ""
    $diagnoseCode = Invoke-DpNoPause @("-m", "core_engine", "auth", "diagnose")
    Write-Host ""
    Write-Host ("Auth diagnose exit code: {0}" -f $diagnoseCode)
    Pause-Dp
}

function Invoke-CleanRuntimeLogs {
    Write-Host ""
    Write-Host "[Clean Runtime Logs by Retention]"
    Write-Host ""
    Write-Host "This removes runtime log/state/temp files older than the selected retention window."
    Write-Host "Blank = use config default. Enter 0 only when you intentionally want to remove all eligible runtime log/state/temp files."
    Write-Host "Credentials, config files, and TradingView browser cache are not deleted by this command."
    Write-Host ""
    $daysRaw = Read-Host "Retention days"
    $days = $(if ($null -eq $daysRaw) { "" } else { $daysRaw.Trim() })
    $cleanArgs = @("-m", "core_engine", "clean-runtime")
    if ($days -ne "") {
        $cleanArgs += @("--days", $days)
    }
    Invoke-DpInline $cleanArgs
}

function Test-ResetRuntimeBlocker {
    $blockers = @()
    $supervisor = Get-ConflictStatus -Kind "supervisor"
    if ($supervisor.active) {
        $blockers += ("DP Program supervisor PID {0}" -f ($(if ($supervisor.pid) { $supervisor.pid } else { "-" })))
    }
    $live = Get-ConflictStatus -Kind "live"
    if ($live.active) {
        $blockers += ("Live fetching PID {0}" -f ($(if ($live.pid) { $live.pid } else { "-" })))
    }
    return $blockers
}

function Start-ResetFlow {
    if (-not (Confirm-ManualRuntimeReady -Title "Historical Reset OHLCV Data")) {
        return
    }

    $previewCmd = Add-HistoricalScope -BaseArgs @("-m", "core_engine", "historical", "--mode", "reset", "--dry-run")

    Write-Host ""
    Write-Host "Step 1/2: reset preview. No data will be deleted in this step." -ForegroundColor Yellow
    Write-Host ""
    $code = Invoke-DpNoPause $previewCmd
    Write-Host ""
    Write-Host ("Preview exit code: {0}" -f $code)
    if ($code -ne 0) {
        Write-Host "Reset stopped because preview did not finish cleanly." -ForegroundColor Red
        Pause-Dp
        return
    }

    $resetBlockers = Test-ResetRuntimeBlocker
    if ($resetBlockers.Count -gt 0) {
        Write-Host ""
        Write-Host "Reset preview completed, but delete is blocked because runtime processes are still active:" -ForegroundColor Red
        foreach ($blocker in $resetBlockers) {
            Write-Host ("- {0}" -f $blocker) -ForegroundColor Red
        }
        Write-Host ""
        Write-Host "Why: reset deletes OHLCV rows, so live fetching and the 24/7 supervisor must be stopped first." -ForegroundColor Yellow
        Write-Host "Next: choose 'Graceful Stop', confirm System Check shows no runtime process, then run Reset again." -ForegroundColor Yellow
        Pause-Dp
        return
    }

    Write-Host ""
    Write-Host "Step 2/2: confirm delete." -ForegroundColor Red
    Write-Host "This will delete the selected OHLCV rows from staging/fact tables." -ForegroundColor Red
    $confirmRaw = Read-Host "Type RESET to delete exactly the previewed scope"
    $confirm = $(if ($null -eq $confirmRaw) { "" } else { $confirmRaw.Trim() })
    if ($confirm -ne "RESET") {
        Write-Host "Reset cancelled. No rows were deleted." -ForegroundColor Yellow
        Pause-Dp
        return
    }

    $deleteCmd = @()
    foreach ($item in $previewCmd) {
        if ($item -ne "--dry-run") {
            $deleteCmd += $item
        }
    }
    $deleteCmd += @("--reset", "--yes")
    Start-ManualDataWindow -Title "Historical Reset OHLCV Data" -PythonArgs $deleteCmd
}

if ($List) {
    Write-Header
    Write-Menu
    exit 0
}

while ($true) {
    Write-Header
    Write-Menu
    $choiceRaw = Read-Host "Choose an option"
    $choice = $(if ($null -eq $choiceRaw) { "0" } else { $choiceRaw.Trim() })
    switch ($choice) {
        "1" { Invoke-DpInline @("-m", "core_engine", "doctor", "--deep-auth") }
        "2" { Start-DpWindow -Title "Chart & Data Health" -PythonArgs @("-m", "core_engine", "chart-datacheck", "--open-browser") }
        "3" { Invoke-DpInline @("-m", "core_engine", "settings") }
        "4" { Invoke-AuthCheck }
        "5" { Start-DpWindow -Title "TradingView Login" -PythonArgs @("-m", "core_engine", "auth", "login", "--timeout-sec", "900") }

        "6" { Start-SupervisorGuardedWindow -Title "DP Program 24/7" -PythonArgs @("-m", "core_engine", "run", "--live") }
        "7" { Invoke-DpInline @("-m", "core_engine", "stop", "--reason", "launcher", "--wait-sec", "60", "--force-after-grace") }

        "8" {
            $title = "Historical Backfill Missing"
            if (-not (Confirm-ManualRuntimeReady -Title $title)) { break }
            $cmd = Add-HistoricalScope -BaseArgs @("-m", "core_engine", "historical", "--mode", "gap", "--replay", "off") -AskLookback
            Start-DpWindow -Title $title -PythonArgs $cmd
        }
        "9" {
            $title = "Historical Initial Full Pull"
            if (-not (Confirm-ManualRuntimeReady -Title $title)) { break }
            if (Confirm-AllScopeRisk "Initial Full Pull") {
                $cmd = Add-HistoricalScope -BaseArgs @("-m", "core_engine", "historical", "--mode", "full", "--replay", "config")
                Start-DpWindow -Title $title -PythonArgs $cmd
            }
        }
        "10" {
            $title = "Historical Replay-assisted Pull"
            if (-not (Confirm-ManualRuntimeReady -Title $title)) { break }
            if (Confirm-AllScopeRisk "Replay-assisted Pull") {
                $cmd = Add-HistoricalScope -BaseArgs @("-m", "core_engine", "historical", "--mode", "full", "--replay", "on")
                Start-DpWindow -Title $title -PythonArgs $cmd
            }
        }
        "11" { Start-ResetFlow }

        "12" {
            $title = "Live Direct Run"
            if (Confirm-ManualRuntimeReady -Title $title) {
                Start-DpWindow -Title $title -PythonArgs @("-m", "core_engine", "live")
            }
        }

        "13" { Start-LogTailWindow "runtime\logs\system\system.log" "DP Program system log" }
        "14" { Start-LogTailWindow "runtime\logs\system\activity.log" "Activity timeline log" }
        "15" { Start-LogTailWindow "runtime\logs\system\auth.log" "TradingView auth log" }
        "16" { Start-LogTailWindow "runtime\logs\system\discord.log" "Discord notification log" }
        "17" { Start-LogTailWindow "runtime\logs\system\subprocess_debug.log" "Subprocess debug log" }
        "18" { Start-LogTailWindow "runtime\logs\operation\live_fetching.log" "Live fetching log" }
        "19" { Start-LogTailWindow "runtime\logs\operation\historical_pulling.log" "Historical pulling log" }
        "20" { Start-LogTailWindow "runtime\logs\operation\data_warehouse.log" "Data warehouse log" }
        "21" { Invoke-CleanRuntimeLogs }
        "0" { exit 0 }
        default { Write-Host "Unknown option."; Start-Sleep -Seconds 1 }
    }
}
