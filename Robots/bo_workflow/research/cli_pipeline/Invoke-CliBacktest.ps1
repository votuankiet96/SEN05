<#
.SYNOPSIS
    Pipeline backtest chuẩn hoá qua ctrader-cli — không cần GUI.

.DESCRIPTION
    Bọc lại toàn bộ quy trình đã kiểm chứng thủ công trong phiên làm việc
    2026-09-01 thành 1 hàm tái sử dụng được:
        1. Validate Symbol (tên đầy đủ, vd US30.cash) qua `ctrader-cli symbols`
           — KHÔNG đoán/rút gọn (bài học: "US30" thay vì "US30.cash" từng
           gây crash khó hiểu "Message expected", xem memory
           ctrader-cli-symbol-name-bug).
        2. Validate Period qua danh sách tĩnh đã lấy từ `ctrader-cli periods`.
        3. Gộp tham số cBot (RiskPercent, SignalFilePath...) vào 1 file
           .cbotset tạm (đáng tin cậy hơn nhiều so với truyền rời từng
           --PropertyName=value).
        4. Chuyển ngày sang đúng định dạng dd/MM/yyyy mà ctrader-cli yêu cầu,
           chạy nền qua Start-Job (không block).
        5. Theo dõi bằng CPU/memory tăng dần của tiến trình con (KHÔNG dựa
           vào log — output console bị buffer nặng khi redirect ra file,
           không phản ánh đúng tiến độ thời gian thực).
        6. Chỉ coi là thành công khi report.json parse được VÀ testingPeriod
           trong report khớp đúng khoảng ngày người gọi yêu cầu.
        7. Khi xong: lưu log.txt (= console output, cùng định dạng Print()
           dùng được thẳng với research/fidelity_lib.py) + report.json +
           command.txt (để tái lập) vào research/cli_runs/<Bot>_<Symbol>_
           <TF>_<timestamp>/ — dọn tiến trình con nếu nó không tự thoát
           (đã gặp thật: tiến trình in xong kết quả nhưng không tự exit).

.NOTES
    Rủi ro/hạn chế đã biết (xem AGENT.md 2026-09-01/02 để biết đầy đủ bối cảnh):
    - Chỉ xác nhận --data-mode hợp lệ với "Ticks" và "M1" — giá trị khác
      chưa dò hết.
    - --full-access không có trong tài liệu chính thức của lệnh backtest
      nhưng được chấp nhận — cần cho mọi cBot AccessRights.FullAccess.
    - Chạy nhiều lượt song song trên cùng account có thể tranh tài nguyên
      (chưa xác định giới hạn an toàn) — nên giới hạn số lượt đồng thời.
    - **Bug đã gặp + đã sửa (2026-09-02)**: vòng lặp theo dõi TỪNG DỰA vào
      `$job.State -eq "Completed"` để biết đã xong — nhưng `ctrader-cli.exe`
      có bug thật: đôi khi in xong KẾT QUẢ ĐẦY ĐỦ (report.json đã ghi xong)
      nhưng KHÔNG TỰ EXIT, khiến `$job.State` kẹt ở "Running" mãi mãi dù việc
      đã xong từ lâu — hậu quả: đã KILL NHẦM 5 lượt backtest đang có kết quả
      TỐT vì tưởng treo (xem AGENT.md "tiếp 23"). Đã sửa: tín hiệu hoàn tất
      THẬT SỰ duy nhất đáng tin là chính `report.json` đã xuất hiện + parse
      được — kiểm tra file này mỗi vòng poll, KHÔNG còn dựa vào `$job.State`
      hay CPU-đứng-yên để kết luận treo. CPU đứng yên giờ chỉ là cảnh báo
      chờ xác nhận thêm, không tự động = treo nữa.
#>

$script:CliExe = "ctrader-cli"
$script:CTID = "votuankiet96@gmail.com"
$script:PwdFile = "C:\Users\Administrator\.ctrader-cli-pwd.txt"
$script:Account = "7563609"
$script:Broker = "FTMO Platform"
$script:CliRunsRoot = "C:\Users\Administrator\Documents\cAlgo\Sources\Robots\research\cli_runs"
$script:CliBatchesRoot = "C:\Users\Administrator\Documents\cAlgo\Sources\Robots\research\cli_batches"
$script:CliComparisonsRoot = "C:\Users\Administrator\Documents\cAlgo\Sources\Robots\research\cli_comparisons"

