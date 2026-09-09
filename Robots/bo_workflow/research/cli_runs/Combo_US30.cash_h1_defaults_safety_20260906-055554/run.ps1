$ErrorActionPreference = 'Stop'
$cliExe = 'C:\Users\Administrator\AppData\Local\Programs\cTrader CLI\ctrader-cli.exe'
$algoPath = 'C:\Users\Administrator\Documents\cAlgo\Sources\Robots\Combo.algo'
$paramsPath = Join-Path $PSScriptRoot 'params.cbotset'
$reportPath = Join-Path $PSScriptRoot 'report.json'
$logPath = Join-Path $PSScriptRoot 'log.txt'
if (Test-Path -LiteralPath $reportPath) { throw 'Use a new directory for another run.' }
$cliArgs = @('backtest', $algoPath, $paramsPath,
    '--start=01/01/2026 00:00', '--end=01/06/2026', '--data-mode=Ticks',
    '--balance=10000', '--ctid=votuankiet96@gmail.com',
    '--pwd-file=C:\Users\Administrator\.ctrader-cli-pwd.txt',
    '--account=7563609', '--broker=FTMO Platform', '--symbol=US30.cash',
    '--period=h1', '--full-access', "--report-json=$reportPath")
$cliArgs | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'arguments.json') -Encoding UTF8
$started = Get-Date
$signalPath = (Get-Content -LiteralPath $paramsPath -Raw | ConvertFrom-Json).Parameters.SignalFilePath
[ordered]@{
    StartedUtc=$started.ToUniversalTime().ToString('o')
    AlgoSHA256=(Get-FileHash -LiteralPath $algoPath).Hash
    SignalSHA256=(Get-FileHash -LiteralPath $signalPath).Hash
    CliVersion=(Get-Item -LiteralPath $cliExe).VersionInfo.FileVersion
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'input.json') -Encoding UTF8
$job = Start-Job -ScriptBlock {
    param($exe, $arguments)
    & $exe @arguments 2>&1 | ForEach-Object { $_.ToString() }
    "CLI_EXIT_CODE=$LASTEXITCODE"
} -ArgumentList $cliExe, $cliArgs
$report = $null
$timedOut = $false
try {
    while ($true) {
        $chunk = @(Receive-Job -Job $job -ErrorAction Continue 2>&1 | ForEach-Object { $_.ToString() })
        if ($chunk.Count) {
            $chunk | Add-Content -LiteralPath $logPath -Encoding UTF8
            $chunk | Select-Object -Last 4 | Write-Output
        }
        if (Test-Path -LiteralPath $reportPath) {
            try { $report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json } catch { $report = $null }
            if ($report -and $report.main.testingPeriod) { break }
        }
        if ($job.State -in @('Completed','Failed','Stopped')) { break }
        if (((Get-Date) - $started).TotalMinutes -ge 20) { $timedOut = $true; break }
        $owned = Get-CimInstance Win32_Process -Filter "Name='ctrader-cli.exe'" |
            Where-Object { $_.CommandLine -like "*$paramsPath*" }
        foreach ($item in $owned) {
            $proc = Get-Process -Id $item.ProcessId -ErrorAction SilentlyContinue
            if ($proc) { Write-Output ("{0:HH:mm:ss} PID={1} CPU={2:N1}s RAM={3:N0}MB elapsed={4:N0}s" -f (Get-Date),$proc.Id,$proc.CPU,($proc.WorkingSet64/1MB),((Get-Date)-$started).TotalSeconds) }
        }
        Start-Sleep -Seconds 5
    }
    # Allow the CLI to flush its final log after it writes report.json.
    Start-Sleep -Seconds 5
    Receive-Job -Job $job -ErrorAction Continue 2>&1 | ForEach-Object { $_.ToString() } |
        Add-Content -LiteralPath $logPath -Encoding UTF8
} finally {
    Get-CimInstance Win32_Process -Filter "Name='ctrader-cli.exe'" |
        Where-Object { $_.CommandLine -like "*$paramsPath*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Stop-Job -Job $job -ErrorAction SilentlyContinue
    Receive-Job -Job $job -ErrorAction SilentlyContinue 2>&1 | ForEach-Object { $_.ToString() } |
        Add-Content -LiteralPath $logPath -Encoding UTF8
    Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
}
$actualStart = $null
$actualEnd = $null
if ($report) {
    $actualStart = [DateTimeOffset]::FromUnixTimeMilliseconds([long]$report.main.testingPeriod.startDate).UtcDateTime.ToString('yyyy-MM-dd HH:mm:ss')
    $actualEnd = [DateTimeOffset]::FromUnixTimeMilliseconds([long]$report.main.testingPeriod.endDate).UtcDateTime.ToString('yyyy-MM-dd HH:mm:ss')
}
$summary = [ordered]@{
    HasReport=[bool]$report; TimedOut=$timedOut
    ElapsedSeconds=[math]::Round(((Get-Date)-$started).TotalSeconds,1)
    ActualStartUTC=$actualStart; ActualEndUTC=$actualEnd
    RequestedStartUTC='2026-01-01 00:00:00'; RequestedEndUTC='2026-06-01 00:00:00'
    Main=if($report){$report.main}else{$null}
    TradeStatistics=if($report){$report.tradeStatistics}else{$null}
}
$summary | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'run-summary.json') -Encoding UTF8
$summary | ConvertTo-Json -Depth 12


