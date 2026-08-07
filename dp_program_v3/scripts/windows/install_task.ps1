[CmdletBinding()]
param(
    [string]$PythonPath = (Get-Command python -ErrorAction Stop).Source,
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [string]$TaskFolder = "\SEN05\",
    [string]$TaskUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name,
    [int]$WatchdogIntervalMinutes = 5,
    [switch]$StartServices,
    [switch]$StartWatchdog
)

# Registers 3 Scheduled Tasks: run-live and run-backfill (AtStartup,
# restart-on-failure — Task Scheduler restarts the action only when it
# exits with a non-zero code; a graceful `dp_program stop` exits 0, so it
# is never fought), and a short-lived watchdog that runs every
# $WatchdogIntervalMinutes and posts a Discord alert if either service is
# unhealthy (see scripts/windows/watchdog.py). Safe to re-run: each task
# definition is fully replaced (-Force), not appended to.
#
# By design this script does NOT start the live/backfill tasks unless
# -StartServices is passed — if those services are already running under
# a different process (e.g. started manually), starting the task too
# would just hit the engine's own single-instance lock and fail cleanly,
# but there is no reason to do that. Pass -StartServices only for a fresh
# machine where nothing is running yet.

$ErrorActionPreference = "Stop"

function Test-Environment {
    $configPath = Join-Path $RepoRoot "Config.yaml"
    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        throw "Python executable not found: $PythonPath"
    }
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
        throw "Config.yaml not found under repository root: $RepoRoot"
    }
    Push-Location $RepoRoot
    try {
        & $PythonPath -m dp_program doctor
        if ($LASTEXITCODE -ne 0) {
            throw "V3 doctor failed; Scheduled Tasks were not changed."
        }
    }
    finally {
        Pop-Location
    }
}

function Register-ServiceTask {
    param([string]$Name, [string]$BatchFile)
    if (-not (Test-Path -LiteralPath $BatchFile -PathType Leaf)) {
        throw "Batch file not found: $BatchFile"
    }
    $existing = Get-ScheduledTask -TaskPath $TaskFolder -TaskName $Name -ErrorAction SilentlyContinue
    if ($existing -and $existing.State -eq "Running") {
        throw "$Name is already running under Task Scheduler. Stop it gracefully first (dp_program stop) before updating its definition."
    }
    $action = New-ScheduledTaskAction -Execute $BatchFile -Argument "start" -WorkingDirectory $RepoRoot
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -RestartCount 999 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -MultipleInstances IgnoreNew `
        -Priority 5
    # [TimeSpan]::Zero passed through -ExecutionTimeLimit fails XML validation
    # on this Task Scheduler build ("value out of range"); setting the ISO8601
    # zero-duration string directly on the settings object is the documented
    # workaround and means "no time limit" (required: run-live/run-backfill
    # run forever until stopped).
    $settings.ExecutionTimeLimit = "PT0S"
    $principal = New-ScheduledTaskPrincipal -UserId $TaskUser -LogonType S4U -RunLevel Highest
    $definition = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal
    Register-ScheduledTask -TaskPath $TaskFolder -TaskName $Name -InputObject $definition -Force -ErrorAction Stop | Out-Null
}

function Register-WatchdogTask {
    param([string]$Name)
    $scriptPath = Join-Path $RepoRoot "scripts\windows\watchdog.py"
    if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
        throw "Watchdog script not found: $scriptPath"
    }
    $action = New-ScheduledTaskAction -Execute $PythonPath -Argument "`"$scriptPath`"" -WorkingDirectory $RepoRoot
    # [TimeSpan]::MaxValue fails the same XML "value out of range" validation
    # as [TimeSpan]::Zero above; Task Scheduler has no true "forever"
    # repetition duration, so 10 years is the conventional stand-in — the
    # task will be redefined long before that ever matters.
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes $WatchdogIntervalMinutes) `
        -RepetitionDuration (New-TimeSpan -Days 3650)
    $settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
        -MultipleInstances IgnoreNew `
        -Priority 7
    $principal = New-ScheduledTaskPrincipal -UserId $TaskUser -LogonType S4U -RunLevel Limited
    $definition = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal
    Register-ScheduledTask -TaskPath $TaskFolder -TaskName $Name -InputObject $definition -Force -ErrorAction Stop | Out-Null
}

function Confirm-ServiceTask {
    param([string]$Name, [string]$BatchFile)
    $task = Get-ScheduledTask -TaskPath $TaskFolder -TaskName $Name
    if (
        $task.Actions.Execute -ne $BatchFile `
        -or $task.Actions.Arguments -ne "start" `
        -or [string]$task.Principal.LogonType -ne "S4U" `
        -or [int]$task.Settings.RestartCount -ne 999 `
        -or [string]$task.Settings.RestartInterval -ne "PT1M" `
        -or -not [bool]$task.Settings.Enabled `
        -or $task.Triggers[0].CimClass.CimClassName -ne "MSFT_TaskBootTrigger"
    ) {
        throw "$Name failed verification after registration."
    }
    $task
}

function Confirm-WatchdogTask {
    param([string]$Name)
    $task = Get-ScheduledTask -TaskPath $TaskFolder -TaskName $Name
    if (
        [string]$task.Principal.LogonType -ne "S4U" `
        -or -not [bool]$task.Settings.Enabled `
        -or $task.Triggers[0].CimClass.CimClassName -ne "MSFT_TaskTimeTrigger" `
        -or [string]$task.Triggers[0].Repetition.Interval -ne "PT${WatchdogIntervalMinutes}M"
    ) {
        throw "$Name failed verification after registration."
    }
    $task
}

Test-Environment

Register-ServiceTask -Name "SEN05 DP Program Live" -BatchFile (Join-Path $RepoRoot "run_live.bat")
Register-ServiceTask -Name "SEN05 DP Program Backfill" -BatchFile (Join-Path $RepoRoot "run_backfill.bat")
Register-WatchdogTask -Name "SEN05 DP Program Watchdog"

$liveTask = Confirm-ServiceTask -Name "SEN05 DP Program Live" -BatchFile (Join-Path $RepoRoot "run_live.bat")
$backfillTask = Confirm-ServiceTask -Name "SEN05 DP Program Backfill" -BatchFile (Join-Path $RepoRoot "run_backfill.bat")
$watchdogTask = Confirm-WatchdogTask -Name "SEN05 DP Program Watchdog"

if ($StartServices) {
    Start-ScheduledTask -TaskPath $TaskFolder -TaskName "SEN05 DP Program Live"
    Start-ScheduledTask -TaskPath $TaskFolder -TaskName "SEN05 DP Program Backfill"
}
if ($StartWatchdog) {
    Start-ScheduledTask -TaskPath $TaskFolder -TaskName "SEN05 DP Program Watchdog"
}

@($liveTask, $backfillTask, $watchdogTask) | ForEach-Object {
    [pscustomobject]@{
        Task      = "$TaskFolder$($_.TaskName)"
        State     = (Get-ScheduledTask -TaskPath $TaskFolder -TaskName $_.TaskName).State
        Enabled   = $_.Settings.Enabled
        Execute   = $_.Actions.Execute
        Arguments = $_.Actions.Arguments
        UserId    = $_.Principal.UserId
        LogonType = $_.Principal.LogonType
    }
} | Format-Table -AutoSize
