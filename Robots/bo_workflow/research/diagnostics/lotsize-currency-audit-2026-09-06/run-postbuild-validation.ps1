$ErrorActionPreference = 'Continue'

. "$PSScriptRoot\..\..\cli_pipeline\Invoke-CliBacktest.ps1"

$root = (Resolve-Path "$PSScriptRoot\..\..\..").Path
$cases = @(
    @{
        Name = 'Combo-JP225'; Algo = Join-Path $root 'Combo.algo'; Symbol = 'JP225.cash'; Period = 'h1'
        Signal = 'Z:\Desktop\og_program\runtime\exports\combo_J225_H1_full_history_signals.csv'
    },
    @{
        Name = 'Combo-HK50'; Algo = Join-Path $root 'Combo.algo'; Symbol = 'HK50.cash'; Period = 'h1'
        Signal = 'Z:\Desktop\og_program\runtime\exports\combo_HK50_H1_full_history_signals.csv'
    },
    @{
        Name = 'Combo-US30'; Algo = Join-Path $root 'Combo.algo'; Symbol = 'US30.cash'; Period = 'h1'
        Signal = 'Z:\Desktop\og_program\runtime\exports\combo_US30_H1_full_history_signals.csv'
    },
    @{
        Name = 'MA-Cross-JP225'; Algo = Join-Path $root 'MA Cross.algo'; Symbol = 'JP225.cash'; Period = 'm45'
        Signal = 'Z:\Desktop\og_program\runtime\exports\ma_cross_J225_M45_full_history_signals.csv'
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
        NetProfit = $run.Metrics.NetProfit
        TotalTrades = $run.Metrics.TotalTrades
        HistoryItems = $run.Metrics.HistoryItems
    }
}

$outputPath = Join-Path $PSScriptRoot 'postbuild-validation-runs.json'
$results | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $outputPath -Encoding UTF8
$results | Format-Table -AutoSize
Write-Host "Saved $outputPath"
