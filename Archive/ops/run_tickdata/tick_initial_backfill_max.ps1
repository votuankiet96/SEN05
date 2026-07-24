param(
    [string]$RepoRoot = "",
    [string]$PythonExe = "",
    [int]$MaxDays = 20000,
    [string[]]$Symbols = @(),
    [string]$To = "",
    [int]$ProbeWindowDays = 7,
    [int]$ProbeTimeoutSeconds = 1800,
    [int]$RequestTimeoutSeconds = 60,
    [switch]$SkipSymbolSync,
    [switch]$Apply
)

Set-StrictMode -Version Latest

$modulePath = Join-Path $PSScriptRoot "lib\TickDataOps.psm1"
Import-Module $modulePath -Force

$paths = Get-TickDataPaths -RepoRoot $RepoRoot
Initialize-TickDataRuntime -Paths $paths
$python = Get-TickDataPython -RepoRoot $paths.RepoRoot -PythonExe $PythonExe
$logPath = $paths.BackfillLog

function Invoke-TickCli {
    param([string[]]$ArgsList)

    Push-Location $paths.RepoRoot
    try {
        $output = & $python -m $paths.TickAppModule @ArgsList
        $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
        foreach ($line in $output) {
            Write-Host $line
        }
        return $exitCode
    }
    finally {
        Pop-Location
    }
}

function Invoke-TickCliCapture {
    param([string[]]$ArgsList)

    Push-Location $paths.RepoRoot
    try {
        $output = & $python -m $paths.TickAppModule @ArgsList
        $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { $LASTEXITCODE }
        return [pscustomobject]@{ ExitCode = $exitCode; Output = ($output -join "`n") }
    }
    finally {
        Pop-Location
    }
}

Write-TickDataOpsLog -LogPath $logPath -Message "initial max backfill requested apply=$Apply max_days=$MaxDays probe_timeout=${ProbeTimeoutSeconds}s request_timeout=${RequestTimeoutSeconds}s symbols=$($Symbols -join ',')"

if (-not $SkipSymbolSync) {
    Write-Host "Running symbol-sync --apply..." -ForegroundColor Cyan
    $code = Invoke-TickCli -ArgsList @("symbol-sync", "--apply")
    if ($code -ne 0) {
        throw "symbol-sync failed with exit code $code"
    }
}

$probeArgs = @(
    "history-depth",
    "--json",
    "--max-days", [string]$MaxDays,
    "--probe-window-days", [string]$ProbeWindowDays,
    "--timeout", [string]$ProbeTimeoutSeconds,
    "--request-timeout", [string]$RequestTimeoutSeconds
)
if ($To) {
    $probeArgs += @("--to", $To)
}
if ($Symbols.Count -gt 0) {
    $probeArgs += "--symbols"
    $probeArgs += $Symbols
}

Write-Host "Probing deepest available tick history..." -ForegroundColor Cyan
$probe = Invoke-TickCliCapture -ArgsList $probeArgs
if ($probe.ExitCode -ne 0) {
    throw "history-depth failed with exit code $($probe.ExitCode)"
}

$results = $probe.Output | ConvertFrom-Json
$toValue = if ($To) { $To } else { (Get-Date).ToUniversalTime().ToString("o") }

$plan = @()
foreach ($item in $results) {
    if ($null -eq $item.earliest_probe_from_utc -or -not $item.deepest_available_days) {
        $plan += [pscustomobject]@{
            Symbol = $item.symbol
            Action = "SKIP"
            Reason = "No historical ticks found in probe window"
            FromUtc = $null
            ToUtc = $toValue
            DeepestDays = $null
        }
        continue
    }
    $plan += [pscustomobject]@{
        Symbol = $item.symbol
        Action = if ($Apply) { "BACKFILL" } else { "PLAN_ONLY" }
        Reason = "Deepest probe with ticks"
        FromUtc = $item.earliest_probe_from_utc
        ToUtc = $toValue
        DeepestDays = $item.deepest_available_days
    }
}

Write-Host ""
Write-Host "== BACKFILL PLAN ==" -ForegroundColor Cyan
$plan | Format-Table -AutoSize

$planJson = $plan | ConvertTo-Json -Depth 5
Add-Content -LiteralPath $logPath -Value $planJson

if (-not $Apply) {
    Write-Host ""
    Write-Host "Dry run only. Re-run with -Apply to execute this plan." -ForegroundColor Yellow
    exit 0
}

foreach ($row in $plan) {
    if ($row.Action -ne "BACKFILL") {
        continue
    }
    Write-Host ""
    Write-Host "Backfilling $($row.Symbol) from $($row.FromUtc) to $($row.ToUtc)..." -ForegroundColor Cyan
    Write-TickDataOpsLog -LogPath $logPath -Message "backfill start symbol=$($row.Symbol) from=$($row.FromUtc) to=$($row.ToUtc)"
    Push-Location $paths.RepoRoot
    try {
        & $python -m $paths.TickAppModule backfill --from $row.FromUtc --to $row.ToUtc --request-timeout $RequestTimeoutSeconds --symbols $row.Symbol *>> $logPath
        $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { $LASTEXITCODE }
    }
    finally {
        Pop-Location
    }
    if ($exitCode -ne 0) {
        Write-TickDataOpsLog -LogPath $logPath -Level "ERROR" -Message "backfill failed symbol=$($row.Symbol) exit_code=$exitCode"
        throw "backfill failed for $($row.Symbol) with exit code $exitCode"
    }
    Write-TickDataOpsLog -LogPath $logPath -Message "backfill done symbol=$($row.Symbol)"
}

Write-Host ""
Write-Host "Initial max backfill completed." -ForegroundColor Cyan
