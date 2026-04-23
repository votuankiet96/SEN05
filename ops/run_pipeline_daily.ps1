param(
    [ValidateSet("auto", "gap", "full")]
    [string]$Mode = "auto",
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

$ScriptPath = Join-Path $RepoRoot "data_provider\01_data_pipeline.py"
$LogDir = Join-Path $RepoRoot "data_provider\logs"
$SchedulerLog = Join-Path $LogDir "pipeline_scheduler.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$StartedAt = Get-Date
Add-Content -Path $SchedulerLog -Value ("[{0}] START pipeline mode={1}" -f $StartedAt.ToString("yyyy-MM-dd HH:mm:ss"), $Mode)

& $PythonExe $ScriptPath --mode $Mode
$ExitCode = $LASTEXITCODE

$EndedAt = Get-Date
Add-Content -Path $SchedulerLog -Value ("[{0}] EXIT code={1}" -f $EndedAt.ToString("yyyy-MM-dd HH:mm:ss"), $ExitCode)

exit $ExitCode
