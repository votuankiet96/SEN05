Set-StrictMode -Version Latest

function Get-Sen05RepoRoot {
    $moduleDir = Split-Path -Parent $PSCommandPath
    return (Resolve-Path (Join-Path $moduleDir "..\..")).Path
}

function Get-Sen05Python {
    param(
        [string]$PythonExe = ""
    )

    if ($PythonExe) {
        return $PythonExe
    }

    $repoRoot = Get-Sen05RepoRoot
    $venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        return $venvPython
    }

    return "python"
}

function Get-Sen05DataProviderCommandMap {
    $repoRoot = Get-Sen05RepoRoot
    return [ordered]@{
        Pipeline = Join-Path $repoRoot "data_provider\01_data_pipeline.py"
        WsLive   = Join-Path $repoRoot "data_provider\02_ws_live.py"
        Checker  = Join-Path $repoRoot "data_provider\04_checker.py"
        Chart    = Join-Path $repoRoot "data_provider\03_chart.py"
        Probe    = Join-Path $repoRoot "data_provider\probe_ws_history_depth.py"
    }
}

function Get-Sen05OpsPaths {
    $repoRoot = Get-Sen05RepoRoot
    [pscustomobject]@{
        RepoRoot         = $repoRoot
        DataProviderRoot = Join-Path $repoRoot "data_provider"
        DataProviderLogs = Join-Path $repoRoot "data_provider\logs"
        OpsRoot          = Join-Path $repoRoot "ops"
        OpsRuntime       = Join-Path $repoRoot "ops\runtime"
        Commands         = Get-Sen05DataProviderCommandMap
    }
}

function Confirm-Sen05Action {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message,
        [switch]$Yes
    )

    if ($Yes) {
        return $true
    }

    Write-Host ""
    Write-Host $Message -ForegroundColor Yellow
    $answer = Read-Host "Type YES to continue"
    return ($answer -ceq "YES")
}

function Invoke-Sen05PythonScript {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("Pipeline", "WsLive", "Checker", "Chart", "Probe")]
        [string]$ScriptKey,
        [string[]]$Arguments = @(),
        [string]$PythonExe = ""
    )

    $paths = Get-Sen05OpsPaths
    $scriptPath = $paths.Commands[$ScriptKey]
    if (-not (Test-Path -LiteralPath $scriptPath)) {
        throw "Data provider command '$ScriptKey' not found: $scriptPath"
    }

    $python = Get-Sen05Python -PythonExe $PythonExe
    $env:PYTHONUNBUFFERED = "1"

    Write-Host ""
    Write-Host ("Repo   : {0}" -f $paths.RepoRoot)
    Write-Host ("Python : {0}" -f $python)
    Write-Host ("Script : {0}" -f $scriptPath)
    if ($Arguments.Count -gt 0) {
        Write-Host ("Args   : {0}" -f ($Arguments -join " "))
    }
    Write-Host ""

    Push-Location $paths.RepoRoot
    try {
        & $python $scriptPath @Arguments
        if ($null -eq $LASTEXITCODE) {
            return 0
        }
        return $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
}

function Invoke-Sen05Pipeline {
    param(
        [ValidateSet("auto", "gap", "full")]
        [string]$Mode = "auto",
        [switch]$DryRun,
        [string]$Symbols = "",
        [string]$Timeframes = "",
        [string]$AssetType = "",
        [switch]$Reset,
        [switch]$ForceUnlock,
        [string]$PythonExe = ""
    )

    $args = @("--mode", $Mode)
    if ($DryRun) { $args += "--dry-run" }
    if ($Symbols) { $args += @("--symbols", $Symbols) }
    if ($Timeframes) { $args += @("--timeframes", $Timeframes) }
    if ($AssetType) { $args += @("--asset-type", $AssetType) }
    if ($Reset) { $args += "--reset" }
    if ($ForceUnlock) { $args += "--force-unlock" }

    return Invoke-Sen05PythonScript -ScriptKey Pipeline -Arguments $args -PythonExe $PythonExe
}

function Invoke-Sen05Checker {
    param(
        [switch]$DryRun,
        [string]$Symbol = "",
        [string]$Timeframe = "",
        [double]$Threshold = 0,
        [switch]$CoCheck,
        [int]$CoDays = 7,
        [switch]$TfCheck,
        [switch]$TfCheckFull,
        [switch]$RebuildComputed,
        [switch]$ManualConfirm,
        [string]$PythonExe = ""
    )

    $args = @()
    if ($DryRun) { $args += "--dry-run" }
    if ($Symbol) { $args += @("--sym", $Symbol) }
    if ($Timeframe) { $args += @("--tf", $Timeframe) }
    if ($Threshold -gt 0) { $args += @("--threshold", ([string]$Threshold)) }
    if ($CoCheck) { $args += @("--co-check", "--co-days", ([string]$CoDays)) }
    if ($TfCheck) { $args += "--tf-check" }
    if ($TfCheckFull) { $args += "--tf-check-full" }
    if ($RebuildComputed) { $args += "--rebuild-computed" }
    if ($ManualConfirm) { $args += "--manual-confirm" }

    return Invoke-Sen05PythonScript -ScriptKey Checker -Arguments $args -PythonExe $PythonExe
}

