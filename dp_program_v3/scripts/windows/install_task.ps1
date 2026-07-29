[CmdletBinding()]
param(
    [string]$PythonPath = (Get-Command python -ErrorAction Stop).Source,
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [string]$TaskPath = "\SEN05\",
    [string]$TaskName = "SEN05 DP Program 24x7",
    [string]$TaskUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name,
    [switch]$Start
)

$ErrorActionPreference = "Stop"
$configPath = Join-Path $RepoRoot "Config.yaml"
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Python executable not found: $PythonPath"
}
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw "Config.yaml not found under repository root: $RepoRoot"
}
Push-Location $RepoRoot
try {
    & $PythonPath -c "import dp_program; print('dp_program_v3_import=ok')"
    if ($LASTEXITCODE -ne 0) {
        throw "The selected Python interpreter cannot import dp_program."
    }
    & $PythonPath -m dp_program doctor
    if ($LASTEXITCODE -ne 0) {
        throw "V3 doctor failed; Scheduled Task was not changed."
    }
}
finally {
    Pop-Location
}

$action = New-ScheduledTaskAction `
    -Execute $PythonPath `
    -Argument "-m dp_program run" `
    -WorkingDirectory $RepoRoot
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew
$existing = Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing -and $existing.State -eq "Running") {
    throw "Task is running. Stop V3 gracefully before updating its definition."
}
$principal = New-ScheduledTaskPrincipal `
    -UserId $TaskUser `
    -LogonType S4U `
    -RunLevel Highest
$definition = New-ScheduledTask `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal
Register-ScheduledTask `
    -TaskPath $TaskPath `
    -TaskName $TaskName `
    -InputObject $definition `
    -Force `
    -ErrorAction Stop | Out-Null

if ($Start) {
    Push-Location $RepoRoot
    try {
        & $PythonPath -c "from pathlib import Path; from dp_program.configuration import load_config; (Path(load_config()['app']['runtime_dir']) / 'run' / 'stop.request').unlink(missing_ok=True)"
        if ($LASTEXITCODE -ne 0) {
            throw "The previous stop request could not be cleared safely."
        }
    }
    finally {
        Pop-Location
    }
    Start-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction Stop
}

$task = Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName
if (
    $task.Actions.Execute -ne $PythonPath `
    -or $task.Actions.Arguments -ne "-m dp_program run" `
    -or $task.Actions.WorkingDirectory -ne $RepoRoot `
    -or [string]$task.Principal.LogonType -ne "S4U" `
    -or [string]$task.Principal.RunLevel -ne "Highest" `
    -or [string]$task.Settings.MultipleInstances -ne "IgnoreNew" `
    -or [string]$task.Settings.ExecutionTimeLimit -ne "PT0S" `
    -or [int]$task.Settings.RestartCount -ne 999 `
    -or [string]$task.Settings.RestartInterval -ne "PT1M" `
    -or -not [bool]$task.Settings.Enabled `
    -or $task.Triggers.Count -ne 1 `
    -or $task.Triggers[0].CimClass.CimClassName -ne "MSFT_TaskBootTrigger"
) {
    throw "Scheduled Task verification failed after registration."
}
[pscustomobject]@{
    Task = "$TaskPath$TaskName"
    State = $task.State
    Enabled = $task.Settings.Enabled
    Execute = $task.Actions.Execute
    Arguments = $task.Actions.Arguments
    WorkingDirectory = $task.Actions.WorkingDirectory
    UserId = $task.Principal.UserId
    LogonType = $task.Principal.LogonType
    RestartCount = $task.Settings.RestartCount
    RestartInterval = $task.Settings.RestartInterval
}
