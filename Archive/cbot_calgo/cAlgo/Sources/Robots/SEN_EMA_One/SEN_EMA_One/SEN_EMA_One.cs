using System;
using System.Collections.Generic;
using System.Globalization;
using cAlgo.API;
using cAlgo.API.Internals;

namespace cAlgo.Robots
{
    // Bot nay chay 1 symbol duy nhat tren chart hien tai va doc tin hieu tu file CSV chung.
    [Robot(AccessRights = AccessRights.FullAccess, AddIndicators = true)]
    public class sen05_ema_one : Robot
    {
        // Duong dan file CSV dau vao theo format: bartime,symbol,signal[,atr].
        [Parameter("CSV File Path", DefaultValue = @"D:\Auto Trading\SEN05\raw_signal_exports\ma_cross_GOLD_M30_signals.csv")]
        public string CsvPath { get; set; }

        // Ten symbol trong CSV; de trong thi bot dung SymbolName cua chart hien tai.
        [Parameter("CSV Symbol Name", DefaultValue = "")]
        public string CsvSymbolName { get; set; }

        // Offset de canh gio CSV voi gio server/chart cua cTrader.
        [Parameter("CSV Time Offset Hours", DefaultValue = 7, MinValue = -12, MaxValue = 14, Step = 1)]
        public int CsvTimeOffsetHours { get; set; }

        // Lot cua lenh dau tien trong moi chuoi.
        [Parameter("Initial Lot", DefaultValue = 0.01, MinValue = 0.01, MaxValue = 100, Step = 0.01)]
        public double InitialLot { get; set; }

        // So lot cong them cho moi lenh tiep theo trong chuoi.
        [Parameter("Lot Add", DefaultValue = 0.01, MinValue = 0, MaxValue = 100, Step = 0.01)]
        public double LotAdd { get; set; }

        // So lenh toi da trong mot chuoi; theo yeu cau hien tai la toi da 5.
        [Parameter("Max Positions", DefaultValue = 5, MinValue = 1, MaxValue = 5, Step = 1)]
        public int MaxPositions { get; set; }

        // Muc lo toi da cua ca chuoi, tinh theo balance luc bat dau chuoi.
        [Parameter("Max Chain Loss %", DefaultValue = 2.0, MinValue = 0.1, MaxValue = 20.0, Step = 0.1)]
        public double MaxChainLossPercent { get; set; }

        // Dieu kien chot loi chuoi theo net PnL, tinh theo balance luc bat dau chuoi.
        [Parameter("Chain Profit Target %", DefaultValue = 2.0, MinValue = 0.1, MaxValue = 20.0, Step = 0.1)]
        public double ChainProfitTargetPercent { get; set; }

        // Label de bot chi quan ly dung lenh cua minh tren dung symbol.
        [Parameter("Trade Label", DefaultValue = "SEN05_ONE")]
        public string TradeLabel { get; set; }

        // Mot dong tin hieu da parse tu CSV.
        private sealed class SignalInfo
        {
            // Huong lenh sau khi map signal CSV: 1 = Buy, -1 = Sell.
            public TradeType Direction { get; set; }

            // ATR tu CSV duoc luu lai de log/doi chieu neu file co cot nay.
            public double? Atr { get; set; }
        }

        // Tin hieu cua 1 symbol sau khi da loc, key la thoi diem nen M30.
        private readonly Dictionary<DateTime, SignalInfo> _signals = new Dictionary<DateTime, SignalInfo>();

        // Ten symbol trong CSV ma bot dang su dung de loc tin hieu.
        private string _activeCsvSymbol;

        // Balance tai thoi diem chuoi bat dau, dung de tinh max loss va profit target cua chuoi.
        private double _chainStartBalance;

        // Co danh dau chuoi hien tai da co balance goc hay chua.
        private bool _chainActive;

        protected override void OnStart()
        {
            // Dang ky event de reset trang thai chuoi khi tat ca lenh da dong.
            Positions.Closed += OnPositionClosed;

            // Dam bao bot chi chay dung khung M30 vi CSV dau vao la M30.
            if (!ValidateTimeFrame())
                return;

            // In thong tin symbol/volume de de kiem tra min lot va mapping CSV.
            PrintStartupInfo();

            // Doc va loc tin hieu CSV cho duy nhat 1 symbol.
            LoadSignals();
        }

