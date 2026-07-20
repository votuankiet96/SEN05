param(
    [string]$ServiceName = "SEN05DataProvider",
    [string]$AppRoot = "",
    [string]$PythonExe = "",
    [string]$NssmPath = "nssm.exe",
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
& $nssm set $ServiceName Start SERVICE_AUTO_START
& $nssm set $ServiceName AppStdout (Join-Path $runtimeLogDir "service_stdout.log")
& $nssm set $ServiceName AppStderr (Join-Path $runtimeLogDir "service_stderr.log")
& $nssm set $ServiceName AppRotateFiles 1
& $nssm set $ServiceName AppRotateOnline 1
& $nssm set $ServiceName AppRotateBytes 10485760
& $nssm set $ServiceName AppThrottle 15000
& $nssm set $ServiceName AppRestartDelay 30000
& $nssm set $ServiceName AppExit Default Restart
& $nssm set $ServiceName AppStopMethodConsole 240000
& $nssm set $ServiceName AppStopMethodWindow 30000
& $nssm set $ServiceName AppStopMethodThreads 30000
& $nssm set $ServiceName AppStopMethodSkip 0

Write-Host "Installed Windows service: $ServiceName"
Write-Host "App root : $AppRoot"
Write-Host "Python   : $PythonExe"
Write-Host "Logs     : $runtimeLogDir"
Write-Host ""
Write-Host "Recommended: configure the service Log On account as the same Windows user that owns TradingView browser/cache access."
Write-Host "Use: services.msc -> $ServiceName -> Properties -> Log On"

if ($Start) {
    Start-Service -Name $ServiceName
    Write-Host "Started service: $ServiceName"
}
