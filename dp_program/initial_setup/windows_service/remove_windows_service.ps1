param(
    [string]$ServiceName = "SEN05DataProvider",
    [string]$AppRoot = "",
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
    python -m core_engine stop --reason "windows_service_remove"
} finally {
    Pop-Location
}

Start-Sleep -Seconds 5

$service.Refresh()
if ($service.Status -ne "Stopped") {
    Stop-Service -Name $ServiceName -ErrorAction SilentlyContinue
}

& $nssm remove $ServiceName confirm
Write-Host "Removed Windows service: $ServiceName"