        protected override void OnBar()
        {
            // Can toi thieu 2 bar de co the lay nen vua dong bang Last(1).
            if (Bars.Count < 2)
                return;

            // OnBar chay khi bar moi mo, nen Last(1) la bar vua dong.
            var closedBarTime = Bars.OpenTimes.Last(1);

            // Cong offset de dua gio chart/server ve cung he quy chieu voi CSV.
            var signalTime = NormalizeSignalTime(closedBarTime.AddHours(CsvTimeOffsetHours));

            // Neu CSV khong co tin hieu tai thoi diem nay thi khong lam gi.
            if (!_signals.TryGetValue(signalTime, out var signal))
                return;

            // Log tin hieu tim thay de doi chieu voi file CSV khi backtest.
            var atrText = signal.Atr.HasValue ? signal.Atr.Value.ToString("F5", CultureInfo.InvariantCulture) : "n/a";
            Print($"Signal found: {_activeCsvSymbol} {signal.Direction} at {signalTime:yyyy-MM-dd HH:mm} | ATR: {atrText}");

            // Mo them 1 lenh vao chuoi neu chua vuot max positions.
            TryOpenChainOrder(signal, signalTime);
        }

        protected override void OnTick()
        {
            // Lay snapshot 1 lan moi tick de dung chung cho SL/TP chuoi.
            var positions = Positions.FindAll(TradeLabel, SymbolName);

            // Kiem tra SL cua chuoi truoc, vi day la risk guard quan trong nhat.
            if (CheckChainStopLoss(positions))
                return;

            // Neu chua cham SL chuoi, tiep tuc kiem tra dieu kien TP cua chuoi.
            CheckBasketTakeProfit(positions);
        }

        private bool ValidateTimeFrame()
        {
            // Neu chay nham timeframe, ket qua match signal M30 se khong dang tin.
            if (TimeFrame != TimeFrame.Minute30)
            {
                Print($"ERROR: This cBot expects M30. Current chart timeframe: {TimeFrame}.");
                Stop();
                return false;
            }

            return true;
        }

        private void PrintStartupInfo()
        {
            // Lay symbol CSV dang dung: neu user de trong thi bang SymbolName chart.
            _activeCsvSymbol = GetActiveCsvSymbol();

            // In mapping de tranh nham lan vi CSV co the ghi GOLD nhung chart broker la XAUUSD.
            Print($"Trading Symbol: {SymbolName} | CSV Symbol: {_activeCsvSymbol}");

            // In min/max volume theo lot de user biet lieu Initial Lot co hop le khong.
            Print($"Symbol min lot: {Symbol.VolumeInUnitsToQuantity(Symbol.VolumeInUnitsMin):F4} | max lot: {Symbol.VolumeInUnitsToQuantity(Symbol.VolumeInUnitsMax):F4}");

            // In mo ta quan ly chuoi hien tai.
            Print($"Chain setup: InitialLot={InitialLot:F2}, LotAdd={LotAdd:F2}, MaxPositions={MaxPositions}, MaxLoss={MaxChainLossPercent:F1}%, ChainProfit={ChainProfitTargetPercent:F1}%.");
        }

