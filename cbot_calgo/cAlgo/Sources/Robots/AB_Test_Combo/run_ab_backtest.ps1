param(
    [Parameter(Mandatory = $true)]
    [string] $Ctid,

    [Parameter(Mandatory = $true)]
    [string] $PwdFile,

    [Parameter(Mandatory = $true)]
    [string] $Account,

    [string] $Symbol = "US 30",
    [string] $Period = "h4",
    [string] $DataMode = "ticks",
    [double] $Balance = 10000
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Cli = Join-Path $env:LOCALAPPDATA "Spotware\cTrader\abb70432efbee65d18af69e79fe8efe1\ctrader-cli.exe"
$Reports = Join-Path $PSScriptRoot "reports"

$AlgoV0 = Join-Path $Root "SEN_Combo_V0.algo"
$AlgoV1 = Join-Path $Root "SEN_Combo_V1.algo"
$SettingsV0 = Join-Path $PSScriptRoot "SEN_Combo_V0_US30_H4_20230101_20250601.cbotset"
$SettingsV1 = Join-Path $PSScriptRoot "SEN_Combo_V1_US30_H4_20230101_20250601.cbotset"

New-Item -ItemType Directory -Force -Path $Reports | Out-Null

$CommonArgs = @(
    "--start=01/01/2023",
    "--end=01/06/2025 23:59",
    "--data-mode=$DataMode",
    "--balance=$Balance",
    "--ctid=$Ctid",
    "--pwd-file=$PwdFile",
    "--account=$Account",
    "--symbol=$Symbol",
    "--period=$Period"
)

function Invoke-Backtest {
    param(
        [string] $Name,
        [string] $Algo,
        [string] $Settings
    )

    $HtmlReport = Join-Path $Reports "$Name.html"
    $JsonReport = Join-Path $Reports "$Name.json"

    Write-Host "Running $Name..."
    & $Cli backtest $Algo $Settings @CommonArgs "--report=$HtmlReport" "--report-json=$JsonReport"

    if ($LASTEXITCODE -ne 0) {
        throw "$Name backtest failed with exit code $LASTEXITCODE"
    }

    Write-Host "$Name report JSON: $JsonReport"
    Write-Host "$Name report HTML: $HtmlReport"
}

Invoke-Backtest -Name "SEN_Combo_V0_US30_H4_20230101_20250601" -Algo $AlgoV0 -Settings $SettingsV0
Invoke-Backtest -Name "SEN_Combo_V1_US30_H4_20230101_20250601" -Algo $AlgoV1 -Settings $SettingsV1

Write-Host "A/B backtest completed."
