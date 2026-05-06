param(
    [int]$RestartDelaySec = 10,
    [string]$PythonExe = "",
    [string]$WatcherArgs = ""
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

$LogDir = Join-Path $RepoRoot "logs"
$WatcherLog = Join-Path $LogDir "watcher.log"
$SupervisorLog = Join-Path $LogDir "watcher_supervisor.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

Set-Location $RepoRoot
$env:PYTHONUNBUFFERED = "1"

$BaseArgs = @(
    "-u",
    "-m",
    "core_python.notify.signal_watcher",
    "--log-file",
    $WatcherLog
)

while ($true) {
    $StartedAt = Get-Date
    Add-Content -Path $SupervisorLog -Value (
        "[{0}] START signal_watcher ({1})" -f $StartedAt.ToString("yyyy-MM-dd HH:mm:ss"), $PythonExe
    )

    if ($WatcherArgs) {
        & $PythonExe @BaseArgs $WatcherArgs
    } else {
        & $PythonExe @BaseArgs
    }

    $ExitCode = $LASTEXITCODE
    $EndedAt = Get-Date
    Add-Content -Path $SupervisorLog -Value (
        "[{0}] EXIT code={1}; restart in {2}s" -f $EndedAt.ToString("yyyy-MM-dd HH:mm:ss"), $ExitCode, $RestartDelaySec
    )

    Start-Sleep -Seconds $RestartDelaySec
}