        private void LoadSignals()
        {
            // Xoa tin hieu cu de moi lan start deu doc lai file sach.
            _signals.Clear();

            // Dam bao _activeCsvSymbol da co gia tri neu LoadSignals bi goi rieng.
            _activeCsvSymbol = GetActiveCsvSymbol();

            // Cho phep user paste path co dau ngoac kep trong UI cTrader.
            var csvPath = NormalizeCsvPath(CsvPath);

            // Neu file khong ton tai, dung bot ngay de tranh backtest rong.
            if (!System.IO.File.Exists(csvPath))
            {
                Print("ERROR: CSV file not found: " + csvPath);
                Stop();
                return;
            }

            // Bien dem so dong hop le cua symbol dang chay.
            var loaded = 0;

            // Bien dem dong bi bo qua do loi format/du lieu.
            var skipped = 0;

            // Bien dem dong cua symbol khac, chi de log.
            var otherSymbols = 0;

            // Danh sach symbol co trong CSV de log khi user map sai ten symbol.
            var detectedSymbols = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

            // Doc tung dong CSV; file hien tai don gian nen split dau phay la du.
            foreach (var line in System.IO.File.ReadAllLines(csvPath))
            {
                // Bo qua dong trong.
                if (string.IsNullOrWhiteSpace(line))
                    continue;

                // Tach cac cot: bartime, symbol, signal, atr tuy chon.
                var parts = line.Split(',');

                // Dong nao thieu cot thi coi la invalid.
                if (parts.Length < 3)
                {
                    skipped++;
                    continue;
                }

                // Bo qua header.
                if (parts[0].Trim().Equals("bartime", StringComparison.OrdinalIgnoreCase))
                    continue;

                // Cot symbol trong CSV.
                var csvSymbol = parts[1].Trim();
                detectedSymbols.Add(csvSymbol);

                // Bot one-symbol chi lay dung symbol dang chon.
                if (!csvSymbol.Equals(_activeCsvSymbol, StringComparison.OrdinalIgnoreCase))
                {
                    otherSymbols++;
                    continue;
                }

                // Parse thoi gian tin hieu; uu tien format CSV moi co giay.
                if (!TryParseSignalTime(parts[0].Trim(), out var signalTime))
                {
                    skipped++;
                    continue;
                }

                // Parse signal: 1 = Buy, -1 = Sell.
                if (!int.TryParse(parts[2].Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out var signalValue) ||
                    (signalValue != 1 && signalValue != -1))
                {
                    skipped++;
                    continue;
                }

                // Parse ATR neu CSV co cot nay; file 3 cot van hop le.
                double? atr = null;
                if (parts.Length >= 4 && !string.IsNullOrWhiteSpace(parts[3]))
                {
                    if (!double.TryParse(parts[3].Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out var parsedAtr))
                    {
                        skipped++;
                        continue;
                    }

                    atr = parsedAtr;
                }

                // Normalize time de key khong bi le giay/millisecond.
                var normalizedTime = NormalizeSignalTime(signalTime);

                // Luu tin hieu, neu trung thoi diem thi dong sau ghi de dong truoc.
                _signals[normalizedTime] = new SignalInfo
                {
                    Direction = signalValue == 1 ? TradeType.Buy : TradeType.Sell,
                    Atr = atr
                };

                // Tang so tin hieu da load cho symbol nay.
                loaded++;
            }

            // Khong co tin hieu cho symbol nay thi dung bot de user sua mapping.
            if (loaded == 0)
            {
                Print($"ERROR: No CSV signals loaded for symbol '{_activeCsvSymbol}'. Check CSV Symbol Name and input file. Detected CSV symbols: {string.Join(", ", detectedSymbols)}.");
                Stop();
                return;
            }

            // Log tong ket viec load CSV.
            Print($"Loaded {loaded} CSV signals for {_activeCsvSymbol}. Ignored other symbols: {otherSymbols}. Skipped invalid rows: {skipped}. CSV offset: UTC{CsvTimeOffsetHours:+#;-#;0}. File: {csvPath}");
        }

        private string NormalizeCsvPath(string path)
        {
            // cTrader parameter fields sometimes keep quotes if pasted from docs/chat.
            return string.IsNullOrWhiteSpace(path) ? string.Empty : path.Trim().Trim('"');
        }

        private string GetActiveCsvSymbol()
        {
            // Neu user khong nhap CSV Symbol Name thi dung SymbolName cua chart.
            var csvSymbol = string.IsNullOrWhiteSpace(CsvSymbolName) ? SymbolName : CsvSymbolName.Trim();

            // Doi ve uppercase de so sanh symbol on dinh hon.
            return csvSymbol.ToUpper(CultureInfo.InvariantCulture);
        }

