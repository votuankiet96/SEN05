param(
    [ValidateSet("menu", "pipeline", "ws-live", "checker", "chart", "probe", "logs", "status")]
    [string]$Command = "menu",

    [ValidateSet("auto", "gap", "full")]
    [string]$Mode = "auto",

    [switch]$DryRun,
    [string]$Symbols = "",
    [string]$Timeframes = "",
    [string]$AssetType = "",
    [switch]$Reset,
    [switch]$ForceUnlock,

    [string]$Symbol = "",
    [string]$Tf = "",
    [double]$Threshold = 0,
    [switch]$CoCheck,
    [int]$CoDays = 7,
    [switch]$TfCheck,
    [switch]$TfCheckFull,
    [switch]$RebuildComputed,
    [switch]$ManualConfirm,

    [string]$ProbeSymbol = "GOLD",
    [string]$ProbeSizes = "",
    [double]$ProbeTimeoutSec = 0,
    [int]$ProbeMoreRounds = -1,
    [int]$ProbeMoreBars = -1,

    [switch]$Forever,
    [int]$RestartDelaySec = 20,
    [switch]$Foreground,
    [switch]$NoBrowser,
    [switch]$Yes,
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"

$ModulePath = Join-Path $PSScriptRoot "lib\Sen05Ops.psm1"
Import-Module $ModulePath -Force

function Read-Default {
    param(
        [string]$Prompt,
        [string]$Default = ""
    )
    if ($Default) {
        $value = Read-Host "$Prompt [$Default]"
        if (-not $value) { return $Default }
        return $value
    }
    return (Read-Host $Prompt)
}

function Read-YesNo {
    param(
        [string]$Prompt,
        [bool]$Default = $false
    )
    $suffix = $(if ($Default) { "[Y/n]" } else { "[y/N]" })
    $value = (Read-Host "$Prompt $suffix").Trim().ToLowerInvariant()
    if (-not $value) { return $Default }
    return ($value -in @("y", "yes"))
}

function Pause-App {
    Write-Host ""
    [void](Read-Host "Press Enter to continue")
}

function Invoke-WsLive {
    if ($Forever) {
        while ($true) {
            $code = Invoke-Sen05PythonScript -ScriptKey WsLive -Arguments @() -PythonExe $PythonExe
            Write-Host ("ws-live exited with code {0}; restart in {1}s" -f $code, $RestartDelaySec)
            Start-Sleep -Seconds $RestartDelaySec
        }
    }

    return Invoke-Sen05PythonScript -ScriptKey WsLive -Arguments @() -PythonExe $PythonExe
}

function Invoke-CommandMode {
    switch ($Command) {
        "pipeline" {
            if ($Reset) {
                $hasScope = ($Symbols -or $Timeframes -or $AssetType)
                if (-not $hasScope) {
                    Write-Error "Pipeline --reset requires at least one scope: -Symbols, -Timeframes, or -AssetType."
                    return 2
                }
                if (-not (Confirm-Sen05Action -Message "Pipeline reset can rewrite scoped warehouse data." -Yes:$Yes)) {
                    return 130
                }
            }
            return Invoke-Sen05Pipeline -Mode $Mode -DryRun:$DryRun -Symbols $Symbols -Timeframes $Timeframes -AssetType $AssetType -Reset:$Reset -ForceUnlock:$ForceUnlock -PythonExe $PythonExe
        }
        "ws-live" {
            return Invoke-WsLive
        }
        "checker" {
            $canWrite = (-not $DryRun) -and (-not $CoCheck)
            if ($canWrite -and -not (Confirm-Sen05Action -Message "Checker without -DryRun can repair/rebuild data." -Yes:$Yes)) {
                return 130
            }
            return Invoke-Sen05Checker -DryRun:$DryRun -Symbol $Symbol -Timeframe $Tf -Threshold $Threshold -CoCheck:$CoCheck -CoDays $CoDays -TfCheck:$TfCheck -TfCheckFull:$TfCheckFull -RebuildComputed:$RebuildComputed -ManualConfirm:$ManualConfirm -PythonExe $PythonExe
        }
        "chart" {
            return Start-Sen05DataChart -Foreground:$Foreground -NoBrowser:$NoBrowser -PythonExe $PythonExe
        }
        "probe" {
            return Invoke-Sen05Probe -Symbol $ProbeSymbol -Timeframes $Timeframes -Sizes $ProbeSizes -TimeoutSec $ProbeTimeoutSec -MoreRounds $ProbeMoreRounds -MoreBars $ProbeMoreBars -PythonExe $PythonExe
        }
        "logs" {
            Show-Sen05LogSummary
            return 0
        }
        "status" {
            Show-Sen05ProcessStatus
            Show-Sen05LogSummary -Last 6
            return 0
        }
    }
}

function Show-PipelineMenu {
    while ($true) {
        Write-Host ""
        Write-Host "Pipeline" -ForegroundColor Cyan
        Write-Host "1. Dry-run auto plan"
        Write-Host "2. Gap backfill"
        Write-Host "3. Full load"
        Write-Host "4. Custom scoped run"
        Write-Host "5. Scoped reset/reload"
        Write-Host "0. Back"
        $choice = Read-Host "Choose"

        switch ($choice) {
            "1" { Invoke-Sen05Pipeline -Mode auto -DryRun -PythonExe $PythonExe; Pause-App }
            "2" {
                if (Confirm-Sen05Action -Message "Run gap backfill now?" -Yes:$Yes) {
                    Invoke-Sen05Pipeline -Mode gap -PythonExe $PythonExe
                }
                Pause-App
            }
            "3" {
                if (Confirm-Sen05Action -Message "Run full pipeline load now?" -Yes:$Yes) {
                    Invoke-Sen05Pipeline -Mode full -PythonExe $PythonExe
                }
                Pause-App
            }
            "4" {
                $modeValue = Read-Default "Mode auto/gap/full" "auto"
                $symbolsValue = Read-Default "Symbols comma-list, blank for all" ""
                $tfValue = Read-Default "Timeframes comma-list, blank for all" ""
                $assetValue = Read-Default "Asset types comma-list, blank for all" ""
                $dryValue = Read-YesNo "Dry-run only?" $true
                Invoke-Sen05Pipeline -Mode $modeValue -DryRun:$dryValue -Symbols $symbolsValue -Timeframes $tfValue -AssetType $assetValue -PythonExe $PythonExe
                Pause-App
            }
            "5" {
                $symbolsValue = Read-Default "Symbols comma-list, blank if not scoped by symbol" ""
                $tfValue = Read-Default "Timeframes comma-list, blank if not scoped by TF" ""
                $assetValue = Read-Default "Asset types comma-list, blank if not scoped by asset" ""
                if (-not ($symbolsValue -or $tfValue -or $assetValue)) {
                    Write-Host "Reset requires at least one scope." -ForegroundColor Yellow
                    Pause-App
                    continue
                }
                if (Confirm-Sen05Action -Message "Scoped reset can delete/reload selected warehouse data." -Yes:$Yes) {
                    Invoke-Sen05Pipeline -Mode full -Symbols $symbolsValue -Timeframes $tfValue -AssetType $assetValue -Reset -PythonExe $PythonExe
                }
                Pause-App
            }
            "0" { return }
        }
    }
}

function Show-WsLiveMenu {
    while ($true) {
        Write-Host ""
        Write-Host "WS Live" -ForegroundColor Cyan
        Write-Host "1. Start foreground"
        Write-Host "2. Start supervised forever"
        Write-Host "3. Status and logs"
        Write-Host "0. Back"
        $choice = Read-Host "Choose"

        switch ($choice) {
            "1" { Invoke-Sen05PythonScript -ScriptKey WsLive -Arguments @() -PythonExe $PythonExe; Pause-App }
            "2" {
                if (Confirm-Sen05Action -Message "Start ws-live forever loop? Use Ctrl+C to stop." -Yes:$Yes) {
                    $script:Forever = $true
                    Invoke-WsLive
                }
            }
            "3" { Show-Sen05ProcessStatus; Show-Sen05LogSummary -Last 8; Pause-App }
            "0" { return }
        }
    }
}

function Show-CheckerMenu {
    while ($true) {
        Write-Host ""
        Write-Host "Checker / Repair" -ForegroundColor Cyan
        Write-Host "1. Dry-run full scan"
        Write-Host "2. Dry-run one symbol"
        Write-Host "3. Dry-run one timeframe"
        Write-Host "4. C-O continuity check"
        Write-Host "5. TF gap check"
        Write-Host "6. Rebuild computed TF dry-run"
        Write-Host "7. Rebuild computed TF execute"
        Write-Host "8. Custom checker run"
        Write-Host "0. Back"
        $choice = Read-Host "Choose"

        switch ($choice) {
            "1" { Invoke-Sen05Checker -DryRun -PythonExe $PythonExe; Pause-App }
            "2" {
                $symValue = Read-Default "Symbol" "GOLD"
                Invoke-Sen05Checker -DryRun -Symbol $symValue -PythonExe $PythonExe
                Pause-App
            }
            "3" {
                $tfValue = Read-Default "Timeframe" "H1"
                Invoke-Sen05Checker -DryRun -Timeframe $tfValue -PythonExe $PythonExe
                Pause-App
            }
            "4" {
                $daysValue = [int](Read-Default "Lookback days" "7")
                Invoke-Sen05Checker -CoCheck -CoDays $daysValue -PythonExe $PythonExe
                Pause-App
            }
            "5" {
                $symValue = Read-Default "Symbol, blank for all" ""
                $tfValue = Read-Default "Timeframe, blank for all" ""
                $fullValue = Read-YesNo "Show full gap details?" $false
                Invoke-Sen05Checker -DryRun -Symbol $symValue -Timeframe $tfValue -TfCheck -TfCheckFull:$fullValue -PythonExe $PythonExe
                Pause-App
            }
            "6" {
                $symValue = Read-Default "Symbol, blank for all" ""
                $tfValue = Read-Default "Computed timeframe, blank for all" ""
                Invoke-Sen05Checker -DryRun -Symbol $symValue -Timeframe $tfValue -RebuildComputed -PythonExe $PythonExe
                Pause-App
            }
            "7" {
                $symValue = Read-Default "Symbol, blank for all" ""
                $tfValue = Read-Default "Computed timeframe, blank for all" ""
                if (Confirm-Sen05Action -Message "Execute computed TF rebuild? This writes to the database." -Yes:$Yes) {
                    Invoke-Sen05Checker -Symbol $symValue -Timeframe $tfValue -RebuildComputed -PythonExe $PythonExe
                }
                Pause-App
            }
            "8" {
                $symValue = Read-Default "Symbol, blank for all" ""
                $tfValue = Read-Default "Timeframe, blank for all" ""
                $dryValue = Read-YesNo "Dry-run only?" $true
                $manualValue = Read-YesNo "Manual-confirm notification?" $false
                if (-not $dryValue -and -not (Confirm-Sen05Action -Message "Custom checker run without dry-run can repair data." -Yes:$Yes)) {
                    Pause-App
                    continue
                }
                Invoke-Sen05Checker -DryRun:$dryValue -Symbol $symValue -Timeframe $tfValue -ManualConfirm:$manualValue -PythonExe $PythonExe
                Pause-App
            }
            "0" { return }
        }
    }
}

function Show-ProbeMenu {
    $symValue = Read-Default "Probe symbol" "GOLD"
    $tfValue = Read-Default "Timeframes comma-list, blank for all" ""
    $sizesValue = Read-Default "Requested sizes, blank for default" ""
    Invoke-Sen05Probe -Symbol $symValue -Timeframes $tfValue -Sizes $sizesValue -PythonExe $PythonExe
    Pause-App
}

function Show-LogMenu {
    while ($true) {
        Write-Host ""
        Write-Host "Logs / Status" -ForegroundColor Cyan
        Write-Host "1. Summary"
        Write-Host "2. Tail pipeline scheduler/run summary"
        Write-Host "3. Tail ws_live"
        Write-Host "4. Tail checker"
        Write-Host "5. Tail data chart"
        Write-Host "6. Process status"
        Write-Host "0. Back"
        $choice = Read-Host "Choose"
        $paths = Get-Sen05OpsPaths

        switch ($choice) {
            "1" { Show-Sen05LogSummary; Pause-App }
            "2" {
                foreach ($name in @("pipeline_scheduler.log", "pipeline.log", "pipeline_run_summary.jsonl")) {
                    $path = Join-Path $paths.DataProviderLogs $name
                    if (Test-Path -LiteralPath $path) {
                        Write-Host "`n== $path ==" -ForegroundColor Cyan
                        Get-Content -LiteralPath $path -Tail 80
                    }
                }
                Pause-App
            }
            "3" {
                foreach ($name in @("ws_live_supervisor.log", "ws_live.log")) {
                    $path = Join-Path $paths.DataProviderLogs $name
                    if (Test-Path -LiteralPath $path) {
                        Write-Host "`n== $path ==" -ForegroundColor Cyan
                        Get-Content -LiteralPath $path -Tail 80
                    }
                }
                Pause-App
            }
            "4" {
                foreach ($name in @("checker_scheduler.log", "checker.log")) {
                    $path = Join-Path $paths.DataProviderLogs $name
                    if (Test-Path -LiteralPath $path) {
                        Write-Host "`n== $path ==" -ForegroundColor Cyan
                        Get-Content -LiteralPath $path -Tail 80
                    }
                }
                Pause-App
            }
            "5" {
                foreach ($name in @("data_chart.out.log", "data_chart.err.log")) {
                    $path = Join-Path $paths.DataProviderLogs $name
                    if (Test-Path -LiteralPath $path) {
                        Write-Host "`n== $path ==" -ForegroundColor Cyan
                        Get-Content -LiteralPath $path -Tail 80
                    }
                }
                Pause-App
            }
            "6" { Show-Sen05ProcessStatus; Pause-App }
            "0" { return }
        }
    }
}

function Show-MainMenu {
    while ($true) {
        Write-Host ""
        Write-Host "SEN05 Data Provider Ops" -ForegroundColor Green
        Write-Host "1. Pipeline"
        Write-Host "2. WS Live"
        Write-Host "3. Checker / Repair"
        Write-Host "4. Data Dashboard"
        Write-Host "5. Probe / Diagnostics"
        Write-Host "6. Logs / Status"
        Write-Host "0. Exit"
        $choice = Read-Host "Choose"

        switch ($choice) {
            "1" { Show-PipelineMenu }
            "2" { Show-WsLiveMenu }
            "3" { Show-CheckerMenu }
            "4" { Start-Sen05DataChart -Foreground:$Foreground -NoBrowser:$NoBrowser -PythonExe $PythonExe; Pause-App }
            "5" { Show-ProbeMenu }
            "6" { Show-LogMenu }
            "0" { return 0 }
        }
    }
}

if ($Command -ne "menu") {
    exit (Invoke-CommandMode)
}

exit (Show-MainMenu)
