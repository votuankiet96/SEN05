// AiTrendExecutor.cs — cTrader cAlgo cBot
//
// Polls the Python signal_server.py for AI Trend M45 entry signals and places
// market orders with SL/TP sized to a fixed risk-per-trade.
//
// === SETUP ===
// 1. Start signal_server.py on the trading machine (ops/run_signal_server.ps1)
// 2. Open a BTCUSD chart in cTrader (Demo account first)
// 3. Attach this cBot to the chart
// 4. Leave DryRun = true until you have verified sizing in the Journal
// 5. Flip DryRun = false only when satisfied with Demo results
//
// === HOW IT WORKS ===
// OnTimer() fires every PollIntervalSeconds (default 5s).
// → GET /api/pending-signals from signal_server.py
// → For each signal matching SymbolFilter:
//     1. Skip if signal is too old (> MaxSignalAgeMinutes)
//     2. Validate SL and entry_ref are present
//     3. Calculate volume via fixed-risk formula (see CalculateVolume())
//     4. DryRun=true  → Print to Journal, ACK signal, NO order placed
//     5. DryRun=false → ExecuteMarketOrder(), then ACK signal
//
// === LOT SIZING ===
// volume_units = (Account.Balance * RiskPct/100) / (sl_pips * Symbol.PipValue)
// Clamped to [VolumeInUnitsMin, VolumeInUnitsMax] and rounded down to step.
//
// === EXPAND TO MORE SYMBOLS ===
// Run a second cBot instance on another chart (e.g. EURUSD).
// Set SymbolFilter = "EURUSD" on that instance.
// signal_server.py already serves all symbols — no Python changes needed.
//
// Requires: cTrader 4.7+ (.NET 6), AccessRights.FullAccess (for HTTP calls)

using System;
using System.Collections.Generic;
using System.Net;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using cAlgo.API;

namespace cAlgo.Robots
{
    [Robot(Name = "AI Trend Executor", Version = "1.0.0", AccessRights = AccessRights.FullAccess)]
    public class AiTrendExecutor : Robot
    {
        // ----------------------------------------------------------------
        // Parameters
        // ----------------------------------------------------------------

        [Parameter("Signal Server URL", Group = "Connection", DefaultValue = "http://127.0.0.1:5050")]
        public string SignalServerUrl { get; set; }

        [Parameter("Poll Interval (seconds)", Group = "Connection", DefaultValue = 5, MinValue = 2, MaxValue = 60)]
        public int PollIntervalSeconds { get; set; }

        [Parameter("Symbol Filter", Group = "Signal", DefaultValue = "BTCUSD")]
        public string SymbolFilter { get; set; }

        [Parameter("Max Signal Age (minutes)", Group = "Signal", DefaultValue = 90, MinValue = 10, MaxValue = 360)]
        public int MaxSignalAgeMinutes { get; set; }

        [Parameter("Risk Per Trade %", Group = "Risk", DefaultValue = 1.0, MinValue = 0.1, MaxValue = 5.0, Step = 0.1)]
        public double RiskPerTradePct { get; set; }

        [Parameter("Label Prefix", Group = "Risk", DefaultValue = "AI_TREND")]
        public string LabelPrefix { get; set; }

        [Parameter("Dry Run (no real orders)", Group = "Safety", DefaultValue = true)]
        public bool DryRun { get; set; }

        // ----------------------------------------------------------------
        // Lifecycle
        // ----------------------------------------------------------------

        protected override void OnStart()
        {
            Print("=== AI Trend Executor started ===");
            Print($"Symbol filter : {SymbolFilter}");
            Print($"Risk per trade: {RiskPerTradePct}%");
            Print($"Signal TTL    : {MaxSignalAgeMinutes} min");
            Print($"Poll interval : {PollIntervalSeconds}s");
            Print($"Signal server : {SignalServerUrl}");

            if (DryRun)
                Print("*** DRY RUN MODE — monitoring only, no real orders will be placed ***");
            else
                Print("*** LIVE MODE — real orders will be placed on DEMO account ***");

            Timer.Start(PollIntervalSeconds);
        }

