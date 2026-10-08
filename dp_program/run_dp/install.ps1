[CmdletBinding()]
param(
    [string]$InstallDir = "",
    [string]$TaskFolder = "\SEN05\",
    [string]$TaskUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name,
    [switch]$SetupSqlSchema,
    [string]$SqlServer,
    [switch]$StartNow
)

# Deployment installer for a fresh machine that has never run dp_program
# before. Meant to be run from inside this same folder (the whole run_dp/
# folder, copied here from the build machine) as Administrator:
#
#   powershell -ExecutionPolicy Bypass -File install.ps1
#
# Safe to re-run: every step checks its own current state first and skips
# work already done. Steps, in order:
#   1. Re-launch elevated if not already Administrator (needed for the
#      ODBC driver, sqlcmd, and Task Scheduler registration below).
#   2. Check/install ODBC Driver 18 for SQL Server (tries winget; if
#      winget is not present on this machine, prints the official
#      download page and stops here rather than guessing a direct MSI
#      URL that could go stale).
#   3. Check/install sqlcmd, the same way -- needed to run the bundled
#      SQL schema scripts without any Python on this machine.
#   4. Copy the bundled Chromium build (vendor\ms-playwright\, prepared
#      on the build machine to match the exact Playwright version baked
#      into dp_program.exe) into %LOCALAPPDATA%\ms-playwright\. Fully
#      automatic and offline -- no download needed for this step.
#   5. config.yaml: if missing, copy config.example.yaml to config.yaml
#      and STOP here. Real credentials cannot be filled in
#      automatically; re-run this script once config.yaml is edited.
#   6. Only with -SetupSqlSchema (opt-in, off by default): run the
#      bundled sql\00_run_all.sql against -SqlServer. Only
#      pass this on a genuinely new SQL Server -- running it again
#      against the real production database is harmless (idempotent,
#      never drops/truncates existing data) but is not something this
#      script should ever do without being asked.
#   7. Register one Scheduled Task for dp_program.exe: AtStartup,
#      restart up to 999 times at 1-minute intervals on a non-zero exit.
#      dp_program.exe itself decides live vs backfill vs both from
#      config.yaml's enabled flags -- this script does not know or care
#      which.

$ErrorActionPreference = "Stop"

if (-not $InstallDir) {
    # $PSScriptRoot is not always populated when this script is launched
    # via `powershell -File` (observed empty in some invocation contexts
    # even though it is the documented, normal way to run this script) --
    # fall back to the running script's own path, then finally the
    # current directory, rather than fail outright on something this
    # basic.
    if ($PSScriptRoot) {
        $InstallDir = $PSScriptRoot
    }
    elseif ($MyInvocation.MyCommand.Path) {
        $InstallDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    }
    else {
        $InstallDir = (Get-Location).Path
    }
}

function Assert-Admin {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        return
    }
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
        "available on this machine to install it automatically. Download and " +
        "install it manually from " +
        "https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server " +
        "then re-run this script."
    )
}

function Install-Sqlcmd {
    if (Get-Command sqlcmd -ErrorAction SilentlyContinue) {
        Write-Host "[ok] sqlcmd already available."
        return
    }
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "Installing sqlcmd via winget..."
        winget install --id Microsoft.sqlcmd --accept-package-agreements --accept-source-agreements --silent
        return
    }
    throw (
        "sqlcmd is not installed, and winget is not available on this machine " +
        "to install it automatically. Download it manually from " +
        "https://learn.microsoft.com/sql/tools/sqlcmd/sqlcmd-utility then re-run " +
        "this script."
    )
}

function Install-Chromium {
    $source = Join-Path $InstallDir "vendor\ms-playwright"
    if (-not (Test-Path -LiteralPath $source)) {
        Write-Warning "vendor\ms-playwright not found next to install.ps1 -- this run_dp package was not assembled with the bundled browser. TradingView headless login will not work until this is fixed."
        return
    }
    $target = Join-Path $env:LOCALAPPDATA "ms-playwright"
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    Copy-Item -Path (Join-Path $source "*") -Destination $target -Recurse -Force
    Write-Host "[ok] Chromium copied to $target"
}