# Danh sách period hop le, lay tinh tu `ctrader-cli periods` (khong doi theo
# thoi gian, khong can goi lai moi lan - do la 1 enum co dinh cua cTrader).
$script:ValidPeriods = @(
    "t1","t2","t3","t4","t5","t6","t7","t8","t9","t10","t15","t20","t25","t30","t40","t50","t60","t80","t90",
    "t100","t150","t200","t250","t300","t500","t750","t1000",
    "m1","m2","m3","m4","m5","m6","m7","m8","m9","m10","m15","m20","m30","m45",
    "h1","h2","h3","h4","h6","h8","h12","D1","D2","D3","W1","Month1",
    "Re1","Re2","Re3","Re4","Re5","Re6","Re7","Re8","Re9","Re10","Re15","Re20","Re25","Re30","Re35","Re40",
    "Re45","Re50","Re100","Re150","Re200","Re300","Re500","Re800","Re1000","Re2000",
    "Ra1","Ra2","Ra3","Ra4","Ra5","Ra8","Ra10","Ra20","Ra30","Ra50","Ra80","Ra100","Ra150","Ra200","Ra300",
    "Ra500","Ra800","Ra1000","Ra2000","Ra5000","Ra7500","Ra10000",
    "Hm1","Hm2","Hm3","Hm4","Hm5","Hm6","Hm7","Hm8","Hm9","Hm10","Hm15","Hm20","Hm30","Hm45",
    "Hh1","Hh2","Hh3","Hh4","Hh6","Hh8","Hh12","Hd1","Hd2","Hd3","Hw1","HMonth1"
)

function Resolve-CliSymbol {
    <#
    .SYNOPSIS
        Tra ten symbol DAY DU tu 1 tu khoa ngan (vd "UK100" -> "UK100.cash").
        Bat buoc goi truoc MOI lan backtest 1 symbol chua tung dung - khong
        bao gio doan/rut gon (xem memory ctrader-cli-symbol-name-bug.md).
    #>
    param([Parameter(Mandatory)][string]$Keyword)

    # QUAN TRONG: bao trong @(...) de LUON ep ket qua thanh mang, ke ca khi
    # pipeline chi tra ve 1 phan tu - neu khong, PowerShell "unwrap" ket qua
    # 1-phan-tu thanh chuoi don, roi [0] se lay nham KY TU DAU cua chuoi
    # thay vi phan tu dau mang (loi thuc te da gap: 'UK100' -> 'U'). Cung
    # doi ten bien khoi '$matches' - trung ten bien tu dong cua PowerShell
    # (duoc -match operator ghi de), du khong phai nguyen nhan chinh o day
    # van la thoi quen an toan hon.
    $activeCli = @(Get-CimInstance Win32_Process -Filter "Name='ctrader-cli.exe'" -ErrorAction SilentlyContinue)
    if ($activeCli.Count -gt 0) {
        throw "Dang co $($activeCli.Count) tien trinh ctrader-cli.exe. Dung/don sach tien trinh cu truoc khi resolve symbol."
    }

    $raw = & $script:CliExe symbols --ctid=$script:CTID --pwd-file=$script:PwdFile --account=$script:Account "--broker=$script:Broker" 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Lenh 'ctrader-cli symbols' that bai (exit code $LASTEXITCODE)."
    }

    $foundNames = @($raw | Select-String -Pattern "`"Name`":\s*`"([^`"]*$([regex]::Escape($Keyword))[^`"]*)`"" -AllMatches |
        ForEach-Object { $_.Matches } | ForEach-Object { $_.Groups[1].Value } | Select-Object -Unique)

    if ($foundNames.Count -eq 0) {
        throw "Khong tim thay symbol nao khop tu khoa '$Keyword'. Chay 'ctrader-cli symbols ...' thu cong de xem toan bo danh sach."
    }
    $exactNames = @($foundNames | Where-Object { $_ -ieq $Keyword })
    if ($exactNames.Count -eq 1) {
        return $exactNames[0]
    }
    if ($foundNames.Count -gt 1) {
        throw "Co $($foundNames.Count) symbol khop '$Keyword': $($foundNames -join ', '). Hay truyen ten day du de tranh chon nham."
    }
    return $foundNames[0]
}

