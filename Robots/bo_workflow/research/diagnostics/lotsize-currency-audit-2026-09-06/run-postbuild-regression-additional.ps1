$ErrorActionPreference = 'Continue'

. "$PSScriptRoot\..\..\cli_pipeline\Invoke-CliBacktest.ps1"

$root = (Resolve-Path "$PSScriptRoot\..\..\..").Path
$cases = @(
    @{
        Name = 'Combo-GER40'; Algo = Join-Path $root 'Combo.algo'; Symbol = 'GER40.cash'; Period = 'h1'
        Signal = 'Z:\Desktop\og_program\runtime\exports\combo_DE40_H1_full_history_signals.csv'
    },
    @{
        Name = 'Combo-XAUUSD'; Algo = Join-Path $root 'Combo.algo'; Symbol = 'XAUUSD'; Period = 'h1'
        Signal = 'Z:\Desktop\og_program\runtime\exports\combo_GOLD_H1_full_history_signals.csv'
    },
    @{
        Name = 'MA-Cross-HK50'; Algo = Join-Path $root 'MA Cross.algo'; Symbol = 'HK50.cash'; Period = 'm45'
        Signal = 'Z:\Desktop\og_program\runtime\exports\ma_cross_HK50_M45_full_history_signals.csv'
    }
)

$results = foreach ($case in $cases) {
    Write-Host "Starting $($case.Name)..."
    $run = Invoke-CliBacktest `
        -AlgoPath $case.Algo `
        -Symbol $case.Symbol `
        -Period $case.Period `
        -StartDate '2025-01-01' `
        -EndDate '2025-01-08' `
        -DataMode Ticks `
        -Params @{ SignalFilePath = $case.Signal } `
        -TimeoutMinutes 20 `
        -PollSeconds 2

    [pscustomobject]@{
        Case = $case.Name
        Success = $run.Success
        FailureReason = $run.FailureReason
        RunDirectory = Split-Path -Parent $run.RunSummaryPath
    }
}

$outputPath = Join-Path $PSScriptRoot 'postbuild-regression-additional-runs.json'
$results | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $outputPath -Encoding UTF8
$results | Format-Table -AutoSize
Write-Host "Saved $outputPath"
