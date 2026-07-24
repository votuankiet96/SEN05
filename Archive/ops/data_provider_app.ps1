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
    [ValidateSet("config", "on", "off")]
    [string]$Replay = "config",
    [string]$ReplayTfs = "",
    [string]$ReplayEndpoint = "",
    [string]$ReplayStartDate = "",
    [int]$ReplayMaxWindows = -1,
    [int]$ReplayWindowBars = -1,
    [int]$ReplayStepBars = -1,
    [double]$ReplayTimeoutSec = 0,

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
    [switch]$FollowLog,
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

function New-PipelineReplayOptions {
    param(
        [ValidateSet("config", "on", "off")]
        [string]$Replay = "config",
        [string]$ReplayTfs = "",
        [string]$ReplayEndpoint = "",
        [string]$ReplayStartDate = "",
        [int]$ReplayMaxWindows = -1,
        [int]$ReplayWindowBars = -1,
        [int]$ReplayStepBars = -1,
        [double]$ReplayTimeoutSec = 0
    )

    [pscustomobject]@{
        Replay = $Replay
        ReplayTfs = $ReplayTfs
        ReplayEndpoint = $ReplayEndpoint
        ReplayStartDate = $ReplayStartDate
        ReplayMaxWindows = $ReplayMaxWindows
        ReplayWindowBars = $ReplayWindowBars
        ReplayStepBars = $ReplayStepBars
        ReplayTimeoutSec = $ReplayTimeoutSec
    }
}

function Read-OptionalInt {
    param(
        [string]$Prompt,
        [int]$Default = -1
    )

    $defaultText = if ($Default -gt 0) { [string]$Default } else { "" }
    $value = Read-Default $Prompt $defaultText
    if (-not $value) { return -1 }
    return [int]$value
}

function Read-OptionalDouble {
    param(
        [string]$Prompt,
        [double]$Default = 0
    )

    $defaultText = if ($Default -gt 0) { [string]$Default } else { "" }
    $value = Read-Default $Prompt $defaultText
    if (-not $value) { return 0 }
    return [double]$value
}

function Read-PipelineReplayOptions {
    Write-Host ""
    Write-Host "Replay bootstrap" -ForegroundColor Cyan
    Write-Host "1. Use config.py / env defaults"
    Write-Host "2. Off (fast, normal history only)"
    Write-Host "3. On with configured replay TFs"
    Write-Host "4. On for H1 only"
    Write-Host "5. On for selected TFs"
    Write-Host "6. Advanced custom"

    $choice = Read-Default "Choose replay option" "1"
    switch ($choice) {
        "1" { return New-PipelineReplayOptions -Replay config }
        "2" { return New-PipelineReplayOptions -Replay off }
        "3" { return New-PipelineReplayOptions -Replay on }
        "4" { return New-PipelineReplayOptions -Replay on -ReplayTfs "H1" }
        "5" {
            $tfValue = Read-Default "Replay TFs comma-list" "H1"
            return New-PipelineReplayOptions -Replay on -ReplayTfs $tfValue
        }
        "6" {
            $modeValue = (Read-Default "Replay config/on/off" "config").Trim().ToLowerInvariant()
            $tfValue = Read-Default "Replay TFs comma-list, blank for config" ""
            $endpointValue = (Read-Default "Replay endpoint data/prodata, blank for config" "").Trim().ToLowerInvariant()
            $startValue = Read-Default "Replay start date, blank for config" ""
            $maxWindowsValue = Read-OptionalInt "Max replay windows per pair, blank for config"
            $windowBarsValue = Read-OptionalInt "Replay window bars, blank for config"
            $stepBarsValue = Read-OptionalInt "Replay step bars, blank for config"
            $timeoutValue = Read-OptionalDouble "Replay timeout sec, blank for config"
            return New-PipelineReplayOptions `
                -Replay $modeValue `
                -ReplayTfs $tfValue `
                -ReplayEndpoint $endpointValue `
                -ReplayStartDate $startValue `
                -ReplayMaxWindows $maxWindowsValue `
                -ReplayWindowBars $windowBarsValue `
                -ReplayStepBars $stepBarsValue `
                -ReplayTimeoutSec $timeoutValue
        }
        default {
            Write-Host "Unknown replay option; using config defaults." -ForegroundColor Yellow
            return New-PipelineReplayOptions -Replay config
        }
    }
}

