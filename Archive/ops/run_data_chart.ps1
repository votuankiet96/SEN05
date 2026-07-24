param(
    [switch]$Foreground,
    [switch]$NoBrowser,
    [switch]$FollowLog,
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"

$ModulePath = Join-Path $PSScriptRoot "lib\Sen05Ops.psm1"
Import-Module $ModulePath -Force

exit (Start-Sen05DataChart -Foreground:$Foreground -NoBrowser:$NoBrowser -FollowLog:$FollowLog -PythonExe $PythonExe)