        private bool TryParseSignalTime(string text, out DateTime signalTime)
        {
            // Format chinh cua CSV moi: yyyy-MM-dd HH:mm:ss.
            return DateTime.TryParseExact(text, "yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture, DateTimeStyles.None, out signalTime) ||
                   // Giu them format cu de bot van doc duoc file 2 cot/khong co giay neu can test nhanh.
                   DateTime.TryParseExact(text, "yyyy-MM-dd HH:mm", CultureInfo.InvariantCulture, DateTimeStyles.None, out signalTime);
        }

        private DateTime NormalizeSignalTime(DateTime time)
        {
            // Bo giay/millisecond de match theo phut cua bar M30.
            return new DateTime(time.Year, time.Month, time.Day, time.Hour, time.Minute, 0);
        }

        private void TryOpenChainOrder(SignalInfo signal, DateTime signalTime)
        {
            // Lay tat ca lenh cua bot tren symbol hien tai.
            var positions = Positions.FindAll(TradeLabel, SymbolName);

            // Neu chuoi da du so lenh toi da thi bo qua signal moi.
            if (positions.Length >= MaxPositions)
            {
                Print($"Skip {signal.Direction} at {signalTime:yyyy-MM-dd HH:mm}: max positions reached ({positions.Length}/{MaxPositions}).");
                return;
            }

            // Ghi nhan balance truoc khi gui lenh dau tien cua chuoi.
            var isFirstOrder = positions.Length == 0;
            var chainStartBalance = Account.Balance;

            // chainIndex la so thu tu lenh trong chuoi, bat dau tu 1.
            var chainIndex = positions.Length + 1;

            // Lot cong dan: lenh 1 = InitialLot, lenh 2 = InitialLot + LotAdd, ...
            var lot = CalculateLot(chainIndex);

            // Doi lot sang volume units theo quy tac cua symbol hien tai.
            var volumeInUnits = LotToVolumeInUnits(lot);

            // Neu volume khong hop le thi khong vao lenh.
            if (volumeInUnits <= 0)
            {
                Print($"ERROR: Invalid volume for chain order #{chainIndex}. Requested lot: {lot:F2}.");
                return;
            }

            // Mo market order KHONG co SL/TP rieng, vi SL/TP duoc quan ly theo chuoi.
            var result = ExecuteMarketOrder(signal.Direction, SymbolName, volumeInUnits, TradeLabel);

            // Neu broker khop lenh thanh cong thi cap nhat trang thai chuoi.
            if (result.IsSuccessful)
            {
                // Neu day la lenh dau tien, bat dau chuoi va luu balance goc.
                if (isFirstOrder)
                    StartNewChain(chainStartBalance);

                // Log lenh vua mo voi chain index ro rang.
                Print($"Opened {signal.Direction} order #{chainIndex}/{MaxPositions} | Lot: {lot:F2} | Volume: {volumeInUnits} units.");
            }
            else
            {
                // Broker co the tu choi lenh do volume, market closed, margin, v.v.
                Print($"ERROR opening order #{chainIndex}: {result.Error}");
            }
        }

        private double CalculateLot(int chainIndex)
        {
            // Cong thuc lot: InitialLot + LotAdd * (thu_tu_lenh - 1).
            return InitialLot + LotAdd * (chainIndex - 1);
        }

        private double LotToVolumeInUnits(double lot)
        {
            // Doi quantity lot sang volume units cua cTrader.
            var rawVolume = Symbol.QuantityToVolumeInUnits(lot);

            // Lam tron xuong de khong trade lon hon lot user yeu cau.
            var volume = Symbol.NormalizeVolumeInUnits(rawVolume, RoundingMode.Down);

            // Neu sau khi lam tron ma nho hon min volume thi skip, khong tu nang len min.
            if (volume < Symbol.VolumeInUnitsMin)
            {
                Print($"ERROR: Requested lot {lot:F2} is below symbol minimum lot {Symbol.VolumeInUnitsToQuantity(Symbol.VolumeInUnitsMin):F4}.");
                return 0;
            }

            // Neu volume vuot qua max symbol thi skip.
            if (volume > Symbol.VolumeInUnitsMax)
            {
                Print($"ERROR: Requested volume {volume} is above symbol maximum {Symbol.VolumeInUnitsMax}.");
                return 0;
            }

            // Tra ve volume hop le de ExecuteMarketOrder.
            return volume;
        }

        private void StartNewChain(double chainStartBalance)
        {
            // Balance goc cua chuoi duoc chot tai luc lenh dau tien mo thanh cong.
            _chainStartBalance = chainStartBalance;

            // Danh dau chuoi dang active de cac guard dung cung 1 balance goc.
            _chainActive = true;

            // Log balance goc va nguong SL/TP cua chuoi.
            Print($"New chain started. Start balance: {_chainStartBalance:F2} | Max loss: {GetMaxChainLossMoney():F2} | Chain profit target: {GetChainProfitTargetMoney():F2}.");
        }

        private void EnsureChainState(Position[] positions)
        {
            // Neu bot restart/backtest resume khi da co lenh, khong biet balance goc that nen dung balance hien tai.
            if (!_chainActive && positions.Length > 0)
            {
                _chainStartBalance = Account.Balance;
                _chainActive = true;
                Print($"Chain state restored from open positions. Start balance assumed: {_chainStartBalance:F2}.");
            }
        }

        private bool CheckChainStopLoss(Position[] positions)
        {
            // Khong co lenh thi khong co gi de stop.
            if (positions.Length == 0)
                return false;

            // Dam bao co balance goc de tinh nguong lo.
            EnsureChainState(positions);

            // Tinh net PnL floating cua ca chuoi.
            var netPnL = GetNetPnL(positions);

            // Nguong lo toi da cua chuoi tinh theo balance luc bat dau chuoi.
            var maxLoss = GetMaxChainLossMoney();

            // Neu lo floating cham/vuot nguong thi dong tat ca lenh.
            if (netPnL <= -maxLoss)
            {
                Print($"Max chain loss reached. Net PnL: {netPnL:F2} | Limit: {-maxLoss:F2}. Closing all positions.");
                CloseAll();
                return true;
            }

            // Chua cham stop chuoi.
            return false;
        }

        private void CheckBasketTakeProfit(Position[] positions)
        {
            // Khong co lenh thi khong can chot.
            if (positions.Length == 0)
                return;

            // Dam bao co balance goc de tinh profit target.
            EnsureChainState(positions);

            // Tinh net PnL floating cua ca chuoi.
            var netPnL = GetNetPnL(positions);

            // Neu net am hoac bang 0 thi chua chot loi.
            if (netPnL <= 0)
                return;

            // Chot chuoi khi net PnL dat muc loi muc tieu theo % balance dau chuoi.
            if (netPnL >= GetChainProfitTargetMoney())
            {
                Print($"Chain profit target reached. Net PnL: {netPnL:F2} | Target: {GetChainProfitTargetMoney():F2}.");
                CloseAll();
            }
        }

        private double GetMaxChainLossMoney()
        {
            // So tien toi da duoc phep lo cho chuoi.
            return _chainStartBalance * MaxChainLossPercent / 100.0;
        }

        private double GetChainProfitTargetMoney()
        {
            // So tien loi muc tieu cua ca chuoi theo net PnL.
            return _chainStartBalance * ChainProfitTargetPercent / 100.0;
        }

        private double GetNetPnL(IEnumerable<Position> positions)
        {
            // Tong PnL floating cua tat ca lenh trong chuoi.
            double netPnL = 0;

            // Cong NetProfit tung position.
            foreach (var position in positions)
                netPnL += position.NetProfit;

            // Tra ve net PnL.
            return netPnL;
        }

        private void CloseAll()
        {
            // Lay snapshot cac lenh hien tai cua bot.
            var positions = Positions.FindAll(TradeLabel, SymbolName);

            // Dong tung lenh trong chuoi.
            foreach (var position in positions)
            {
                // Gui lenh close position.
                var result = ClosePosition(position);

                // Log thanh cong.
                if (result.IsSuccessful)
                    Print($"Closed {position.TradeType} {position.VolumeInUnits} units | PnL: {position.NetProfit:F2}");
                // Log loi neu broker/engine tu choi close.
                else
                    Print($"ERROR closing order: {result.Error}");
            }

            // Neu khong con lenh nao cua bot thi reset chuoi.
            if (Positions.FindAll(TradeLabel, SymbolName).Length == 0)
                ResetChainState();
        }

        private void ResetChainState()
        {
            // Xoa trang thai active cua chuoi.
            _chainActive = false;

            // Reset balance goc ve 0 de tranh dung lai nham chuoi cu.
            _chainStartBalance = 0;

            // Log reset de de doc backtest journal.
            Print("Chain reset.");
        }

        private void OnPositionClosed(PositionClosedEventArgs args)
        {
            // Chi xu ly lenh cua dung bot va dung symbol hien tai.
            if (args.Position.Label != TradeLabel || args.Position.SymbolName != SymbolName)
                return;

            // Neu tat ca lenh trong chuoi da dong thi reset state.
            if (Positions.FindAll(TradeLabel, SymbolName).Length == 0)
                ResetChainState();
        }

        protected override void OnStop()
        {
            // Go event handler de giu lifecycle sach.
            Positions.Closed -= OnPositionClosed;

            // Log bot stop.
            Print("cBot stopped.");
        }
    }
}