function Read-PipelineScopeOptions {
    param(
        [bool]$RequireScope = $false
    )

    while ($true) {
        Write-Host ""
        Write-Host "Pipeline scope" -ForegroundColor Cyan
        if (-not $RequireScope) {
            Write-Host "0. All symbols/timeframes"
        }
        Write-Host "1. All asset types (Indice,FOREX,Metal,Crypto)"
        Write-Host "2. Selected asset types"
        Write-Host "3. Selected symbols"
        Write-Host "4. Selected timeframes"
        Write-Host "5. Custom combined scope"

        $defaultChoice = if ($RequireScope) { "1" } else { "0" }
        $choice = Read-Default "Choose scope" $defaultChoice
        switch ($choice) {
            "0" {
                if ($RequireScope) {
                    Write-Host "Reset requires an explicit scope." -ForegroundColor Yellow
                    continue
                }
                return [pscustomobject]@{ Symbols = ""; Timeframes = ""; AssetType = "" }
            }
            "1" {
                return [pscustomobject]@{ Symbols = ""; Timeframes = ""; AssetType = "Indice,FOREX,Metal,Crypto" }
            }
            "2" {
                $assetValue = Read-Default "Asset types comma-list" "Indice,FOREX,Metal,Crypto"
                return [pscustomobject]@{ Symbols = ""; Timeframes = ""; AssetType = $assetValue }
            }
            "3" {
                $symbolsValue = Read-Default "Symbols comma-list" "GOLD,BTCUSD"
                return [pscustomobject]@{ Symbols = $symbolsValue; Timeframes = ""; AssetType = "" }
            }
            "4" {
                $tfValue = Read-Default "Timeframes comma-list" "H1"
                return [pscustomobject]@{ Symbols = ""; Timeframes = $tfValue; AssetType = "" }
            }
            "5" {
                $symbolsValue = Read-Default "Symbols comma-list, blank if not scoped by symbol" ""
                $tfValue = Read-Default "Timeframes comma-list, blank if not scoped by TF" ""
                $assetValue = Read-Default "Asset types comma-list, blank if not scoped by asset" ""
                if ($RequireScope -and -not ($symbolsValue -or $tfValue -or $assetValue)) {
                    Write-Host "Reset requires at least one scope." -ForegroundColor Yellow
                    continue
                }
                return [pscustomobject]@{ Symbols = $symbolsValue; Timeframes = $tfValue; AssetType = $assetValue }
            }
            default {
                Write-Host "Unknown scope option." -ForegroundColor Yellow
            }
        }
    }
}

function Invoke-PipelineWithReplay {
    param(
        [ValidateSet("auto", "gap", "full")]
        [string]$Mode = "auto",
        [switch]$DryRun,
        [string]$Symbols = "",
        [string]$Timeframes = "",
        [string]$AssetType = "",
        [switch]$Reset,
        [switch]$ForceUnlock,
        [switch]$Yes,
        [switch]$FollowLog,
        [Parameter(Mandatory = $true)]
        [object]$ReplayOptions,
        [string]$PythonExe = ""
    )

    Invoke-Sen05Pipeline `
        -Mode $Mode `
        -DryRun:$DryRun `
        -Symbols $Symbols `
        -Timeframes $Timeframes `
        -AssetType $AssetType `
        -Reset:$Reset `
        -ForceUnlock:$ForceUnlock `
        -Yes:$Yes `
        -FollowLog:$FollowLog `
        -Replay $ReplayOptions.Replay `
        -ReplayTfs $ReplayOptions.ReplayTfs `
        -ReplayEndpoint $ReplayOptions.ReplayEndpoint `
        -ReplayStartDate $ReplayOptions.ReplayStartDate `
        -ReplayMaxWindows $ReplayOptions.ReplayMaxWindows `
        -ReplayWindowBars $ReplayOptions.ReplayWindowBars `
        -ReplayStepBars $ReplayOptions.ReplayStepBars `
        -ReplayTimeoutSec $ReplayOptions.ReplayTimeoutSec `
        -PythonExe $PythonExe
}