function Invoke-Sen05Probe {
    param(
        [string]$Symbol = "GOLD",
        [string]$Timeframes = "",
        [string]$Sizes = "",
        [double]$TimeoutSec = 0,
        [int]$MoreRounds = -1,
        [int]$MoreBars = -1,
        [string]$PythonExe = ""
    )

    $args = @("--symbol", $Symbol)
    if ($Timeframes) { $args += @("--timeframes", $Timeframes) }
    if ($Sizes) { $args += @("--sizes", $Sizes) }
    if ($TimeoutSec -gt 0) { $args += @("--timeout", ([string]$TimeoutSec)) }
    if ($MoreRounds -ge 0) { $args += @("--more-rounds", ([string]$MoreRounds)) }
    if ($MoreBars -gt 0) { $args += @("--more-bars", ([string]$MoreBars)) }

    return Invoke-Sen05PythonScript -ScriptKey Probe -Arguments $args -PythonExe $PythonExe
}

function Test-Sen05HttpEndpoint {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Url,
        [int]$TimeoutSec = 2
    )

    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec $TimeoutSec
        return ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500)
    }
    catch {
        return $false
    }
}

function Start-Sen05DataChart {
    param(
        [switch]$Foreground,
        [switch]$NoBrowser,
        [string]$PythonExe = ""
    )

    $paths = Get-Sen05OpsPaths
    $scriptPath = $paths.Commands["Chart"]
    if (-not (Test-Path -LiteralPath $scriptPath)) {
        throw "Data chart script not found: $scriptPath"
    }

    if ($Foreground) {
        return Invoke-Sen05PythonScript -ScriptKey Chart -Arguments @() -PythonExe $PythonExe
    }

    $url = "http://127.0.0.1:8050/"
    $configUrl = "$($url.TrimEnd('/'))/api/symbols"

    if (-not (Test-Sen05HttpEndpoint -Url $configUrl)) {
        $python = Get-Sen05Python -PythonExe $PythonExe
        New-Item -ItemType Directory -Force -Path $paths.DataProviderLogs | Out-Null
        $outLog = Join-Path $paths.DataProviderLogs "data_chart.out.log"
        $errLog = Join-Path $paths.DataProviderLogs "data_chart.err.log"

        Start-Process `
            -FilePath $python `
            -ArgumentList @($scriptPath) `
            -WorkingDirectory $paths.RepoRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput $outLog `
            -RedirectStandardError $errLog

        for ($i = 0; $i -lt 30; $i++) {
            Start-Sleep -Milliseconds 500
            if (Test-Sen05HttpEndpoint -Url $configUrl) {
                break
            }
        }
    }

    $isReady = Test-Sen05HttpEndpoint -Url $configUrl
    if ($isReady -and -not $NoBrowser) {
        Start-Process $url
    }

    Write-Host ""
    Write-Host ("Data chart URL: {0}" -f $url)
    Write-Host ("Ready         : {0}" -f $isReady)
    return $(if ($isReady) { 0 } else { 1 })
}

function Show-Sen05LogSummary {
    param(
        [int]$Last = 12
    )

    $paths = Get-Sen05OpsPaths
    $dirs = @($paths.DataProviderLogs, $paths.OpsRuntime)
    foreach ($dir in $dirs) {
        Write-Host ""
        Write-Host ("Logs: {0}" -f $dir) -ForegroundColor Cyan
        if (-not (Test-Path -LiteralPath $dir)) {
            Write-Host "  (missing)"
            continue
        }
        Get-ChildItem -LiteralPath $dir -File -Force |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First $Last Name, Length, LastWriteTime |
            Format-Table -AutoSize
    }
}

function Show-Sen05ProcessStatus {
    $patterns = @(
        "data_provider\01_data_pipeline.py",
        "data_provider\02_ws_live.py",
        "data_provider\03_chart.py",
        "data_provider\04_checker.py",
        "probe_ws_history_depth.py"
    )

    Write-Host ""
    Write-Host "Matching Python processes" -ForegroundColor Cyan
    try {
        $processes = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
            Where-Object {
                $cmd = $_.CommandLine
                $patterns | Where-Object { $cmd -like "*$_*" }
            } |
            Select-Object ProcessId, ParentProcessId, CommandLine

        if (@($processes).Count -eq 0) {
            Write-Host "  (none)"
        }
        else {
            $processes | Format-List
        }
    }
    catch {
        Write-Host ("  status unavailable: {0}" -f $_.Exception.Message)
    }
}

Export-ModuleMember -Function `
    Get-Sen05RepoRoot, `
    Get-Sen05Python, `
    Get-Sen05DataProviderCommandMap, `
    Get-Sen05OpsPaths, `
    Confirm-Sen05Action, `
    Invoke-Sen05PythonScript, `
    Invoke-Sen05Pipeline, `
    Invoke-Sen05Checker, `
    Invoke-Sen05Probe, `
    Start-Sen05DataChart, `
    Show-Sen05LogSummary, `
    Show-Sen05ProcessStatus
