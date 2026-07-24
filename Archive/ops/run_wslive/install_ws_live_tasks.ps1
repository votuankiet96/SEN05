param(
    [string]$RepoRoot = "",
    [string]$TaskPath = "\SEN05\",
    [string]$SupervisorTaskName = "SEN05_WsLive_Supervisor",
    [string]$WatchdogTaskName = "SEN05_WsLive_Watchdog",
    [int]$RestartDelaySec = 30,
    [int]$WatchdogEverySeconds = 60,
    [switch]$Force,
    [switch]$NoStart,
    [switch]$RunWhetherLoggedOnOrNot,
    [string]$TaskUser = ""
)

Set-StrictMode -Version Latest

$modulePath = Join-Path $PSScriptRoot "lib\WsLiveOps.psm1"
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

function New-WsLiveTaskSettings {
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

$paths = Get-WsLivePaths -RepoRoot $RepoRoot
Initialize-WsLiveRuntime -Paths $paths

if (-not $TaskUser) {
    $TaskUser = "$env:USERDOMAIN\$env:USERNAME"
}

$psExe = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"
$supervisorScript = Join-Path $paths.RunRoot "ws_live_supervisor.ps1"
$watchdogScript = Join-Path $paths.RunRoot "ws_live_watchdog.ps1"

if (-not (Test-Path -LiteralPath $supervisorScript)) {
    throw "Supervisor script not found: $supervisorScript"
}
if (-not (Test-Path -LiteralPath $watchdogScript)) {
    throw "Watchdog script not found: $watchdogScript"
}

Remove-TaskIfNeeded -Name $SupervisorTaskName -Path $TaskPath -ForceRemove:$Force
Remove-TaskIfNeeded -Name $WatchdogTaskName -Path $TaskPath -ForceRemove:$Force

$settings = New-WsLiveTaskSettings

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
    $principal = New-ScheduledTaskPrincipal -UserId $TaskUser -LogonType InteractiveToken -RunLevel Highest
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

$watchdogTaskNameArg = $SupervisorTaskName.Replace("'", "''")
$watchdogTaskPathArg = $TaskPath.Replace("'", "''")
$watchdogArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$watchdogScript`" -RepoRoot `"$($paths.RepoRoot)`" -TaskName '$watchdogTaskNameArg' -TaskPath '$watchdogTaskPathArg'"
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

if (-not $NoStart) {
    Start-ScheduledTask -TaskName $SupervisorTaskName -TaskPath $TaskPath
    Start-ScheduledTask -TaskName $WatchdogTaskName -TaskPath $TaskPath
}

Write-Host ""
Write-Host "Installed ws_live tasks." -ForegroundColor Cyan
Write-Host ("Supervisor: {0}{1}" -f $TaskPath, $SupervisorTaskName)
Write-Host ("Watchdog  : {0}{1}" -f $TaskPath, $WatchdogTaskName)
Write-Host ("Mode      : {0}" -f ($(if ($RunWhetherLoggedOnOrNot) { "run whether logged on or not" } else { "interactive hidden" })))
Write-Host ("Viewer    : {0}" -f (Join-Path $paths.RunRoot "ws_live_log_viewer.bat"))
Write-Host ""
& (Join-Path $paths.RunRoot "ws_live_status.ps1") -RepoRoot $paths.RepoRoot -TaskPath $TaskPath -SupervisorTaskName $SupervisorTaskName -WatchdogTaskName $WatchdogTaskName
