param(
    [switch]$DryRun,
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

$ScriptPath = Join-Path $RepoRoot "data_provider\04_checker.py"
$LogDir = Join-Path $RepoRoot "data_provider\logs"
$SchedulerLog = Join-Path $LogDir "checker_scheduler.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Args = @($ScriptPath)
if ($DryRun) {
    $Args += "--dry-run"
}

$StartedAt = Get-Date
Add-Content -Path $SchedulerLog -Value ("[{0}] START checker dry_run={1}" -f $StartedAt.ToString("yyyy-MM-dd HH:mm:ss"), [bool]$DryRun)

& $PythonExe @Args
$ExitCode = $LASTEXITCODE

$EndedAt = Get-Date
Add-Content -Path $SchedulerLog -Value ("[{0}] EXIT code={1}" -f $EndedAt.ToString("yyyy-MM-dd HH:mm:ss"), $ExitCode)

exit $ExitCode
