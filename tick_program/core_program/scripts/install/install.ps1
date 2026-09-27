[CmdletBinding()]
param(
    [string]$InstallDir = "",
    [string]$TaskFolder = "\SEN05\",
    [string]$TaskUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name,
    [switch]$SetupSqlSchema,
    [string]$SqlServer,
    [switch]$StartNow
)

# Deployment installer for tick_program, mirroring dp_program_v3's
# run_dp\install.ps1 pattern. Run as Administrator from the repo root:
#
#   powershell -ExecutionPolicy Bypass -File scripts\install\install.ps1
#
# Safe to re-run: every step checks its own current state first. Steps:
#   1. Re-launch elevated if not already Administrator.
#   2. Check/install ODBC Driver 18 for SQL Server (winget; prints the
#      official download page and stops if winget is unavailable).
#   3. Config.yaml (repo root): if missing, STOP and tell the operator to
#      create it (see docs\ARCHITECTURE.md for the expected shape) with
#      real SQL Server / cTrader / Discord values, then re-run.
#   4. Only with -SetupSqlSchema (opt-in, off by default): run
#      scripts\deploy_schema.py against -SqlServer. Idempotent
#      (never drops/truncates existing data) but not something this
#      script does automatically.
#   5. Register one Scheduled Task for `python -m src service`:
#      AtStartup, restart up to 999 times at 1-minute intervals on a
#      non-zero exit. This is the auto-restart mechanism tick_program did
#      not have before — a machine reboot no longer requires an operator
#      to notice and start it back up by hand.

$ErrorActionPreference = "Stop"

if (-not $InstallDir) {
    # This script lives at scripts\install\install.ps1 -- repo root is two
    # levels up.
    if ($PSScriptRoot) {
        $InstallDir = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    } elseif ($MyInvocation.MyCommand.Path) {
        $InstallDir = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
    } else {
        $InstallDir = (Get-Location).Path
    }
}

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
    if (Test-Path -LiteralPath $configPath) {
        Write-Host "[ok] Config.yaml already present."
        return $true
    }
    Write-Host ""
    Write-Host "Config.yaml not found at $configPath." -ForegroundColor Yellow
    Write-Host "Create it with real SQL Server / cTrader / Discord settings"
    Write-Host "(see docs\ARCHITECTURE.md for the expected keys), then run install.ps1 again."
    return $false
}

function Install-SqlSchema {
    if (-not $SqlServer) { throw "-SetupSqlSchema requires -SqlServer." }
    Write-Host "Running SQL tick schema installer against $SqlServer..."
    $python = (Get-Command python -ErrorAction Stop).Source
    & $python -B (Join-Path $InstallDir "scripts\deploy_schema.py")
    if ($LASTEXITCODE -ne 0) { throw "SQL schema installer failed (exit code $LASTEXITCODE)." }
    Write-Host "[ok] SQL tick schema verified."
}

function Register-EngineTask {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) { throw "python was not found on PATH." }
    $name = "SEN05 Tick Program Engine"
    $existing = Get-ScheduledTask -TaskPath $TaskFolder -TaskName $name -ErrorAction SilentlyContinue
    if ($existing -and $existing.State -eq "Running") {
        throw "$name is already running. Stop it (graceful stop sentinel or Stop-ScheduledTask) before re-running this installer."
    }
    $action = New-ScheduledTaskAction -Execute $python.Source -Argument "-B -m src service" -WorkingDirectory $InstallDir
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
        -MultipleInstances IgnoreNew -Priority 5
    $settings.ExecutionTimeLimit = "PT0S"
    $principal = New-ScheduledTaskPrincipal -UserId $TaskUser -LogonType S4U -RunLevel Highest
    $definition = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal
    Register-ScheduledTask -TaskPath $TaskFolder -TaskName $name -InputObject $definition -Force -ErrorAction Stop | Out-Null
    Write-Host "[ok] Scheduled Task '$TaskFolder$name' registered (AtStartup, restart on failure)."
    if ($StartNow) {
        Start-ScheduledTask -TaskPath $TaskFolder -TaskName $name
        Write-Host "[ok] Started now."
    } else {
        Write-Host "Not started (pass -StartNow to start immediately instead of waiting for next reboot)."
    }
}

Assert-Admin
Install-OdbcDriver18
$configReady = Initialize-ConfigYaml
if (-not $configReady) { exit 0 }
if ($SetupSqlSchema) { Install-SqlSchema }
Register-EngineTask
Write-Host ""
Write-Host "Install complete."
