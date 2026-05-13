$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$HostName = "127.0.0.1"
$Port = 8516
$Url = "http://${HostName}:$Port/"
$ConfigUrl = "$($Url.TrimEnd('/'))/api/config"

$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = "python"
}

$env:PYTHONDONTWRITEBYTECODE = "1"

$RuntimeDir = Join-Path $ScriptDir "runtime"
New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
$OutLog = Join-Path $RuntimeDir "chart_server.out.log"
$ErrLog = Join-Path $RuntimeDir "chart_server.err.log"

function Test-ChartServer {
    try {
        $home = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2
        $config = Invoke-WebRequest -UseBasicParsing -Uri $ConfigUrl -TimeoutSec 2
        return ($home.StatusCode -eq 200 -and $config.StatusCode -eq 200)
    }
    catch {
        return $false
    }
}

if (-not (Test-ChartServer)) {
    Start-Process `
        -FilePath $Python `
        -ArgumentList @("-m", "core_python.main", "--host", $HostName, "--port", [string]$Port) `
        -WorkingDirectory $RepoRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $OutLog `
        -RedirectStandardError $ErrLog

    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-ChartServer) {
            break
        }
    }
}

Start-Process $Url
