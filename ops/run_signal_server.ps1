param(
    [int]$RestartDelaySec = 15,
    [string]$PythonExe    = "",
    [int]$Port            = 5050,
    [int]$TtlMinutes      = 120
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if (-not $PythonExe) {
    $VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    $PythonExe  = if (Test-Path $VenvPython) { $VenvPython } else { "python" }
}

$LogDir       = Join-Path $RepoRoot "core_python\runtime\logs"
$SupervisorLog = Join-Path $LogDir "signal_server_supervisor.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

while ($true) {
    $StartedAt = Get-Date
    Add-Content -Path $SupervisorLog -Value (
        "[{0}] START signal_server port={1} ttl={2}min ({3})" -f
        $StartedAt.ToString("yyyy-MM-dd HH:mm:ss"), $Port, $TtlMinutes, $PythonExe
    )

    & $PythonExe -m core_python.execution.signal_server `
        --host 127.0.0.1 `
        --port $Port `
        --ttl-minutes $TtlMinutes

    $ExitCode  = $LASTEXITCODE
    $EndedAt   = Get-Date
    Add-Content -Path $SupervisorLog -Value (
        "[{0}] EXIT code={1}; restart in {2}s" -f
        $EndedAt.ToString("yyyy-MM-dd HH:mm:ss"), $ExitCode, $RestartDelaySec
    )

    Start-Sleep -Seconds $RestartDelaySec
}
