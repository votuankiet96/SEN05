param()
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
$checks = [System.Collections.Generic.List[object]]::new()
function Record($name, $pass, $detail) {
    $checks.Add([pscustomobject]@{name=$name;pass=[bool]$pass;detail=$detail})
    if (!$pass) { throw "Check failed: $name" }
}
$combo = Get-Content -Raw -Encoding UTF8 (Join-Path $root 'Combo/Combo/Combo.cs')
$mac = Get-Content -Raw -Encoding UTF8 (Join-Path $root 'MA Cross/MA Cross/MA Cross.cs')
function SizingRegion($source) {
    $region = [regex]::Match($source, '(?s)#region Risk & Position Sizing(.*?)#endregion').Groups[1].Value
    $region = [regex]::Replace($region, '//[^\r\n]*', '')
    $region = [regex]::Replace(($region -replace 'MA Cross:', 'Combo:'), '\s+', '')
    return $region.Replace('"+"', '')
}
Record 'Sizing regions identical after removing comments/whitespace/log prefix' ((SizingRegion $combo) -ceq (SizingRegion $mac)) 'Direct comparison of complete regions, not report summaries.'
$apiPath = 'C:\Users\Administrator\Documents\cAlgo\API\cAlgo.API.dll'
$api = [Reflection.Assembly]::LoadFrom($apiPath)
$symbolType = $api.GetType('cAlgo.API.Internals.Symbol', $true)
$marginMethod = $symbolType.GetMethod('GetEstimatedMargin')
Record 'Installed API margin return is nonnullable System.Double' ($marginMethod.ReturnType -eq [double]) $marginMethod.ToString()
$credit = $api.GetType('cAlgo.API.Internals.IAccount', $true).GetProperty('Credit')
Record 'Installed API has Account.Credit double' ($credit.PropertyType -eq [double]) $credit.ToString()
$normalize = $symbolType.GetMethod('NormalizeVolumeInUnits')
Record 'Reference assembly cannot execute normalization' $normalize.IsAbstract 'Abstract contract; this script makes NO claim to exercise broker normalization.'
$nan = [double]::NaN
$parsed = 0.0
$parseOk = [double]::TryParse('NaN', [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$parsed)
Record 'NaN parses and bypasses <=0 guard' ($parseOk -and [double]::IsNaN($parsed) -and !($nan -le 0)) 'Actual .NET TryParse/comparison; downstream API behavior not simulated.'
Record 'Infinite pip value passes <=0 guard' (!([double]::PositiveInfinity -le 0)) 'Must check all finite positive inputs, not only zero.'
$restart = @(
    [pscustomobject]@{balance=10800;botFloor=10800*.9;contractFloor=9000;effect='tighter'},
    [pscustomobject]@{balance=9200;botFloor=9200*.9;contractFloor=9000;effect='looser'}
)
Record 'B7 profit restart is tighter, loss restart looser' ($restart[0].botFloor -gt 9000 -and $restart[1].botFloor -lt 9000) $restart
function TierMargin([double]$v) { return [math]::Min($v,100)*1 + [math]::Max(0,$v-100)*2 }
$original=200.0; $budget=150.0
$scaled=$original*$budget/(TierMargin $original)*.98
Record 'Progressive-tier scaling is conservative in fixed-state example' ((TierMargin $scaled) -le $budget) @{original=$original;margin=(TierMargin $original);scaled=$scaled;scaledMargin=(TierMargin $scaled);budget=$budget}
Record 'D1 example 25pct gap allowance exceeds both FTMO total-loss budgets' (25 -gt 10) 'Risk preference example is unsuitable as FTMO protection default.'
Record 'Separate stress knob cannot preserve nominal risk when stress cap binds' ((10000*.01/(100*.10)) -lt (10000*.01/1)) @{price=100;stopDistance=1;gap=.10;stopRiskVolume=100;stressRiskVolume=10}
Record 'Clamping normalized quantity upward may violate cap' ((1*100) -gt (.6*100)) @{capVolume=.6;brokerMin=1;unitLoss=100;capLoss=60;clampedLoss=100}
Record 'Two 50pct per-order caps are not 50pct portfolio cap' ((5000+4900) -gt 10000*.5) 'Second order can consume almost all remaining margin.'
# The following are deliberately tiny state-machine models, NOT real broker failure injection.
$oldOpen=$true; $closeSucceeded=$false; $newOrderSent=$false
if ($oldOpen) { if($closeSucceeded){$oldOpen=$false}; $newOrderSent=$true }
Record 'Model: ignored Close failure permits opposing order request' ($oldOpen -and $newOrderSent) 'Mirrors unconditional return true in both reconciliation functions.'
$oldOpen=$true; $closeSucceeded=$true; $newAccepted=$false
if($closeSucceeded){$oldOpen=$false}
Record 'Model: successful close then rejected entry leaves flat exposure' (!$oldOpen -and !$newAccepted) 'No rollback; signal is already IsHandled.'
$halted=$false; $closeAttempts=0
1..3 | ForEach-Object { if(!$halted){$halted=$true; $closeAttempts++} }
Record 'Model: latched PM does not retry failed close' ($closeAttempts -eq 1) 'Three checks; actual ForceClose return ignored.'
$tz=[TimeZoneInfo]::FindSystemTimeZoneById('Central Europe Standard Time')
$dates=@('2026-01-01T23:00:00Z','2026-07-01T22:00:00Z','2026-03-29T00:59:59Z','2026-03-29T01:00:00Z','2026-10-25T00:59:59Z','2026-10-25T01:00:00Z')
$dst=foreach($d in $dates){$utc=[datetime]::Parse($d,[Globalization.CultureInfo]::InvariantCulture,[Globalization.DateTimeStyles]::RoundtripKind); [pscustomobject]@{utc=$utc.ToString('o');prague=[TimeZoneInfo]::ConvertTimeFromUtc($utc,$tz).ToString('yyyy-MM-dd HH:mm:ss');offset=$tz.GetUtcOffset($utc).TotalHours}}
Record 'Windows Prague-compatible timezone applies 2026 DST transitions' ($dst[2].offset -eq 1 -and $dst[3].offset -eq 2 -and $dst[4].offset -eq 2 -and $dst[5].offset -eq 1) $dst
$run = Join-Path $root 'research/cli_runs/Combo_XAUUSD_h1_ticks_2026Jan01-07_20260905-170125'
$report=Get-Content -Raw -Encoding UTF8 (Join-Path $run 'report.json') | ConvertFrom-Json
$trace=Import-Csv (Join-Path $run 'signal-trace.csv')
$events=Get-Content -Raw -Encoding UTF8 (Join-Path $run 'events.json') | ConvertFrom-Json
$trade=$report.history.items | Where-Object entryPrice -eq 4345.73
$signal=$trace | Where-Object RawEntry -eq '4347.52'
$nominal=[double]$signal.RawATR*$trade.volume
$entrySlip=([double]$signal.RawEntry-$trade.entryPrice)*$trade.volume
$exitSlip=($trade.closePrice-[double]$signal.ActualSL)*$trade.volume
$cost=-$trade.swaps-$trade.commissions
$netLoss=$nominal+$entrySlip+$exitSlip+$cost
Record 'Raw gold net loss decomposes into nominal SL + entry slip + exit slip + cost' ([math]::Abs($netLoss+$trade.net) -lt .000001) @{nominal=$nominal;entrySlippage=$entrySlip;exitSlippage=$exitSlip;cost=$cost;netLoss=$netLoss;target=9999.76*.01;overTargetPercent=100*($netLoss/(9999.76*.01)-1);entrySlipPercentOfPrice=100*1.79/4347.52;entrySlipPercentOfSL=100*1.79/[double]$signal.RawATR}
Record 'Raw fill event corroborates CSV trace' (@($events | Where-Object {$_.event -eq 'Stop Order Filled' -and $_.orderId -eq 5}).Count -eq 1) 'Order 5, raw native event plus independent report.history entry price.'
Record 'Gold gross P/L confirms one account dollar per price point per volume unit' ([math]::Abs(-$trade.gross/(($trade.closePrice-$trade.entryPrice)*$trade.volume)-1) -lt .000001) @{deposit=$report.main.depositAsset;lotSize=$report.usedSymbols.lotSize;units=$trade.volume;lots=$trade.quantity}
Record 'Gold backtest disabled automatic commissions' (!$report.main.commissions.applyCommissionAutomatically -and $report.main.commissions.value -eq 0) $report.main.commissions
$marginLog=Join-Path $root 'research/cli_batches/Combo_leverage_crosscheck_2025H1/BTCUSD/log.txt'
$observations=foreach($line in Get-Content -Encoding UTF8 $marginLog){if($line -match 'Signal wanted ([0-9.]+) units, which needs about \$([0-9.]+) margin'){[pscustomobject]@{wanted=[double]$matches[1];estimate=[double]$matches[2];marginPerUnit=[double]$matches[2]/[double]$matches[1]}}}
$stats=$observations | Measure-Object marginPerUnit -Minimum -Maximum -Average
Record 'BTC historical estimator samples exist for independent confound check' ($observations.Count -gt 300) @{count=$observations.Count;min=$stats.Minimum;max=$stats.Maximum;mean=$stats.Average;firstFive=@($observations | Select-Object -First 5)}
$hashes = @('Combo/Combo/Combo.cs','MA Cross/MA Cross/MA Cross.cs','Combo.algo','MA Cross.algo') | ForEach-Object {Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $root $_)}
$output=[ordered]@{utc=[datetime]::UtcNow.ToString('o');description='Read-only source/assembly/report audit plus explicitly labelled arithmetic and state-machine models. No cBot compilation, broker order, or new backtest.';checks=$checks;hashes=$hashes;apiHash=(Get-FileHash -Algorithm SHA256 $apiPath);passCount=$checks.Count}
$output | ConvertTo-Json -Depth 10 | Set-Content -Encoding UTF8 (Join-Path $PSScriptRoot 'verification.json')
Write-Output "$($checks.Count) evidence checks passed; verification.json written."