function Pause-App {
    Write-Host ""
    [void](Read-Host "Press Enter to continue")
}

function Invoke-WsLive {
    if ($FollowLog) {
        Start-Sen05LogFollow -LogName "ws_live.log" -Title "SEN05 ws-live log"
    }

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
            return Invoke-Sen05Pipeline `
                -Mode $Mode `
                -DryRun:$DryRun `
                -Symbols $Symbols `
                -Timeframes $Timeframes `
                -AssetType $AssetType `
                -Reset:$Reset `
                -ForceUnlock:$ForceUnlock `
                -Yes:$Yes `
                -FollowLog:$FollowLog `
                -Replay $Replay `
                -ReplayTfs $ReplayTfs `
                -ReplayEndpoint $ReplayEndpoint `
                -ReplayStartDate $ReplayStartDate `
                -ReplayMaxWindows $ReplayMaxWindows `
                -ReplayWindowBars $ReplayWindowBars `
                -ReplayStepBars $ReplayStepBars `
                -ReplayTimeoutSec $ReplayTimeoutSec `
                -PythonExe $PythonExe
        }
        "ws-live" {
            return Invoke-WsLive
        }
        "checker" {
            $canWrite = (-not $DryRun) -and (-not $CoCheck)
            if ($canWrite -and -not (Confirm-Sen05Action -Message "Checker without -DryRun can repair/rebuild data." -Yes:$Yes)) {
                return 130
            }
            return Invoke-Sen05Checker -DryRun:$DryRun -Symbol $Symbol -Timeframe $Tf -Threshold $Threshold -CoCheck:$CoCheck -CoDays $CoDays -TfCheck:$TfCheck -TfCheckFull:$TfCheckFull -RebuildComputed:$RebuildComputed -ManualConfirm:$ManualConfirm -FollowLog:$FollowLog -PythonExe $PythonExe
        }
        "chart" {
            return Start-Sen05DataChart -Foreground:$Foreground -NoBrowser:$NoBrowser -FollowLog:$FollowLog -PythonExe $PythonExe
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
        Write-Host "3. Full load fast (replay off)"
        Write-Host "4. Full load with replay options"
        Write-Host "5. Scoped reset/reload wizard"
        Write-Host "6. Custom advanced run"
        Write-Host "0. Back"
        $choice = Read-Host "Choose"

        switch ($choice) {
            "1" {
                $replayOptions = New-PipelineReplayOptions -Replay off
                Invoke-PipelineWithReplay -Mode auto -DryRun -FollowLog:$FollowLog -ReplayOptions $replayOptions -PythonExe $PythonExe
                Pause-App
            }
            "2" {
                if (Confirm-Sen05Action -Message "Run gap backfill now?" -Yes:$Yes) {
                    $replayOptions = New-PipelineReplayOptions -Replay off
                    Invoke-PipelineWithReplay -Mode gap -FollowLog:$FollowLog -ReplayOptions $replayOptions -PythonExe $PythonExe
                }
                Pause-App
            }
            "3" {
                if (Confirm-Sen05Action -Message "Run full pipeline load with replay off?" -Yes:$Yes) {
                    $replayOptions = New-PipelineReplayOptions -Replay off
                    Invoke-PipelineWithReplay -Mode full -FollowLog:$FollowLog -ReplayOptions $replayOptions -PythonExe $PythonExe
                }
                Pause-App
            }
            "4" {
                $replayOptions = Read-PipelineReplayOptions
                if (Confirm-Sen05Action -Message "Run full pipeline load with selected replay settings?" -Yes:$Yes) {
                    Invoke-PipelineWithReplay -Mode full -FollowLog:$FollowLog -ReplayOptions $replayOptions -PythonExe $PythonExe
                }
                Pause-App
            }
            "5" {
                $scope = Read-PipelineScopeOptions -RequireScope $true
                $replayOptions = Read-PipelineReplayOptions
                if (Confirm-Sen05Action -Message "Scoped reset can rewrite selected warehouse data." -Yes:$Yes) {
                    Invoke-PipelineWithReplay `
                        -Mode full `
                        -Symbols $scope.Symbols `
                        -Timeframes $scope.Timeframes `
                        -AssetType $scope.AssetType `
                        -Reset `
                        -Yes `
                        -FollowLog:$FollowLog `
                        -ReplayOptions $replayOptions `
                        -PythonExe $PythonExe
                }
                Pause-App
            }
            "6" {
                $modeValue = (Read-Default "Mode auto/gap/full" "auto").Trim().ToLowerInvariant()
                $resetValue = Read-YesNo "Reset selected scope?" $false
                $scope = Read-PipelineScopeOptions -RequireScope $resetValue
                $dryValue = Read-YesNo "Dry-run only?" $true
                $replayOptions = Read-PipelineReplayOptions
                $canRun = $true
                if ($resetValue -and -not $dryValue) {
                    $canRun = Confirm-Sen05Action -Message "Execute reset/reload with selected settings?" -Yes:$Yes
                }
                if ($canRun) {
                    Invoke-PipelineWithReplay `
                        -Mode $modeValue `
                        -DryRun:$dryValue `
                        -Symbols $scope.Symbols `
                        -Timeframes $scope.Timeframes `
                        -AssetType $scope.AssetType `
                        -Reset:$resetValue `
                        -Yes:$resetValue `
                        -FollowLog:$FollowLog `
                        -ReplayOptions $replayOptions `
                        -PythonExe $PythonExe
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
            "1" { Invoke-WsLive; Pause-App }
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
            "1" { Invoke-Sen05Checker -DryRun -FollowLog:$FollowLog -PythonExe $PythonExe; Pause-App }
            "2" {
                $symValue = Read-Default "Symbol" "GOLD"
                Invoke-Sen05Checker -DryRun -Symbol $symValue -FollowLog:$FollowLog -PythonExe $PythonExe
                Pause-App
            }
            "3" {
                $tfValue = Read-Default "Timeframe" "H1"
                Invoke-Sen05Checker -DryRun -Timeframe $tfValue -FollowLog:$FollowLog -PythonExe $PythonExe
                Pause-App
            }
            "4" {
                $daysValue = [int](Read-Default "Lookback days" "7")
                Invoke-Sen05Checker -CoCheck -CoDays $daysValue -FollowLog:$FollowLog -PythonExe $PythonExe
                Pause-App
            }
            "5" {
                $symValue = Read-Default "Symbol, blank for all" ""
                $tfValue = Read-Default "Timeframe, blank for all" ""
                $fullValue = Read-YesNo "Show full gap details?" $false
                Invoke-Sen05Checker -DryRun -Symbol $symValue -Timeframe $tfValue -TfCheck -TfCheckFull:$fullValue -FollowLog:$FollowLog -PythonExe $PythonExe
                Pause-App
            }
            "6" {
                $symValue = Read-Default "Symbol, blank for all" ""
                $tfValue = Read-Default "Computed timeframe, blank for all" ""
                Invoke-Sen05Checker -DryRun -Symbol $symValue -Timeframe $tfValue -RebuildComputed -FollowLog:$FollowLog -PythonExe $PythonExe
                Pause-App
            }
            "7" {
                $symValue = Read-Default "Symbol, blank for all" ""
                $tfValue = Read-Default "Computed timeframe, blank for all" ""
                if (Confirm-Sen05Action -Message "Execute computed TF rebuild? This writes to the database." -Yes:$Yes) {
                    Invoke-Sen05Checker -Symbol $symValue -Timeframe $tfValue -RebuildComputed -FollowLog:$FollowLog -PythonExe $PythonExe
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
                Invoke-Sen05Checker -DryRun:$dryValue -Symbol $symValue -Timeframe $tfValue -ManualConfirm:$manualValue -FollowLog:$FollowLog -PythonExe $PythonExe
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
            "4" { Start-Sen05DataChart -Foreground:$Foreground -NoBrowser:$NoBrowser -FollowLog:$FollowLog -PythonExe $PythonExe; Pause-App }
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
