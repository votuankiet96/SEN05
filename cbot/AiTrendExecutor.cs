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
            // 1. Symbol filter — ACK silently if not for this cBot instance
            if (string.IsNullOrEmpty(signal.symbol) ||
                !signal.symbol.Equals(SymbolFilter, StringComparison.OrdinalIgnoreCase))
            {
                AckSignal(signal.id);
                return;
            }

            // 2. Validate side EXPLICITLY — never infer direction from a default branch
            //    An ambiguous side must be rejected, not silently treated as SELL.
            bool isBuy;
            if (signal.side != null && signal.side.Equals("BUY", StringComparison.OrdinalIgnoreCase))
                isBuy = true;
            else if (signal.side != null && signal.side.Equals("SELL", StringComparison.OrdinalIgnoreCase))
                isBuy = false;
            else
            {
                Print($"[SKIP] {signal.id}: invalid side='{signal.side}' — expected BUY or SELL");
                AckSignal(signal.id);
                return;
            }
            var tradeType = isBuy ? TradeType.Buy : TradeType.Sell;

            // 3. Age check — stale signals must not be acted on
            if (!TryParseUtc(signal.created_at, out DateTime createdAt))
            {
                Print($"[SKIP] {signal.id}: cannot parse created_at='{signal.created_at}'");
                AckSignal(signal.id);
                return;
            }
            double ageMinutes = (DateTime.UtcNow - createdAt).TotalMinutes;
            if (ageMinutes > MaxSignalAgeMinutes)
            {
                Print($"[SKIP] {signal.id}: too old ({ageMinutes:F0} min > {MaxSignalAgeMinutes} min)");
                AckSignal(signal.id);
                return;
            }

            // 4. Validate required numeric fields
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

            double entryRef        = signal.entry_ref.Value;
            double slPrice         = signal.sl_price.Value;
            double slDistancePrice = Math.Abs(entryRef - slPrice);

            if (slDistancePrice <= 0)
            {
                Print($"[SKIP] {signal.id}: SL distance = 0");
                AckSignal(signal.id);
                return;
            }

            // 5. Deterministic label — used for duplicate guard and position tracking
            string label = BuildLabel(signal.id);

            // 6. Duplicate guard — if a position with this label already exists, a previous
            //    execution succeeded but the ACK HTTP call failed. Consuming the signal now
            //    prevents placing a second order for the same signal bar.
            var existingPosition = Positions.Find(label);
            if (existingPosition != null)
            {
                Print($"[SKIP] {signal.id}: position '{label}' already open — duplicate guard, ACKing");
                AckSignal(signal.id);
                return;
            }

            // 7. Risk-based volume (warns if clamped to minimum)
            double volumeUnits = CalculateVolume(slDistancePrice);
            if (volumeUnits <= 0)
            {
                Print($"[SKIP] {signal.id}: volume = 0 (balance too low for this SL distance?)");
                AckSignal(signal.id);
                return;
            }

            // 8. SL/TP in pips
            double slPips  = slDistancePrice / Symbol.PipSize;
            double? tpPips = signal.tp_price.HasValue
                ? Math.Abs(entryRef - signal.tp_price.Value) / Symbol.PipSize
                : (double?)null;

            // 9. DryRun gate — default true, must be explicitly disabled
            if (DryRun)
            {
                double lots = volumeUnits / Symbol.LotSize;
                Print(
                    $"[DRY RUN] Would place: {tradeType} {lots:F3} lots {SymbolFilter} | " +
                    $"SL={slPrice:F2} ({slPips:F1} pips) TP={signal.tp_price?.ToString("F2") ?? "-"} | " +
                    $"Risk={RiskPerTradePct}% = {Account.Balance * RiskPerTradePct / 100:F2} {Account.Currency} | " +
                    $"Label={label}"
                );
                AckSignal(signal.id);
                return;
            }

            // 10. Margin pre-check (informational — broker is the final authority)
            double estMargin = (volumeUnits / Symbol.LotSize) * Symbol.InitialMargin;
            if (estMargin > Account.FreeMargin)
                Print($"[WARN] {signal.id}: est. margin {estMargin:F2} > free margin {Account.FreeMargin:F2} — order may be rejected");

            // 11. Place live market order.
            //     ACK regardless of outcome — on failure, consuming the signal avoids
            //     infinite retry loops. The error is logged prominently for manual review.
            var result = ExecuteMarketOrder(tradeType, SymbolName, volumeUnits, label, slPips, tpPips);
            if (result.IsSuccessful)
            {
                double lots = volumeUnits / Symbol.LotSize;
                Print(
                    $"[ORDER OK] {tradeType} {lots:F3} lots | " +
                    $"Entry={result.Position?.EntryPrice:F5} SL={slPrice:F2} " +
                    $"TP={signal.tp_price?.ToString("F2") ?? "-"} | Label={label}"
                );
            }
            else
            {
                Print($"!!! [ORDER FAILED] {result.Error} | {tradeType} {SymbolName} | Signal={signal.id}");
                Print($"!!! Signal ACKed (consumed). Verify account state manually — no automatic retry.");
            }

            AckSignal(signal.id);
        }

        private string BuildLabel(string signalId)
        {
            // Last 12 chars of the signal id — stays within cTrader's label length limit.
            // Deterministic: same signal always produces the same label (used by duplicate guard).
            return $"{LabelPrefix}_{signalId.Substring(Math.Max(0, signalId.Length - 12))}";
        }

        // ----------------------------------------------------------------
        // Risk-based volume calculation
        // ----------------------------------------------------------------

        private double CalculateVolume(double slDistanceInPrice)
        {
            double riskAmount = Account.Balance * (RiskPerTradePct / 100.0);
            double slPips     = slDistanceInPrice / Symbol.PipSize;

            // Symbol.PipValue: value of 1 pip per 1 unit in account currency.
            // riskAmount = units * slPips * Symbol.PipValue  →  units = riskAmount / (slPips * PipValue)
            if (slPips <= 0 || Symbol.PipValue <= 0)
                return 0;

            double rawUnits   = riskAmount / (slPips * Symbol.PipValue);
            double normalised = Symbol.NormalizeVolumeInUnits(rawUnits, RoundingMode.Down);

            if (normalised < Symbol.VolumeInUnitsMin)
            {
                // Clamping to minimum means actual risk EXCEEDS configured risk %.
                // Warn explicitly so operator knows the sizing is not as configured.
                double actualRisk    = Symbol.VolumeInUnitsMin * slPips * Symbol.PipValue;
                double actualRiskPct = Account.Balance > 0 ? (actualRisk / Account.Balance) * 100 : 0;
                Print(
                    $"[WARN] Calculated volume ({normalised / Symbol.LotSize:F4} lots) < minimum " +
                    $"({Symbol.VolumeInUnitsMin / Symbol.LotSize:F4} lots). " +
                    $"Clamped — actual risk = {actualRiskPct:F2}% ({actualRisk:F2} {Account.Currency}) " +
                    $"vs configured {RiskPerTradePct:F2}%"
                );
                normalised = Symbol.VolumeInUnitsMin;
            }
            else
            {
                normalised = Math.Min(normalised, Symbol.VolumeInUnitsMax);
            }

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
