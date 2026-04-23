param(
    [int]$RestartDelaySec = 20,
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $PythonExe) {
    $VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path $VenvPython) {
        $PythonExe = $VenvPython
    } else {
        $PythonExe = "python"
    }
}

$ScriptPath = Join-Path $RepoRoot "data_provider\02_ws_live.py"
$LogDir = Join-Path $RepoRoot "data_provider\logs"
$SupervisorLog = Join-Path $LogDir "ws_live_supervisor.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

while ($true) {
    $StartedAt = Get-Date
    Add-Content -Path $SupervisorLog -Value ("[{0}] START ws_live ({1})" -f $StartedAt.ToString("yyyy-MM-dd HH:mm:ss"), $PythonExe)

    & $PythonExe $ScriptPath
    $ExitCode = $LASTEXITCODE

    $EndedAt = Get-Date
    Add-Content -Path $SupervisorLog -Value ("[{0}] EXIT code={1}; restart in {2}s" -f $EndedAt.ToString("yyyy-MM-dd HH:mm:ss"), $ExitCode, $RestartDelaySec)

    Start-Sleep -Seconds $RestartDelaySec
}
