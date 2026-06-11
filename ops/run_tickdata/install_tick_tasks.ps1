param(
    [string]$RepoRoot = "",
    [string]$TaskPath = "\SEN05\",
    [string]$SupervisorTaskName = "SEN05_TickLive_Supervisor",
    [string]$WatchdogTaskName = "SEN05_TickLive_Watchdog",
    [string]$CheckerTaskName = "SEN05_TickData_Checker",
    [int]$RestartDelaySec = 30,
    [int]$WatchdogEverySeconds = 60,
    [int]$CheckerEveryMinutes = 5,
    [switch]$NoChecker,
    [switch]$Force,
    [switch]$NoStart,
    [switch]$RunWhetherLoggedOnOrNot,
    [string]$TaskUser = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$modulePath = Join-Path $PSScriptRoot "lib\TickDataOps.psm1"
Import-Module $modulePath -Force

function Test-IsAdmin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Remove-TaskIfNeeded {
    param(
        [string]$Name,
        [string]$Path,
        [switch]$ForceRemove
    )

    $existing = Get-ScheduledTask -TaskName $Name -TaskPath $Path -ErrorAction SilentlyContinue
    if ($null -eq $existing) {
        return
    }

    if (-not $ForceRemove) {
        throw "Scheduled task already exists: $Path$Name. Re-run with -Force to replace it."
    }

    Stop-ScheduledTask -TaskName $Name -TaskPath $Path -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $Name -TaskPath $Path -Confirm:$false
}

function New-TickDataTaskSettings {
    try {
        return New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -StartWhenAvailable `
            -MultipleInstances IgnoreNew `
            -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
            -RestartCount 3 `
            -RestartInterval (New-TimeSpan -Minutes 1)
    }
    catch {
        return New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -StartWhenAvailable `
            -MultipleInstances IgnoreNew `
            -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
    }
}

if (-not (Test-IsAdmin)) {
    throw "Run this installer from an elevated PowerShell session."
}

$paths = Get-TickDataPaths -RepoRoot $RepoRoot
Initialize-TickDataRuntime -Paths $paths

if (-not $TaskUser) {
    $TaskUser = "$env:USERDOMAIN\$env:USERNAME"
}

$psExe = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"
$supervisorScript = Join-Path $paths.RunRoot "tick_live_supervisor.ps1"
$watchdogScript = Join-Path $paths.RunRoot "tick_live_watchdog.ps1"
$checkerScript = Join-Path $paths.RunRoot "tick_check.ps1"

foreach ($script in @($supervisorScript, $watchdogScript, $checkerScript)) {
    if (-not (Test-Path -LiteralPath $script)) {
        throw "Script not found: $script"
    }
}

Remove-TaskIfNeeded -Name $SupervisorTaskName -Path $TaskPath -ForceRemove:$Force
Remove-TaskIfNeeded -Name $WatchdogTaskName -Path $TaskPath -ForceRemove:$Force
if (-not $NoChecker) {
    Remove-TaskIfNeeded -Name $CheckerTaskName -Path $TaskPath -ForceRemove:$Force
}

$settings = New-TickDataTaskSettings

if ($RunWhetherLoggedOnOrNot) {
    $secure = Read-Host -Prompt "Password for $TaskUser" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
    $principal = New-ScheduledTaskPrincipal -UserId $TaskUser -LogonType Password -RunLevel Highest
}
else {
    $plainPassword = $null
    $principal = New-ScheduledTaskPrincipal -UserId $TaskUser -LogonType Interactive -RunLevel Highest
}

$supervisorArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$supervisorScript`" -RepoRoot `"$($paths.RepoRoot)`" -RestartDelaySec $RestartDelaySec"
$supervisorAction = New-ScheduledTaskAction -Execute $psExe -Argument $supervisorArgs -WorkingDirectory $paths.RepoRoot
$supervisorTriggers = @()
$supervisorTriggers += New-ScheduledTaskTrigger -AtStartup
$supervisorTriggers += New-ScheduledTaskTrigger -AtLogOn
$supervisorTask = New-ScheduledTask -Action $supervisorAction -Trigger $supervisorTriggers -Settings $settings -Principal $principal

if ($RunWhetherLoggedOnOrNot) {
    Register-ScheduledTask -TaskName $SupervisorTaskName -TaskPath $TaskPath -InputObject $supervisorTask -User $TaskUser -Password $plainPassword -Force | Out-Null
}
else {
    Register-ScheduledTask -TaskName $SupervisorTaskName -TaskPath $TaskPath -InputObject $supervisorTask -Force | Out-Null
}

$watchdogArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$watchdogScript`" -RepoRoot `"$($paths.RepoRoot)`" -TaskName $SupervisorTaskName -TaskPath $TaskPath"
$watchdogAction = New-ScheduledTaskAction -Execute $psExe -Argument $watchdogArgs -WorkingDirectory $paths.RepoRoot
$watchdogTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Seconds $WatchdogEverySeconds) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$watchdogTask = New-ScheduledTask -Action $watchdogAction -Trigger $watchdogTrigger -Settings $settings -Principal $principal

if ($RunWhetherLoggedOnOrNot) {
    Register-ScheduledTask -TaskName $WatchdogTaskName -TaskPath $TaskPath -InputObject $watchdogTask -User $TaskUser -Password $plainPassword -Force | Out-Null
}
else {
    Register-ScheduledTask -TaskName $WatchdogTaskName -TaskPath $TaskPath -InputObject $watchdogTask -Force | Out-Null
}

if (-not $NoChecker) {
    $checkerArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$checkerScript`" -RepoRoot `"$($paths.RepoRoot)`""
    $checkerAction = New-ScheduledTaskAction -Execute $psExe -Argument $checkerArgs -WorkingDirectory $paths.RepoRoot
    $checkerTrigger = New-ScheduledTaskTrigger `
        -Once `
        -At (Get-Date).AddMinutes(2) `
        -RepetitionInterval (New-TimeSpan -Minutes $CheckerEveryMinutes) `
        -RepetitionDuration (New-TimeSpan -Days 3650)
    $checkerTask = New-ScheduledTask -Action $checkerAction -Trigger $checkerTrigger -Settings $settings -Principal $principal

    if ($RunWhetherLoggedOnOrNot) {
        Register-ScheduledTask -TaskName $CheckerTaskName -TaskPath $TaskPath -InputObject $checkerTask -User $TaskUser -Password $plainPassword -Force | Out-Null
    }
    else {
        Register-ScheduledTask -TaskName $CheckerTaskName -TaskPath $TaskPath -InputObject $checkerTask -Force | Out-Null
    }
}

if (-not $NoStart) {
    Start-ScheduledTask -TaskName $SupervisorTaskName -TaskPath $TaskPath
    Start-ScheduledTask -TaskName $WatchdogTaskName -TaskPath $TaskPath
    if (-not $NoChecker) {
        Start-ScheduledTask -TaskName $CheckerTaskName -TaskPath $TaskPath
    }
}

Write-Host ""
Write-Host "Installed tick_data tasks." -ForegroundColor Cyan
Write-Host ("Supervisor: {0}{1}" -f $TaskPath, $SupervisorTaskName)
Write-Host ("Watchdog  : {0}{1}" -f $TaskPath, $WatchdogTaskName)
if (-not $NoChecker) {
    Write-Host ("Checker   : {0}{1}" -f $TaskPath, $CheckerTaskName)
}
Write-Host ("Mode      : {0}" -f ($(if ($RunWhetherLoggedOnOrNot) { "run whether logged on or not" } else { "interactive hidden" })))
Write-Host ("Viewer    : {0}" -f (Join-Path $paths.RunRoot "tick_log_viewer.bat"))
Write-Host ""
& (Join-Path $paths.RunRoot "tick_status.ps1") -RepoRoot $paths.RepoRoot -TaskPath $TaskPath -SupervisorTaskName $SupervisorTaskName -WatchdogTaskName $WatchdogTaskName -CheckerTaskName $CheckerTaskName
