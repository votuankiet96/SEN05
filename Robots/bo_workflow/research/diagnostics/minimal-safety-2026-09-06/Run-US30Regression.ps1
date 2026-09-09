param([switch]$UseOldBinary)
$ErrorActionPreference = 'Stop'
$repo = 'C:\Users\Administrator\Documents\cAlgo\Sources\Robots'
$template = Get-Content -LiteralPath (Join-Path $repo 'research\cli_runs\Combo_XAUUSD_h1_ticks_2026Jan01-07_20260905-170125\run.ps1') -Raw -Encoding UTF8
$cases = @(
    @{ Bot='MA Cross'; Tag='MACross'; Period='m30'; End='2026-02-01'; Signal='ma_cross_US30_M30_full_history_signals.csv' },
    @{ Bot='Combo'; Tag='Combo'; Period='h1'; End='2026-06-01'; Signal='combo_US30_H1_full_history_signals.csv' }
)
$results = @()
foreach ($case in $cases) {
    Write-Output ('START ' + $case.Bot + ' US30.cash ' + $case.Period + ' 2026-01-01 -> ' + $case.End)
    $phase = if ($UseOldBinary) { 'control_before' } else { 'defaults_safety' }
    $runDir = Join-Path $repo ('research\cli_runs\'+$case.Tag+'_US30.cash_'+$case.Period+'_'+$phase+'_'+(Get-Date -Format 'yyyyMMdd-HHmmss'))
    New-Item -ItemType Directory -Path $runDir | Out-Null
    @{ Parameters=@{ SignalFilePath=('Z:\Desktop\og_program\runtime\exports\'+$case.Signal) } } |
        ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $runDir 'params.cbotset') -Encoding UTF8
    $endCli = ([datetime]$case.End).ToString('dd/MM/yyyy')
    $algoRelative = if ($UseOldBinary) { 'research\diagnostics\minimal-safety-2026-09-06\before\'+$case.Bot+'.algo' } else { $case.Bot+'.algo' }
    $script = $template.Replace('Robots\Combo.algo',('Robots\'+$algoRelative)).
        Replace('--symbol=XAUUSD','--symbol=US30.cash').Replace('--period=h1',('--period='+$case.Period)).
        Replace('--end=07/01/2026 23:59',('--end='+$endCli)).
        Replace("RequestedEndUTC='2026-01-07 23:59:00'",("RequestedEndUTC='"+$case.End+" 00:00:00'")).
        Replace('TotalMinutes -ge 10','TotalMinutes -ge 20').Replace('Start-Sleep -Seconds 10','Start-Sleep -Seconds 5')
    $script | Set-Content -LiteralPath (Join-Path $runDir 'run.ps1') -Encoding UTF8
    $results += [pscustomobject]@{Bot=$case.Bot; OutputDir=$runDir}
    $manifest = if ($UseOldBinary) { 'regression-controls.json' } else { 'regression-runs.json' }
    $results | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $PSScriptRoot $manifest) -Encoding UTF8
    & (Join-Path $runDir 'run.ps1')
    $summary = Get-Content -LiteralPath (Join-Path $runDir 'run-summary.json') -Raw | ConvertFrom-Json
    if (-not $summary.HasReport -or $summary.TimedOut) { throw ('Backtest failed: '+$runDir) }
    Write-Output ('DONE '+$case.Bot+': '+$runDir)
}
