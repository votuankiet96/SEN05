param(
    [string]$ServiceName = "SEN05DataProvider",
    [string]$AppRoot = "",
    [string]$PythonExe = "",
    [string]$NssmPath = "nssm.exe",
    # Windows account to run the service as (DOMAIN\user or .\user), e.g.
    # the same account that owns the TradingView browser/cache profile.
    # If not supplied, NSSM defaults to LocalSystem, which normally cannot
    # see that profile - see docs/OPERATOR_RUNBOOK.md section 6. Passing
    # this explicitly here (instead of relying on a manual services.msc
    # step after install) is the round-2 audit H13 fix: a manual step is
    # easy to skip, and skipping it fails silently until the next headless
    # auth refresh is needed.
    [string]$ServiceUser = "",
    [securestring]$ServicePassword,
    [switch]$Start
)

$ErrorActionPreference = "Stop"

function Require-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run PowerShell as Administrator before installing the Windows service."
    }
}

function Resolve-CommandPath([string]$CommandName) {
    $cmd = Get-Command $CommandName -ErrorAction SilentlyContinue
    if ($null -eq $cmd) {
        return $null
    }
    return $cmd.Source
}

Require-Administrator

if (-not $AppRoot) {
    $AppRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
} else {
    $AppRoot = (Resolve-Path $AppRoot).Path
}

if (-not $PythonExe) {
    $PythonExe = Resolve-CommandPath "python.exe"
    if (-not $PythonExe) {
        $PythonExe = Resolve-CommandPath "python"
    }
}
if (-not $PythonExe) {
    throw "python.exe was not found in PATH. Pass -PythonExe explicitly."
}

$nssm = Resolve-CommandPath $NssmPath
if (-not $nssm -and (Test-Path $NssmPath)) {
    $nssm = (Resolve-Path $NssmPath).Path
}
if (-not $nssm) {
    throw "NSSM was not found. Install NSSM or pass -NssmPath C:\path\to\nssm.exe."
}

# Deploy guard: catch a missing `pip install -e .` (or a broken venv) here,
# with a clear error, rather than installing a service that will just
# crash-loop with an unhelpful ImportError in service_stderr.log.
Write-Host "Validating core_engine is importable..."
& $PythonExe -c "import core_engine" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "python -c `"import core_engine`" failed with $PythonExe. Run 'pip install -e .' from $AppRoot first (see docs/OPERATOR_RUNBOOK.md section 1)."
}
Write-Host "OK: core_engine is importable."

$runtimeLogDir = Join-Path $AppRoot "runtime\logs\system"
New-Item -ItemType Directory -Force -Path $runtimeLogDir | Out-Null

$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    throw "Service '$ServiceName' already exists. Remove it first or choose another -ServiceName."
}

& $nssm install $ServiceName $PythonExe "-m core_engine run"
& $nssm set $ServiceName AppDirectory $AppRoot
& $nssm set $ServiceName DisplayName "SEN05 Data Provider"
& $nssm set $ServiceName Description "SEN05 backend data provider: live OHLCV fetching, scheduled historical backfill, health and Discord reporting."
# Delayed-auto (not plain auto-start): the service depends on SQL Server
# and network being reachable, which are not guaranteed to be up yet at
# the exact moment Windows starts auto-start services on boot. Delayed
# start gives those a head start instead of racing them - the process's
# own startup checks (test_connection(), verify_database_contract()) plus
# NSSM's AppRestartDelay still apply as a second layer of defense either way.
& $nssm set $ServiceName Start SERVICE_DELAYED_AUTO_START
& $nssm set $ServiceName AppStdout (Join-Path $runtimeLogDir "service_stdout.log")
& $nssm set $ServiceName AppStderr (Join-Path $runtimeLogDir "service_stderr.log")
& $nssm set $ServiceName AppRotateFiles 1
& $nssm set $ServiceName AppRotateOnline 1
& $nssm set $ServiceName AppRotateBytes 10485760
& $nssm set $ServiceName AppThrottle 15000
& $nssm set $ServiceName AppRestartDelay 30000
& $nssm set $ServiceName AppExit Default Restart
# Exit code 5 = EXIT_LOCK_CONFLICT (core_engine.exit_codes): another
# supervisor already holds the DP Program advisory lock. Restarting
# immediately (even with the 30s AppRestartDelay above) just repeats the
# same conflict until an operator resolves it, wasting the NSSM restart
# budget that should be reserved for genuine crashes. Exit instead of
# Restart for this specific code and let the operator investigate
# (`python -m core_engine status` / conflict-status) before restarting by
# hand.
& $nssm set $ServiceName AppExit 5 Exit
& $nssm set $ServiceName AppStopMethodConsole 240000
& $nssm set $ServiceName AppStopMethodWindow 30000
& $nssm set $ServiceName AppStopMethodThreads 30000
& $nssm set $ServiceName AppStopMethodSkip 0

if ($ServiceUser) {
    if (-not $ServicePassword) {
        throw "-ServiceUser was supplied without -ServicePassword. Both are required to set the service Log On account here."
    }
    $plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($ServicePassword)
    )
    & $nssm set $ServiceName ObjectName $ServiceUser $plainPassword
    Write-Host "Service Log On account set to: $ServiceUser"
} else {
    Write-Host ""
    Write-Host "WARNING: -ServiceUser was not supplied - the service will run as LocalSystem." -ForegroundColor Yellow
    Write-Host "LocalSystem normally cannot see a per-user TradingView browser/cache profile," -ForegroundColor Yellow
    Write-Host "which breaks headless auth refresh after a reboot/restart. Prefer re-running" -ForegroundColor Yellow
    Write-Host "this script with -ServiceUser/-ServicePassword, or set it manually now:" -ForegroundColor Yellow
    Write-Host "  services.msc -> $ServiceName -> Properties -> Log On" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Installed Windows service: $ServiceName"
Write-Host "App root : $AppRoot"
Write-Host "Python   : $PythonExe"
Write-Host "Logs     : $runtimeLogDir"

if ($Start) {
    Start-Service -Name $ServiceName
    Write-Host "Started service: $ServiceName"
}
