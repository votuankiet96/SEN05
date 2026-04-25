param(
    [string]$Symbol = "",
    [int]$Bars = 300,
    [int]$Port = 8513,
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

$ChartPath = Join-Path $RepoRoot "core_python\strategies\combo\combo_chart.py"

$PythonPathParts = @($RepoRoot, (Join-Path $RepoRoot "core_python"))
if ($env:PYTHONPATH) {
    $PythonPathParts += $env:PYTHONPATH
}
$env:PYTHONPATH = ($PythonPathParts -join ";")

$Args = @($ChartPath, "--port", $Port, "--default-bars", $Bars)
if ($Symbol) {
    $Args += @("--default-symbol", $Symbol)
}

Write-Host "Combo chart"
Write-Host "Repo   : $RepoRoot"
Write-Host "Python : $PythonExe"
Write-Host "Script : $ChartPath"
Write-Host "URL    : http://127.0.0.1:$Port"

Push-Location $RepoRoot
try {
    & $PythonExe @Args
    $ExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}

exit $ExitCode
