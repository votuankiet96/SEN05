[CmdletBinding()]
param(
    [string]$InstallDir = "",
    [string]$TaskFolder = "\SEN05\",
    [string]$TaskUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name,
    [switch]$StartNow
)

# Deployment installer for tick_program.exe on a machine that already has
# the SEN05_AutoTrading SQL Server schema in place (this build target does
# not need first-time schema creation -- see DEPLOY.md for what to do on a
# genuinely new SQL Server). Run from inside this same run_tick\ folder as
# Administrator:
#
#   powershell -ExecutionPolicy Bypass -File install.ps1
#
# Safe to re-run: every step checks its own current state first. Steps:
#   1. Re-launch elevated if not already Administrator.
#   2. Check/install ODBC Driver 18 for SQL Server (via winget; prints the
#      official download page and stops if winget is unavailable).
#   3. Config.yaml: if missing, copy Config.example.yaml to Config.yaml and
#      STOP here for the operator to fill in real values.
#   4. Register the Engine Scheduled Task (AtStartup, restart on crash) and
#      the Watchdog Scheduled Task (every 5 minutes, alerts if the engine
#      looks dead) -- both under \SEN05\, matching the names already used
#      in production so this is an in-place update, not a duplicate.

$ErrorActionPreference = "Stop"

if (-not $InstallDir) {
    if ($PSScriptRoot) {
        $InstallDir = $PSScriptRoot
    } elseif ($MyInvocation.MyCommand.Path) {
        $InstallDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    } else {
        $InstallDir = (Get-Location).Path
    }
}

$ExePath = Join-Path $InstallDir "tick_program.exe"
$EngineTask = "SEN05 Tick Program Engine"
$WatchdogTask = "SEN05 Tick Program Watchdog"

function Assert-Admin {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { return }
    Write-Host "Re-launching elevated (Administrator) -- accept the UAC prompt..."
    $arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"") + $PSBoundParameters.GetEnumerator().ForEach({
        if ($_.Value -is [switch]) { if ($_.Value) { "-$($_.Key)" } } else { "-$($_.Key)", "`"$($_.Value)`"" }
    })
    Start-Process powershell -Verb RunAs -ArgumentList $arguments
    exit
}

function Install-OdbcDriver18 {
    $driver = Get-OdbcDriver -Name "ODBC Driver 18 for SQL Server" -ErrorAction SilentlyContinue
    if ($driver) {
        Write-Host "[ok] ODBC Driver 18 for SQL Server already installed."
        return
    }
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "Installing ODBC Driver 18 via winget..."
        winget install --id Microsoft.msodbcsql.18 --accept-package-agreements --accept-source-agreements --silent
        return
    }
    throw (
        "ODBC Driver 18 for SQL Server is not installed, and winget is not " +
        "available to install it automatically. Download and install it from " +
        "https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server " +
        "then re-run this script."
    )
}

function Initialize-ConfigYaml {
    $configPath = Join-Path $InstallDir "Config.yaml"
    $examplePath = Join-Path $InstallDir "Config.example.yaml"
    if (Test-Path -LiteralPath $configPath) {
        Write-Host "[ok] Config.yaml already present."
        return $true
    }
    Copy-Item -LiteralPath $examplePath -Destination $configPath
    Write-Host ""
    Write-Host "Da tao Config.yaml tu Config.example.yaml." -ForegroundColor Yellow
    Write-Host "Dien thong tin SQL Server / cTrader / Discord that vao $configPath roi chay lai install.ps1."
    return $false
}

function Register-Tasks {
    if (-not (Test-Path -LiteralPath $ExePath)) { throw "Khong tim thay $ExePath." }

    $existing = Get-ScheduledTask -TaskPath $TaskFolder -TaskName $EngineTask -ErrorAction SilentlyContinue
    if ($existing -and $existing.State -eq "Running") {
        throw "$EngineTask dang chay. Dung an toan truoc khi dang ky lai (xem DEPLOY.md)."
    }

    $engineAction = New-ScheduledTaskAction -Execute $ExePath -WorkingDirectory $InstallDir
    $engineTrigger = New-ScheduledTaskTrigger -AtStartup
    $engineSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 999 `
        -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -Priority 5
    $engineSettings.ExecutionTimeLimit = "PT0S"
    $principal = New-ScheduledTaskPrincipal -UserId $TaskUser -LogonType S4U -RunLevel Highest
    $engineDef = New-ScheduledTask -Action $engineAction -Trigger $engineTrigger -Settings $engineSettings -Principal $principal
    Register-ScheduledTask -TaskPath $TaskFolder -TaskName $EngineTask -InputObject $engineDef -Force -ErrorAction Stop | Out-Null
    Write-Host "[ok] Scheduled Task '$TaskFolder$EngineTask' da dang ky (AtStartup, tu restart khi crash)."

    $watchdogAction = New-ScheduledTaskAction -Execute $ExePath -Argument "--watchdog" -WorkingDirectory $InstallDir
    $watchdogTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5) `
        -RepetitionDuration (New-TimeSpan -Days 3650)
    $watchdogSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -Priority 7
    $watchdogDef = New-ScheduledTask -Action $watchdogAction -Trigger $watchdogTrigger -Settings $watchdogSettings -Principal $principal
    Register-ScheduledTask -TaskPath $TaskFolder -TaskName $WatchdogTask -InputObject $watchdogDef -Force -ErrorAction Stop | Out-Null
    Write-Host "[ok] Scheduled Task '$TaskFolder$WatchdogTask' da dang ky (kiem tra moi 5 phut)."

    if ($StartNow) {
        Start-ScheduledTask -TaskPath $TaskFolder -TaskName $EngineTask
        Write-Host "[ok] Da khoi dong $EngineTask ngay."
    } else {
        Write-Host "Chua khoi dong (dung -StartNow de chay ngay thay vi doi lan reboot ke tiep)."
    }
}

Assert-Admin
Install-OdbcDriver18
$configReady = Initialize-ConfigYaml
if (-not $configReady) { exit 0 }
Register-Tasks
Write-Host ""
Write-Host "Install complete."