function Initialize-ConfigYaml {
    $configPath = Join-Path $InstallDir "config.yaml"
    if (Test-Path -LiteralPath $configPath) {
        Write-Host "[ok] config.yaml already present."
        return $true
    }
    Copy-Item -Path (Join-Path $InstallDir "config.example.yaml") -Destination $configPath
    Write-Host ""
    Write-Host "config.yaml created from the example template."
    Write-Host "Edit it now with real SQL Server / TradingView / Discord settings,"
    Write-Host "then run install.ps1 again to continue."
    return $false
}

function Install-SqlSchema {
    if (-not $SqlServer) {
        throw "-SetupSqlSchema requires -SqlServer."
    }
    # sql\01_setup_database.sql creates and USEs the SEN05_AutoTrading
    # database by name -- it is not parameterized, so no -d flag is
    # passed here; whatever the initial connection database is gets
    # switched away from immediately by the script itself.
    Write-Host "Running SQL warehouse schema installer against $SqlServer (database: SEN05_AutoTrading)..."
    Push-Location (Join-Path $InstallDir "sql")
    try {
        sqlcmd -b -S $SqlServer -E -i "00_run_all.sql"
        if ($LASTEXITCODE -ne 0) {
            throw "SQL schema installer failed (sqlcmd exit code $LASTEXITCODE)."
        }
    }
    finally {
        Pop-Location
    }
    Write-Host "[ok] SQL warehouse schema verified."
}

function Register-EngineTask {
    $exePath = Join-Path $InstallDir "dp_program.exe"
    if (-not (Test-Path -LiteralPath $exePath -PathType Leaf)) {
        throw "dp_program.exe not found in $InstallDir."
    }
    $name = "SEN05 DP Program Engine"
    $existing = Get-ScheduledTask -TaskPath $TaskFolder -TaskName $name -ErrorAction SilentlyContinue
    if ($existing -and $existing.State -eq "Running") {
        throw "$name is already running. Stop it before re-running this installer."
    }
    # The actual New-ScheduledTaskAction/Trigger/Settings/Principal call
    # lives in exactly one place -- dp_program_task_setup.py's
    # install_tasks(), bundled into the exe -- so this and the
    # interactive menu's own "Cai/cap nhat Task Scheduler" option can
    # never drift apart (they did once already: the watchdog task's
    # [TimeSpan]::MaxValue bug was already fixed here and had to be
    # re-fixed there, because the two were separate copies).
    # --setup registers BOTH canonical tasks (engine + watchdog) and
    # removes the retired first-generation Live/Backfill tasks.
    # We are already elevated (Assert-Admin ran above), so this calls
    # dp_program_entry directly rather than going through a UAC relaunch;
    # -TaskUser is not honored here -- the exe always registers under
    # $env:USERDOMAIN\$env:USERNAME, i.e. whoever is running install.ps1.
    & $exePath --setup
    if ($LASTEXITCODE -ne 0) {
        throw "dp_program.exe --setup failed (exit $LASTEXITCODE)."
    }
    if ($StartNow) {
        Start-ScheduledTask -TaskPath $TaskFolder -TaskName $name
        Write-Host "[ok] Started now."
    }
    else {
        Write-Host "Not started (pass -StartNow to start immediately instead of waiting for next reboot)."
    }
}

Assert-Admin
Install-OdbcDriver18
Install-Sqlcmd
Install-Chromium
$configReady = Initialize-ConfigYaml
if (-not $configReady) {
    exit 0
}
if ($SetupSqlSchema) {
    Install-SqlSchema
}
Register-EngineTask
Write-Host ""
Write-Host "Install complete. See DEPLOY.md for the full checklist and troubleshooting."
