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

function Invoke-NssmChecked {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$NssmArguments
    )
    & $script:nssm @NssmArguments
    if ($LASTEXITCODE -ne 0) {
        throw "NSSM command failed (exit $LASTEXITCODE): nssm $($NssmArguments -join ' ')"
    }
}

function Invoke-ScChecked {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$ScArguments
    )
    & sc.exe @ScArguments
    if ($LASTEXITCODE -ne 0) {
        throw "sc.exe command failed (exit $LASTEXITCODE): sc.exe $($ScArguments -join ' ')"
    }
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
$modulePath = (& $PythonExe -c "import core_engine; print(core_engine.__file__)" 2>$null | Select-Object -Last 1)
if ($LASTEXITCODE -ne 0) {
    throw "python -c `"import core_engine`" failed with $PythonExe. Run 'pip install -e .' from $AppRoot first (see docs/OPERATOR_RUNBOOK.md section 1)."
}
$modulePath = [IO.Path]::GetFullPath(([string]$modulePath).Trim())
$expectedModuleRoot = [IO.Path]::GetFullPath((Join-Path $AppRoot "src\core_engine"))
if (-not $modulePath.StartsWith($expectedModuleRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "core_engine resolves to '$modulePath', not this deployment at '$expectedModuleRoot'. Run '$PythonExe -m pip install -e .' from $AppRoot first."
}
Write-Host "OK: core_engine resolves to $modulePath"

$runtimeLogDir = Join-Path $AppRoot "runtime\logs\system"
New-Item -ItemType Directory -Force -Path $runtimeLogDir | Out-Null

$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    throw "Service '$ServiceName' already exists. Remove it first or choose another -ServiceName."
}

$installedByThisRun = $false
try {
    Invoke-NssmChecked install $ServiceName $PythonExe "-m core_engine run"
    $installedByThisRun = $true
    Invoke-NssmChecked set $ServiceName AppDirectory $AppRoot
    Invoke-NssmChecked set $ServiceName DisplayName "SEN05 Data Provider"
    Invoke-NssmChecked set $ServiceName Description "SEN05 backend data provider: live OHLCV fetching, scheduled historical backfill, health and Discord reporting."
    # Delayed-auto (not plain auto-start): the service depends on SQL Server
    # and network being reachable, which are not guaranteed to be up yet at
    # the exact moment Windows starts auto-start services on boot.
    Invoke-NssmChecked set $ServiceName Start SERVICE_DELAYED_AUTO_START
    Invoke-NssmChecked set $ServiceName AppStdout (Join-Path $runtimeLogDir "service_stdout.log")
    Invoke-NssmChecked set $ServiceName AppStderr (Join-Path $runtimeLogDir "service_stderr.log")
    Invoke-NssmChecked set $ServiceName AppRotateFiles 1
    Invoke-NssmChecked set $ServiceName AppRotateOnline 1
    Invoke-NssmChecked set $ServiceName AppRotateBytes 10485760
    Invoke-NssmChecked set $ServiceName AppThrottle 15000
    Invoke-NssmChecked set $ServiceName AppRestartDelay 30000
    Invoke-NssmChecked set $ServiceName AppExit Default Restart
    # Exit code 5 = EXIT_LOCK_CONFLICT. Leave the service stopped rather
    # than spending the application restart loop on a second supervisor.
    Invoke-NssmChecked set $ServiceName AppExit 5 Exit
    Invoke-NssmChecked set $ServiceName AppStopMethodConsole 240000
    Invoke-NssmChecked set $ServiceName AppStopMethodWindow 30000
    Invoke-NssmChecked set $ServiceName AppStopMethodThreads 30000
    Invoke-NssmChecked set $ServiceName AppStopMethodSkip 0

    if ($ServiceUser) {
        if (-not $ServicePassword) {
            throw "-ServiceUser was supplied without -ServicePassword. Both are required to set the service Log On account here."
        }
        $passwordBstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($ServicePassword)
        try {
            $plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringAuto($passwordBstr)
            Invoke-NssmChecked set $ServiceName ObjectName $ServiceUser $plainPassword
        } finally {
            if ($null -ne $passwordBstr) {
                [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordBstr)
            }
            $plainPassword = $null
        }
        Write-Host "Service Log On account set to: $ServiceUser"
    } else {
        Write-Host ""
        Write-Host "WARNING: -ServiceUser was not supplied - the service will run as LocalSystem." -ForegroundColor Yellow
        Write-Host "LocalSystem normally cannot see a per-user TradingView browser/cache profile," -ForegroundColor Yellow
        Write-Host "which breaks headless auth refresh after a reboot/restart. Prefer re-running" -ForegroundColor Yellow
        Write-Host "this script with -ServiceUser/-ServicePassword, or set it manually now:" -ForegroundColor Yellow
        Write-Host "  services.msc -> $ServiceName -> Properties -> Log On" -ForegroundColor Yellow
    }

    # NSSM restarts the Python app. SCM recovery is a separate safety net
    # for the service wrapper itself being killed or crashing. AppExit 5
    # remains an intentional NSSM stop and is not marked as a non-crash
    # failure, so it does not become a second conflict restart loop.
    Invoke-ScChecked failure $ServiceName "reset=" "86400" "actions=" "restart/60000/restart/120000/restart/300000"
} catch {
    if ($installedByThisRun) {
        Write-Warning "Service installation failed; removing the partial service definition."
        & $nssm remove $ServiceName confirm | Out-Null
    }
    throw
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