        protected override void OnTimer()
        {
            try
            {
                PollAndProcess();
            }
            catch (Exception ex)
            {
                Print($"[ERROR] Poll cycle failed: {ex.GetType().Name}: {ex.Message}");
            }
        }

        protected override void OnStop()
        {
            Print("AI Trend Executor stopped.");
        }

        // ----------------------------------------------------------------
        // Poll + process
        // ----------------------------------------------------------------

        private void PollAndProcess()
        {
            string json = FetchPendingSignals();
            if (string.IsNullOrWhiteSpace(json))
                return;

            List<SignalDto> signals;
            try
            {
                signals = JsonSerializer.Deserialize<List<SignalDto>>(json,
                    new JsonSerializerOptions { PropertyNameCaseInsensitive = true });
            }
            catch (JsonException ex)
            {
                Print($"[ERROR] JSON parse failed: {ex.Message}");
                return;
            }

            if (signals == null || signals.Count == 0)
                return;

            foreach (var signal in signals)
                ProcessSignal(signal);
        }

        private void ProcessSignal(SignalDto signal)
        {
            // 1. Symbol filter — ACK silently if this instance is not for this symbol
            if (!signal.symbol.Equals(SymbolFilter, StringComparison.OrdinalIgnoreCase))
            {
                AckSignal(signal.id);
                return;
            }

            // 2. Age check — stale signals must not be acted on
            if (!TryParseUtc(signal.created_at, out DateTime createdAt))
            {
                Print($"[SKIP] {signal.id}: cannot parse created_at='{signal.created_at}'");
                AckSignal(signal.id);
                return;
            }
            double ageMinutes = (DateTime.UtcNow - createdAt).TotalMinutes;
            if (ageMinutes > MaxSignalAgeMinutes)
            {
                Print($"[SKIP] {signal.id}: signal too old ({ageMinutes:F0} min > {MaxSignalAgeMinutes} min)");
                AckSignal(signal.id);
                return;
            }

            // 3. Validate required fields
            if (signal.sl_price == null)
            {
                Print($"[SKIP] {signal.id}: sl_price is null — cannot size risk");
                AckSignal(signal.id);
                return;
            }
            if (signal.entry_ref == null)
            {
                Print($"[SKIP] {signal.id}: entry_ref is null");
                AckSignal(signal.id);
                return;
            }

            double entryRef  = signal.entry_ref.Value;
            double slPrice   = signal.sl_price.Value;
            double slDistancePrice = Math.Abs(entryRef - slPrice);

            if (slDistancePrice <= 0)
            {
                Print($"[SKIP] {signal.id}: SL distance = 0");
                AckSignal(signal.id);
                return;
            }

            // 4. Trade direction
            var tradeType = signal.side.Equals("BUY", StringComparison.OrdinalIgnoreCase)
                ? TradeType.Buy
                : TradeType.Sell;

            // 5. Risk-based volume
            double volumeUnits = CalculateVolume(slDistancePrice);
            if (volumeUnits <= 0)
            {
                Print($"[SKIP] {signal.id}: calculated volume = 0 (balance too low or SL too wide?)");
                AckSignal(signal.id);
                return;
            }

            // 6. SL/TP in pips
            double slPips = slDistancePrice / Symbol.PipSize;
            double? tpPips = null;
            if (signal.tp_price.HasValue)
                tpPips = Math.Abs(entryRef - signal.tp_price.Value) / Symbol.PipSize;

            // Unique label — last 12 chars of signal id to stay within cTrader label limit
            string label = $"{LabelPrefix}_{signal.id.Substring(Math.Max(0, signal.id.Length - 12))}";

            // 7. Execute or log (DryRun)
            if (DryRun)
            {
                double lots = volumeUnits / Symbol.LotSize;
                Print(
                    $"[DRY RUN] Would place: {tradeType} {lots:F2} lots {SymbolFilter} | " +
                    $"SL={slPrice:F2} ({slPips:F1} pips) TP={signal.tp_price?.ToString("F2") ?? "-"} | " +
                    $"Risk={RiskPerTradePct}% = {Account.Balance * RiskPerTradePct / 100:F2} {Account.Currency} | " +
                    $"Label={label}"
                );
                AckSignal(signal.id);
                return;
            }

            // Live order
            var result = ExecuteMarketOrder(tradeType, SymbolName, volumeUnits, label, slPips, tpPips);
            if (result.IsSuccessful)
            {
                double lots = volumeUnits / Symbol.LotSize;
                Print(
                    $"[ORDER] Placed {tradeType} {lots:F2} lots | " +
                    $"Entry={result.Position?.EntryPrice:F5} SL={slPrice:F2} TP={signal.tp_price?.ToString("F2") ?? "-"} | " +
                    $"Label={label}"
                );
            }
            else
            {
                Print($"[ERROR] Order failed: {result.Error} | Signal={signal.id}");
            }

            AckSignal(signal.id);
        }

