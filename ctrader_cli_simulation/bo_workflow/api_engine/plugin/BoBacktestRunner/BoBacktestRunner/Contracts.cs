namespace cAlgo.Plugins;

internal static class BridgeSchemas
{
    public const string Job = "bo-backtest-api-job/v2";
    public const string Result = "bo-backtest-api-result/v1";
}

internal sealed class UnsupportedBacktestingCapabilityException : InvalidOperationException
{
    public UnsupportedBacktestingCapabilityException(string capability, string message) : base(message)
    {
        Capability = capability;
    }

    public string Capability { get; }
}

internal sealed class BacktestJob
{
    public string Schema { get; set; } = "";
    public string JobId { get; set; } = "";
    public string RobotName { get; set; } = "";
    public string? AlgoPath { get; set; }
    public string Symbol { get; set; } = "";
    public string Timeframe { get; set; } = "";
    public string StartUtc { get; set; } = "";
    public string EndUtc { get; set; } = "";
    public double Balance { get; set; }
    public string DataMode { get; set; } = "ticks";
    public string? DataFile { get; set; }
    public bool? PreciseConversion { get; set; }
    public double? Commission { get; set; }
    public string? CommissionType { get; set; }
    public bool? CommissionAuto { get; set; }
    public double? Spread { get; set; }
    public Dictionary<string, JsonElement> Parameters { get; set; } = new();
}

internal sealed class BacktestResult
{
    public string Schema { get; set; } = "";
    public string JobId { get; set; } = "";
    public string Status { get; set; } = "";
    public string? BacktestingError { get; set; }
    public string? JsonReport { get; set; }
    public string? HtmlReport { get; set; }
    public DateTime StartedUtc { get; set; }
    public DateTime EndedUtc { get; set; }
    public double WallSeconds { get; set; }
    public double LastProgress { get; set; }
    public string? Error { get; set; }
    public bool PreciseConversionRequested { get; set; }
    public bool PreciseConversionApplied { get; set; }
    public bool CommissionAutoRequested { get; set; }
    public bool CommissionAutoApplied { get; set; }
    public BacktestingCapabilities? Capabilities { get; set; }
    public RuntimeDiagnostics? Diagnostics { get; set; }
}

internal sealed class BacktestingCapabilities
{
    public string ApiAssemblyVersion { get; set; } = "";
    public bool PreciseConversion { get; set; }
    public bool AutomaticCommission { get; set; }
    public bool TypedCommission { get; set; }
    public bool LegacyUsdPerMillionCommission { get; set; }
    public bool TickCsv { get; set; }
}

internal sealed class RuntimeDiagnostics
{
    public string? AccountBrokerName { get; set; }
    public double AccountPreciseLeverage { get; set; }
    public double AccountStopOutLevel { get; set; }
    public string? AccountTotalMarginCalculationType { get; set; }
    public IReadOnlyList<LeverageTierInfo> SymbolDynamicLeverage { get; set; } = Array.Empty<LeverageTierInfo>();
    public Dictionary<string, double> EstimatedMargins { get; set; } = new();
}

internal sealed class LeverageTierInfo
{
    public double Volume { get; set; }
    public double Leverage { get; set; }
}

internal sealed class JobContext
{
    public JobContext(BacktestJob job, string claimedPath, DateTime startedUtc)
    {
        Job = job;
        ClaimedPath = claimedPath;
        StartedUtc = startedUtc;
    }

    public BacktestJob Job { get; }
    public string ClaimedPath { get; }
    public DateTime StartedUtc { get; }
    public double LastProgress { get; set; }
}