function Invoke-CliBacktest {
    <#
    .SYNOPSIS
        Chay 1 lam backtest qua ctrader-cli, khong block, tu luu ket qua.

    .PARAMETER AlgoPath
        Duong dan file .algo da build (vd Combo.algo).
    .PARAMETER Symbol
        Ten symbol NGAN de tra cuu (vd "US30", "UK100") - se tu goi
        Resolve-CliSymbol de lay ten day du that su dung khi goi CLI.
    .PARAMETER Period
        Ma timeframe (vd h4, m30) - validate qua $script:ValidPeriods.
    .PARAMETER StartDate / EndDate
        DateTime hoac chuoi ISO (vd "2025-01-01") - tu doi sang dung dinh
        dang dd/MM/yyyy ma ctrader-cli yeu cau.
    .PARAMETER DataMode
        "Ticks" hoac "M1" (2 gia tri da xac nhan hop le). Default: Ticks.
    .PARAMETER Params
        Hashtable tham so cBot (vd @{RiskPercent=0.5; SignalFilePath="..."}).
        Duoc goi vao 1 file .cbotset tam truoc khi goi.
    .PARAMETER TimeoutMinutes
        Thoi gian toi da cho (phut) truoc khi coi la treo va dung. Default 60
        - du lieu tick nhieu thang co the that su lau.
    .PARAMETER PollSeconds
        Chu ky kiem tra tien do (giay). Default 20.

    .OUTPUTS
        PSCustomObject { Success, OutputDir, LogPath, ReportJsonPath,
                          CommandPath, TimedOut, FailureReason,
                          RequestedStart, RequestedEnd, ActualStart,
                          ActualEnd, Summary }
    #>
    param(
        [Parameter(Mandatory)][string]$AlgoPath,
        [Parameter(Mandatory)][string]$Symbol,
        [Parameter(Mandatory)][string]$Period,
        [Parameter(Mandatory)]$StartDate,
        [Parameter(Mandatory)]$EndDate,
        [ValidateSet("Ticks", "M1")][string]$DataMode = "Ticks",
        [hashtable]$Params = @{},
        [int]$TimeoutMinutes = 60,
        [int]$PollSeconds = 20
    )

    if ($Period -notin $script:ValidPeriods) {
        throw "Period '$Period' khong hop le. Xem `$script:ValidPeriods hoac chay 'ctrader-cli periods'."
    }
    if ($TimeoutMinutes -lt 1) {
        throw "TimeoutMinutes phai >= 1."
    }
    if ($PollSeconds -lt 1) {
        throw "PollSeconds phai >= 1."
    }
    if (-not (Get-Command $script:CliExe -ErrorAction SilentlyContinue)) {
        throw "Khong tim thay '$script:CliExe' trong PATH."
    }
    if (-not (Test-Path -LiteralPath $script:PwdFile -PathType Leaf)) {
        throw "Khong tim thay pwd-file da cau hinh tai '$script:PwdFile'."
    }

    $activeCli = @(Get-CimInstance Win32_Process -Filter "Name='ctrader-cli.exe'" -ErrorAction SilentlyContinue)
    if ($activeCli.Count -gt 0) {
        throw "Dang co $($activeCli.Count) tien trinh ctrader-cli.exe. Dung/don sach tien trinh cu truoc khi bat dau backtest moi."
    }

    if (-not (Test-Path -LiteralPath $AlgoPath -PathType Leaf)) {
        throw "Khong tim thay algo file '$AlgoPath'."
    }
    $AlgoPath = (Resolve-Path -LiteralPath $AlgoPath).Path

    if ($Params.ContainsKey("SignalFilePath")) {
        $requestedSignalPath = [string]$Params["SignalFilePath"]
        if (-not (Test-Path -LiteralPath $requestedSignalPath -PathType Leaf)) {
            throw "SignalFilePath khong ton tai: '$requestedSignalPath'."
        }
    }

    $start = [datetime]$StartDate
    $end = [datetime]$EndDate
    if ($end -le $start) {
        throw "EndDate '$end' phai lon hon StartDate '$start'."
    }

    $fullSymbol = Resolve-CliSymbol -Keyword $Symbol
    Write-Host "Symbol da xac nhan: '$Symbol' -> '$fullSymbol'"

    $botName = [System.IO.Path]::GetFileNameWithoutExtension($AlgoPath)
    if (-not (Test-Path -LiteralPath $script:CliRunsRoot)) {
        New-Item -ItemType Directory -Path $script:CliRunsRoot | Out-Null
    }
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $outDirBase = Join-Path $script:CliRunsRoot "$($botName)_$($fullSymbol)_$($Period)_$timestamp"
    $outDir = $outDirBase
    $suffix = 1
    while (Test-Path -LiteralPath $outDir) {
        $outDir = "$outDirBase-$('{0:D2}' -f $suffix)"
        $suffix++
    }
    New-Item -ItemType Directory -Path $outDir | Out-Null

    # Goi tham so cBot vao 1 file .cbotset tam - dang tin cay hon rat nhieu
    # so voi truyen roi tung --PropertyName=value (da xac nhan qua session
    # nay: gia tri co dau '.' (vd duong dan Z:\...), khoang trang... de gay
    # loi quoting neu truyen rieng le).
    $cbotsetPath = Join-Path $outDir "params.cbotset"
    $paramsJson = @{ Parameters = @{} }
    foreach ($key in $Params.Keys) { $paramsJson.Parameters[$key] = [string]$Params[$key] }
    $paramsJson | ConvertTo-Json -Depth 5 | Set-Content -Path $cbotsetPath -Encoding utf8

    $reportJsonPath = Join-Path $outDir "report.json"
    $logPath = Join-Path $outDir "log.txt"
    $commandPath = Join-Path $outDir "command.txt"
    $runSummaryPath = Join-Path $outDir "run-summary.json"

    function Build-Args($startStr, $endStr) {
        return @(
            "backtest", $AlgoPath, $cbotsetPath,
            "--start=$startStr", "--end=$endStr", "--data-mode=$DataMode",
            "--ctid=$script:CTID", "--pwd-file=$script:PwdFile", "--account=$script:Account",
            "--broker=$script:Broker", "--symbol=$fullSymbol", "--period=$Period", "--full-access",
            "--report-json=$reportJsonPath"
        )
    }

    # ctrader-cli batch mode dung dd/MM/yyyy. Dinh dang MM/dd/yyyy truoc day
    # da khien 01/04/2025 (1 Apr) bi hieu thanh 04/01/2025 (4 Jan), tao cam
    # giac UK100 "dung som" du report van hop le. Tuyet doi khong tu dich
    # +/-1 ngay: thay doi am tham pham vi backtest lam mat tinh tai lap.
    $startStr = $start.ToString("dd/MM/yyyy", [Globalization.CultureInfo]::InvariantCulture)
    $endStr = $end.ToString("dd/MM/yyyy", [Globalization.CultureInfo]::InvariantCulture)
    $usedArgs = Build-Args $startStr $endStr
    $usedArgs | Out-String | Set-Content -Path $commandPath -Encoding utf8

    Write-Host "Dang chay: $script:CliExe $($usedArgs -join ' ')"
    $job = Start-Job -ScriptBlock {
        param($exe, $argList)
        & $exe @argList 2>&1
    } -ArgumentList $script:CliExe, $usedArgs

    # Theo doi qua CPU/memory tang dan cua tien trinh con - KHONG doc log de
    # phat hien TIEN DO (buffer khi redirect khien log khong phan anh dung
    # thoi gian thuc). NHUNG diem dung vong lap KHONG duoc chi dua vao
    # $job.State=="Completed" - $job.State chi doi khi chinh tien trinh
    # ctrader-cli.exe THOAT, va co 1 bug THAT cua ctrader-cli (da gap nhieu
    # lan 2026-09-01/02): tien trinh in xong KET QUA DAY DU (report.json da
    # ghi xong, log co dong "CBot instance ... stopped") nhung KHONG TU EXIT
    # - $job.State ket qua o "Running" MAI MAI du viec da xong tu lau, khien
    # vong lap cu nhu vay hieu nham la treo, cho het TimeoutMinutes roi kill
    # oan 1 lam da chay thanh cong (da xay ra that, xem AGENT.md "tiep 23" -
    # kill nham 5 lam dang co ket qua tot). Tin hieu HOAN TAT THAT SU dang
    # tin cay duy nhat: chinh file report.json da xuat hien + parse duoc -
    # kiem tra no MOI vong poll, KHONG cho $job.State.
    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    $lastCpu = -1
    $stuckPolls = 0
    $completedViaReportJson = $false
    $reportPeriodMismatch = $false
    $actualStart = $null
    $actualEnd = $null
    while ((Get-Date) -lt $deadline) {
        if (Test-Path $reportJsonPath) {
            # File co the dang duoc ghi do (partial) - thu parse, that bai
            # thi poll tiep vong sau thay vi ket luan vong ngay.
            try {
                $candidateSummary = Get-Content $reportJsonPath -Raw | ConvertFrom-Json
                $actualStart = [DateTimeOffset]::FromUnixTimeMilliseconds([long]$candidateSummary.main.testingPeriod.startDate).UtcDateTime
                $actualEnd = [DateTimeOffset]::FromUnixTimeMilliseconds([long]$candidateSummary.main.testingPeriod.endDate).UtcDateTime
                if ($actualStart.Date -ne $start.Date -or $actualEnd.Date -ne $end.Date) {
                    Write-Warning "report.json hop le nhung testingPeriod KHONG KHOP: yeu cau $($start.ToString('yyyy-MM-dd')) -> $($end.ToString('yyyy-MM-dd')), report $($actualStart.ToString('yyyy-MM-dd')) -> $($actualEnd.ToString('yyyy-MM-dd'))."
                    $reportPeriodMismatch = $true
                } else {
                    Write-Host "  [$( Get-Date -Format 'HH:mm:ss')] report.json hop le va testingPeriod khop yeu cau - COI LA XONG THAT."
                    $completedViaReportJson = $true
                }
                break
            } catch {
                # report.json dang ghi do, chua doc duoc - poll tiep.
            }
        }

        if ($job.State -in @("Completed", "Failed", "Stopped")) { break }

        $childProc = Get-CimInstance Win32_Process -Filter "Name='ctrader-cli.exe'" |
            Where-Object { $_.CommandLine -like "*$cbotsetPath*" }
        if ($childProc) {
            $proc = Get-Process -Id $childProc.ProcessId -ErrorAction SilentlyContinue
            if ($proc) {
                $cpu = $proc.CPU
                if ($cpu -le $lastCpu) { $stuckPolls++ } else { $stuckPolls = 0 }
                $lastCpu = $cpu
                Write-Host "  [$( Get-Date -Format 'HH:mm:ss')] job=$($job.State) CPU=$([math]::Round($cpu,1))s Mem=$([math]::Round($proc.WorkingSet64/1MB))MB"
                if ($stuckPolls -ge 6) {
                    # CHI la canh bao CPU dung yen - KHONG suy ra "treo" o day,
                    # vi CPU dung yen SAU KHI xong viec that (cho report.json
                    # xuat hien o vong poll ke tiep) trong giong het treo that
                    # su. Chi kill/timeout that su o cuoi vong lap (deadline).
                    Write-Warning "Khong thay CPU tang trong $($stuckPolls * $PollSeconds)s - dang cho xac nhan qua report.json truoc khi ket luan treo."
                }
            }
        }
        Start-Sleep -Seconds $PollSeconds
    }

    $timedOut = -not $completedViaReportJson -and -not $reportPeriodMismatch -and (Get-Date) -ge $deadline -and $job.State -notin @("Completed", "Failed", "Stopped")
    if ($timedOut) {
        Write-Warning "Vuot qua $TimeoutMinutes phut, report.json van chua xuat hien hop le - THAT SU treo, dang dung job va tien trinh con."
    }

    # Neu xong that qua report.json ma tien trinh khong tu exit, Receive-Job
    # -Wait se cho VO HAN (job.State khong bao gio "Completed") - chi -Wait
    # khi job that su da o trang thai ket thuc, con lai lay ngay buffer hien
    # co (-Keep, khong -Wait) roi xu ly dut diem ben duoi.
    if ($job.State -in @("Completed", "Failed", "Stopped")) {
        $output = Receive-Job -Job $job -Wait -AutoRemoveJob:$false -ErrorAction SilentlyContinue
    } else {
        $output = Receive-Job -Job $job -Keep -ErrorAction SilentlyContinue
    }
    $output | Out-String | Set-Content -Path $logPath -Encoding utf8

    Stop-Job -Job $job -ErrorAction SilentlyContinue
    Remove-Job -Job $job -Force -ErrorAction SilentlyContinue

    # Don dep tien trinh con neu no khong tu thoat - da gap that trong phien
    # nay (1 tien trinh in xong ket qua nhung khong tu exit).
    Get-CimInstance Win32_Process -Filter "Name='ctrader-cli.exe'" |
        Where-Object { $_.CommandLine -like "*$cbotsetPath*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

    $summary = $null
    if (Test-Path $reportJsonPath) {
        try {
            $summary = Get-Content $reportJsonPath -Raw | ConvertFrom-Json
            $actualStart = [DateTimeOffset]::FromUnixTimeMilliseconds([long]$summary.main.testingPeriod.startDate).UtcDateTime
            $actualEnd = [DateTimeOffset]::FromUnixTimeMilliseconds([long]$summary.main.testingPeriod.endDate).UtcDateTime
            $reportPeriodMismatch = $actualStart.Date -ne $start.Date -or $actualEnd.Date -ne $end.Date
            $completedViaReportJson = -not $reportPeriodMismatch
        } catch {
            $summary = $null
        }
    }
    if (-not $summary -and $output -match '\{[\s\S]*"Fitness"[\s\S]*\}') {
        # report-json khong duoc ghi (vd loi giua chung) nhung block JSON
        # tom tat van co trong console output - trich xuat du phong.
        try { $summary = ($output -join "`n" | Select-String -Pattern '\{[\s\S]*?"Fitness"\s*:\s*[\d.-]+[\s\S]*?\}').Matches[0].Value | ConvertFrom-Json } catch {}
    }

    $success = $completedViaReportJson -and -not $timedOut -and -not $reportPeriodMismatch -and ($output -notmatch "InvalidOperationException")

    $failureReason = $null
    if ($reportPeriodMismatch) {
        $failureReason = "ReportPeriodMismatch"
    } elseif ($timedOut) {
        $failureReason = "TimedOutWithoutValidReport"
    } elseif (-not (Test-Path $reportJsonPath)) {
        $failureReason = "ReportJsonMissing"
    } elseif ($output -match "InvalidOperationException") {
        $failureReason = "CliInvalidOperationException"
    }

    # Mot manifest gon, de cong cu downstream khong phai parse lai console
    # log hoac doan input tu ten folder. params.cbotset/report.json van la
    # raw artifact chinh; run-summary.json chi la lop index chuan hoa.
    $signalFileInfo = $null
    if ($Params.ContainsKey("SignalFilePath")) {
        $signalPath = [string]$Params["SignalFilePath"]
        if (Test-Path -LiteralPath $signalPath -PathType Leaf) {
            $signalItem = Get-Item -LiteralPath $signalPath
            $signalFileInfo = [ordered]@{
                Path          = $signalItem.FullName
                Length        = $signalItem.Length
                LastWriteTime = $signalItem.LastWriteTimeUtc.ToString("o")
                SHA256        = (Get-FileHash -LiteralPath $signalItem.FullName -Algorithm SHA256).Hash
            }
        } else {
            $signalFileInfo = [ordered]@{ Path = $signalPath; Missing = $true }
        }
    }

    $tradeItems = if ($summary -and $summary.history -and $summary.history.items) { @($summary.history.items) } else { @() }
    $runSummary = [ordered]@{
        SchemaVersion = 1
        CreatedAtUtc  = [DateTime]::UtcNow.ToString("o")
        Success       = [bool]$success
        FailureReason = $failureReason
        Input          = [ordered]@{
            AlgoPath      = $AlgoPath
            AlgoSHA256    = (Get-FileHash -LiteralPath $AlgoPath -Algorithm SHA256).Hash
            SymbolInput   = $Symbol
            ResolvedSymbol = $fullSymbol
            Period        = $Period
            StartDate     = $start.ToString("yyyy-MM-dd")
            EndDate       = $end.ToString("yyyy-MM-dd")
            DataMode      = $DataMode
            Broker        = $script:Broker
            Account       = $script:Account
            Parameters    = $Params
            SignalFile    = $signalFileInfo
        }
        Actual         = [ordered]@{
            StartDate = if ($actualStart) { $actualStart.ToString("yyyy-MM-dd") } else { $null }
            EndDate   = if ($actualEnd) { $actualEnd.ToString("yyyy-MM-dd") } else { $null }
        }
        Metrics        = if ($summary) {
            [ordered]@{
                StartingCapital       = $summary.main.startingCapital
                EndingBalance         = $summary.main.endingBalance
                NetProfit             = $summary.main.netProfit
                ROI                   = $summary.main.roi
                ProfitFactor          = $summary.tradeStatistics.profitFactor.all
                TotalTrades           = $summary.tradeStatistics.totalTrades.all
                WinningTrades         = $summary.tradeStatistics.winningTrades.all
                LosingTrades          = $summary.tradeStatistics.losingTrades.all
                MaxBalanceDrawdownPct = $summary.equity.maxBalanceDrawdownPercent
                MaxEquityDrawdownPct  = $summary.equity.maxEquityDrawdownPercent
                HistoryItems          = $tradeItems.Count
            }
        } else { $null }
        Artifacts      = [ordered]@{
            Params     = $cbotsetPath
            Command    = $commandPath
            Log        = $logPath
            ReportJson = $reportJsonPath
        }
    }
    $runSummary | ConvertTo-Json -Depth 10 | Set-Content -Path $runSummaryPath -Encoding utf8

    [PSCustomObject]@{
        Success        = [bool]$success
        OutputDir      = $outDir
        LogPath        = $logPath
        ReportJsonPath = $reportJsonPath
        RunSummaryPath = $runSummaryPath
        CommandPath    = $commandPath
        TimedOut       = $timedOut
        FailureReason  = $failureReason
        RequestedStart = $start.Date
        RequestedEnd   = $end.Date
        ActualStart    = if ($actualStart) { $actualStart.Date } else { $null }
        ActualEnd      = if ($actualEnd) { $actualEnd.Date } else { $null }
        Summary        = $summary
    }
}

function Get-CliBacktestMetrics {
    <#
    .SYNOPSIS
        Doc 1 folder cli_runs va tra ve 1 row metric chuan hoa de so sanh.
    #>
    param([Parameter(Mandatory)][string]$RunDirectory)

    $runDir = (Resolve-Path -LiteralPath $RunDirectory -ErrorAction Stop).Path
    $reportPath = Join-Path $runDir "report.json"
    if (-not (Test-Path -LiteralPath $reportPath -PathType Leaf)) {
        throw "Folder '$runDir' khong co report.json."
    }

    try {
        $report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
    } catch {
        throw "report.json tai '$runDir' khong parse duoc: $($_.Exception.Message)"
    }

    $start = [DateTimeOffset]::FromUnixTimeMilliseconds([long]$report.main.testingPeriod.startDate).UtcDateTime
    $end = [DateTimeOffset]::FromUnixTimeMilliseconds([long]$report.main.testingPeriod.endDate).UtcDateTime
    $totalTrades = [int]$report.tradeStatistics.totalTrades.all
    $winningTrades = [int]$report.tradeStatistics.winningTrades.all
    $paramMap = [ordered]@{}
    foreach ($parameter in @($report.parameters)) {
        $paramMap[$parameter.propertyName] = $parameter.value
    }

    [PSCustomObject]@{
        RunName                = Split-Path -Leaf $runDir
        Bot                    = $report.main.cBotName
        Symbol                 = $report.main.symbol
        Period                 = $report.main.period
        StartDate              = $start.ToString("yyyy-MM-dd")
        EndDate                = $end.ToString("yyyy-MM-dd")
        DataMode               = $report.main.data.type
        StartingCapital        = $report.main.startingCapital
        EndingBalance          = $report.main.endingBalance
        NetProfit              = $report.main.netProfit
        ROI                    = $report.main.roi
        ProfitFactor           = $report.tradeStatistics.profitFactor.all
        TotalTrades            = $totalTrades
        WinningTrades          = $winningTrades
        LosingTrades           = $report.tradeStatistics.losingTrades.all
        WinRatePct             = if ($totalTrades -gt 0) { [math]::Round(100 * $winningTrades / $totalTrades, 2) } else { 0 }
        MaxBalanceDrawdownPct  = $report.equity.maxBalanceDrawdownPercent
        MaxEquityDrawdownPct   = $report.equity.maxEquityDrawdownPercent
        Parameters             = ($paramMap | ConvertTo-Json -Compress)
        RunDirectory           = $runDir
        ReportJsonPath         = $reportPath
    }
}

function Export-CliBacktestComparison {
    <#
    .SYNOPSIS
        So sanh nhieu folder cli_runs va luu bang CSV/JSON/Markdown.

    .DESCRIPTION
        Ham chi so sanh ket qua performance tong hop. Fidelity cua chien
        luoc (signal -> dat lenh -> fill/reject/expire) can parser rieng doc
        log.txt + CSV vi semantics log cua Combo va MA Cross khac nhau.
    #>
    param(
        [Parameter(Mandatory)][string[]]$RunDirectories,
        [string]$Name = "comparison"
    )

    if ($RunDirectories.Count -lt 2) {
        throw "Can it nhat 2 run directory de so sanh."
    }

    $rows = @($RunDirectories | ForEach-Object { Get-CliBacktestMetrics -RunDirectory $_ })
    $comparisonFields = @("Bot", "Symbol", "Period", "StartDate", "EndDate", "DataMode", "StartingCapital")
    $differences = @()
    foreach ($field in $comparisonFields) {
        $values = @($rows | ForEach-Object { [string]$_.$field } | Select-Object -Unique)
        if ($values.Count -gt 1) { $differences += "$field=[$($values -join ', ')]" }
    }
    $comparable = $differences.Count -eq 0

    if (-not (Test-Path -LiteralPath $script:CliComparisonsRoot)) {
        New-Item -ItemType Directory -Path $script:CliComparisonsRoot | Out-Null
    }
    $safeName = $Name -replace '[^A-Za-z0-9._-]', '_'
    $base = Join-Path $script:CliComparisonsRoot "$($safeName)_$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    $outDir = $base
    $suffix = 1
    while (Test-Path -LiteralPath $outDir) {
        $outDir = "$base-$('{0:D2}' -f $suffix)"
        $suffix++
    }
    New-Item -ItemType Directory -Path $outDir | Out-Null

    $csvPath = Join-Path $outDir "comparison.csv"
    $jsonPath = Join-Path $outDir "comparison.json"
    $mdPath = Join-Path $outDir "comparison.md"
    $rows | Export-Csv -LiteralPath $csvPath -NoTypeInformation -Encoding utf8
    $payload = [ordered]@{
        Comparable        = $comparable
        DifferenceReasons = $differences
        RankedByNetProfit = @($rows | Sort-Object NetProfit -Descending)
    }
    $payload | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $jsonPath -Encoding utf8

    $md = @(
        "# CLI backtest comparison",
        "",
        "Generated UTC: $([DateTime]::UtcNow.ToString('o'))",
        "",
        $(if ($comparable) { "Comparable: **YES** (same bot/symbol/period/date/data/capital)." } else { "Comparable: **NO** - $($differences -join '; ')" }),
        "",
        "| Rank | Run | Net profit | ROI % | PF | Trades | Win % | Max equity DD % |",
        "|---:|---|---:|---:|---:|---:|---:|---:|"
    )
    $rank = 0
    foreach ($row in @($rows | Sort-Object NetProfit -Descending)) {
        $rank++
        $md += "| $rank | $($row.RunName) | $($row.NetProfit) | $($row.ROI) | $($row.ProfitFactor) | $($row.TotalTrades) | $($row.WinRatePct) | $([math]::Round([double]$row.MaxEquityDrawdownPct, 2)) |"
    }
    $md | Set-Content -LiteralPath $mdPath -Encoding utf8

    [PSCustomObject]@{
        Comparable        = $comparable
        DifferenceReasons = $differences
        OutputDirectory   = $outDir
        CsvPath           = $csvPath
        JsonPath          = $jsonPath
        MarkdownPath      = $mdPath
        Results           = $rows
    }
}

function Invoke-CliBacktestGrid {
    <#
    .SYNOPSIS
        Chay tuan tu Cartesian product cua nhieu tap tham so cBot.

    .DESCRIPTION
        ctrader-cli 5.9 khong co batch optimize. Ham nay dung lai
        Invoke-CliBacktest cho tung to hop, chi chay 1 ctrader-cli.exe tai
        mot thoi diem, va checkpoint grid-summary.json/csv sau moi run.
        Day la lop quet tham so In-Sample; logic chia rolling window va chon
        tham so OOS cua Walk-Forward van la lop rieng, khong tu suy dien o day.

    .PARAMETER BaseParams
        Tham so co dinh ap dung cho moi run, vd SignalFilePath.

    .PARAMETER ParameterGrid
        Hashtable: moi key la ten parameter, value la mang gia tri can quet.
        Vd @{ RiskPercent=@(0.5,1.0); KtpLevel=@(0,3,7) } tao 6 to hop.

    .PARAMETER MaxRuns
        Chan an toan de khong vo tinh tao grid qua lon. Default 100.

    .PARAMETER ContinueOnFailure
        Neu true (default), ghi nhan run loi va tiep tuc. Neu false, dung grid
        sau run dau tien khong Success.
    #>
    param(
        [Parameter(Mandatory)][string]$AlgoPath,
        [Parameter(Mandatory)][string]$Symbol,
        [Parameter(Mandatory)][string]$Period,
        [Parameter(Mandatory)]$StartDate,
        [Parameter(Mandatory)]$EndDate,
        [ValidateSet("Ticks", "M1")][string]$DataMode = "Ticks",
        [hashtable]$BaseParams = @{},
        [Parameter(Mandatory)][hashtable]$ParameterGrid,
        [int]$MaxRuns = 100,
        [int]$TimeoutMinutes = 60,
        [int]$PollSeconds = 20,
        [bool]$ContinueOnFailure = $true
    )

    if ($MaxRuns -lt 1) {
        throw "MaxRuns phai >= 1."
    }
    if ($ParameterGrid.Count -eq 0) {
        throw "ParameterGrid phai co it nhat 1 parameter."
    }

    # Sap xep key de thu tu to hop va artifact tai lap duoc.
    $gridKeys = @($ParameterGrid.Keys | Sort-Object)
    $combinations = @(@{})
    foreach ($key in $gridKeys) {
        $values = @($ParameterGrid[$key])
        if ($values.Count -eq 0 -or ($values.Count -eq 1 -and $null -eq $values[0])) {
            throw "ParameterGrid['$key'] khong co gia tri."
        }

        $expanded = @()
        foreach ($combination in $combinations) {
            foreach ($value in $values) {
                $copy = @{}
                foreach ($existingKey in $combination.Keys) {
                    $copy[$existingKey] = $combination[$existingKey]
                }
                $copy[$key] = $value
                $expanded += ,$copy
            }
        }
        $combinations = $expanded

        if ($combinations.Count -gt $MaxRuns) {
            throw "Parameter grid tao $($combinations.Count) to hop, vuot MaxRuns=$MaxRuns."
        }
    }

    if (-not (Test-Path -LiteralPath $script:CliBatchesRoot)) {
        New-Item -ItemType Directory -Path $script:CliBatchesRoot | Out-Null
    }

    $botName = [System.IO.Path]::GetFileNameWithoutExtension($AlgoPath)
    $safeSymbol = $Symbol -replace '[^A-Za-z0-9._-]', '_'
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $batchDirBase = Join-Path $script:CliBatchesRoot "$($botName)_$($safeSymbol)_$($Period)_grid_$timestamp"
    $batchDir = $batchDirBase
    $suffix = 1
    while (Test-Path -LiteralPath $batchDir) {
        $batchDir = "$batchDirBase-$('{0:D2}' -f $suffix)"
        $suffix++
    }
    New-Item -ItemType Directory -Path $batchDir | Out-Null

    $jsonPath = Join-Path $batchDir "grid-summary.json"
    $csvPath = Join-Path $batchDir "grid-summary.csv"
    $results = @()
    $runNumber = 0

    foreach ($combination in $combinations) {
        $runNumber++
        $params = @{}
        foreach ($key in $BaseParams.Keys) { $params[$key] = $BaseParams[$key] }
        foreach ($key in $combination.Keys) { $params[$key] = $combination[$key] }

        Write-Host "Grid run $runNumber/$($combinations.Count): $($params | ConvertTo-Json -Compress)"
        $runResult = $null
        $caughtError = $null
        try {
            $runResult = Invoke-CliBacktest `
                -AlgoPath $AlgoPath `
                -Symbol $Symbol `
                -Period $Period `
                -StartDate $StartDate `
                -EndDate $EndDate `
                -DataMode $DataMode `
                -Params $params `
                -TimeoutMinutes $TimeoutMinutes `
                -PollSeconds $PollSeconds
        } catch {
            $caughtError = $_.Exception.Message
        }

        $summary = if ($runResult) { $runResult.Summary } else { $null }
        $success = $null -ne $runResult -and $runResult.Success
        $failureReason = if ($caughtError) {
            "Exception: $caughtError"
        } elseif ($runResult) {
            $runResult.FailureReason
        } else {
            "UnknownFailure"
        }

        $results += [PSCustomObject]@{
            RunNumber            = $runNumber
            Success              = [bool]$success
            FailureReason        = $failureReason
            Parameters           = ($params | ConvertTo-Json -Compress)
            OutputDir            = if ($runResult) { $runResult.OutputDir } else { $null }
            NetProfit            = if ($summary) { $summary.main.netProfit } else { $null }
            ROI                  = if ($summary) { $summary.main.roi } else { $null }
            ProfitFactor         = if ($summary) { $summary.tradeStatistics.profitFactor.all } else { $null }
            TotalTrades          = if ($summary) { $summary.tradeStatistics.totalTrades.all } else { $null }
            MaxEquityDrawdownPct = if ($summary) { $summary.equity.maxEquityDrawdownPercent } else { $null }
        }

        # Checkpoint sau tung run de khong mat ket qua grid neu session dung.
        # Dung -InputObject de giu JSON la array ke ca khi grid moi co 1 row.
        ConvertTo-Json -InputObject $results -Depth 8 | Set-Content -Path $jsonPath -Encoding utf8
        $results | Export-Csv -Path $csvPath -NoTypeInformation -Encoding utf8

        if (-not $success -and -not $ContinueOnFailure) {
            break
        }
    }

    $successfulRows = @($results | Where-Object { $_.Success })
    $bestRun = $successfulRows | Sort-Object NetProfit -Descending | Select-Object -First 1

    [PSCustomObject]@{
        Success        = $results.Count -eq $combinations.Count -and @($results | Where-Object { -not $_.Success }).Count -eq 0
        BatchDir       = $batchDir
        JsonPath       = $jsonPath
        CsvPath        = $csvPath
        PlannedRuns    = $combinations.Count
        CompletedRuns  = $results.Count
        SuccessfulRuns = $successfulRows.Count
        FailedRuns     = @($results | Where-Object { -not $_.Success }).Count
        BestRun        = $bestRun
        Results        = $results
    }
}