        // ----------------------------------------------------------------
        // Risk-based volume calculation
        // ----------------------------------------------------------------

        private double CalculateVolume(double slDistanceInPrice)
        {
            double riskAmount   = Account.Balance * (RiskPerTradePct / 100.0);
            double slPips       = slDistanceInPrice / Symbol.PipSize;

            // Symbol.PipValue is value of 1 pip per 1 unit in account currency.
            // Total pip value for N units = N * slPips * Symbol.PipValue
            // We want: riskAmount = units * slPips * Symbol.PipValue
            if (slPips <= 0 || Symbol.PipValue <= 0)
                return 0;

            double rawUnits = riskAmount / (slPips * Symbol.PipValue);

            // Normalise to valid step, clamp to broker min/max
            double normalised = Symbol.NormalizeVolumeInUnits(rawUnits, RoundingMode.Down);
            normalised = Math.Max(Symbol.VolumeInUnitsMin,
                         Math.Min(Symbol.VolumeInUnitsMax, normalised));
            return normalised;
        }

        // ----------------------------------------------------------------
        // HTTP helpers
        // ----------------------------------------------------------------

        private string FetchPendingSignals()
        {
            try
            {
                using var client = new WebClient { Encoding = Encoding.UTF8 };
                return client.DownloadString($"{SignalServerUrl}/api/pending-signals");
            }
            catch (WebException ex)
            {
                Print($"[WARN] Cannot reach signal server: {ex.Message}");
                return null;
            }
        }

        private void AckSignal(string signalId)
        {
            try
            {
                using var client = new WebClient { Encoding = Encoding.UTF8 };
                client.Headers[HttpRequestHeader.ContentType] = "application/json";
                client.UploadString(
                    $"{SignalServerUrl}/api/ack/{Uri.EscapeDataString(signalId)}",
                    "POST",
                    "{}"
                );
            }
            catch (Exception ex)
            {
                Print($"[WARN] ACK failed for {signalId}: {ex.Message}");
            }
        }

        // ----------------------------------------------------------------
        // Utilities
        // ----------------------------------------------------------------

        private static bool TryParseUtc(string value, out DateTime result)
        {
            result = default;
            if (string.IsNullOrEmpty(value))
                return false;
            return DateTime.TryParse(
                value,
                null,
                System.Globalization.DateTimeStyles.RoundtripKind |
                System.Globalization.DateTimeStyles.AssumeUniversal,
                out result
            );
        }
    }

    // ----------------------------------------------------------------
    // Signal DTO — maps to JSON from signal_server.py
    // ----------------------------------------------------------------

    public class SignalDto
    {
        [JsonPropertyName("id")]         public string id         { get; set; }
        [JsonPropertyName("state_key")]  public string state_key  { get; set; }
        [JsonPropertyName("symbol")]     public string symbol     { get; set; }
        [JsonPropertyName("side")]       public string side       { get; set; }
        [JsonPropertyName("entry_ref")]  public double? entry_ref { get; set; }
        [JsonPropertyName("sl_price")]   public double? sl_price  { get; set; }
        [JsonPropertyName("tp_price")]   public double? tp_price  { get; set; }
        [JsonPropertyName("bar_time")]   public string bar_time   { get; set; }
        [JsonPropertyName("created_at")] public string created_at { get; set; }
    }
}
