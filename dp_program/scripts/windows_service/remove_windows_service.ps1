param(
    [string]$ServiceName = "SEN05DataProvider",
    [string]$AppRoot = "",
    [string]$PythonExe = "",
    [string]$NssmPath = "nssm.exe"
)

$ErrorActionPreference = "Stop"

function Require-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run PowerShell as Administrator before removing the Windows service."
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

$service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if (-not $service) {
    Write-Host "Service '$ServiceName' does not exist."
    exit 0
}

Push-Location $AppRoot
try {
    & $PythonExe -m core_engine stop --reason "windows_service_remove"
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "The cooperative stop command exited $LASTEXITCODE; continuing with Windows service stop."
    }
} finally {
    Pop-Location
}

Start-Sleep -Seconds 5

$service.Refresh()
if ($service.Status -ne "Stopped") {
    Stop-Service -Name $ServiceName -ErrorAction SilentlyContinue
}

& $nssm remove $ServiceName confirm
if ($LASTEXITCODE -ne 0) {
    throw "NSSM could not remove service '$ServiceName' (exit $LASTEXITCODE)."
}
Write-Host "Removed Windows service: $ServiceName"
