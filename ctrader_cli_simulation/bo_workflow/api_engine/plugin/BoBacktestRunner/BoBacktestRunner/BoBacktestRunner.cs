namespace cAlgo.Plugins;

public partial class BoBacktestRunner : Plugin
{
    private readonly Dictionary<BacktestingProcess, JobContext> _running = new();
    private readonly JsonSerializerOptions _jsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        PropertyNameCaseInsensitive = true,
        WriteIndented = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    private string JobsDir => IOPath.Combine(BridgeRoot, "jobs");
    private string ClaimedDir => IOPath.Combine(BridgeRoot, "claimed");
    private string ResultsDir => IOPath.Combine(BridgeRoot, "results");
    private string FailedDir => IOPath.Combine(BridgeRoot, "failed");
    private string HeartbeatDir => IOPath.Combine(BridgeRoot, "heartbeat");
    private int EffectiveMaxConcurrent => Math.Clamp(MaxConcurrentJobs, 1, 12);

    protected override void OnStart()
    {
        EnsureBridgeDirs();
        Timer.Start(Math.Max(1, PollSeconds));
        WriteHeartbeat("started");
        Print($"BoBacktestRunner started. bridge={BridgeRoot}, maxConcurrent={EffectiveMaxConcurrent}");
    }

    protected override void OnStop()
    {
        Timer.Stop();
        foreach (var process in _running.Keys.ToArray())
        {
            try
            {
                process.Terminate();
            }
            catch (Exception ex)
            {
                Print($"Terminate failed: {ex.Message}");
            }
        }
        _running.Clear();
        WriteHeartbeat("stopped");
    }

    protected override void OnTimer()
    {
        WriteHeartbeat("alive");
        while (_running.Count < EffectiveMaxConcurrent)
        {
            var next = IODirectory.EnumerateFiles(JobsDir, "*.json")
                .OrderBy(path => path, StringComparer.OrdinalIgnoreCase)
                .FirstOrDefault();
            if (next is null)
                return;

            var claimed = IOPath.Combine(ClaimedDir, IOPath.GetFileName(next));
            try
            {
                IOFile.Move(next, claimed);
            }
            catch (IOException)
            {
                return;
            }

            try
            {
                StartClaimedJob(claimed);
            }
            catch (UnsupportedBacktestingCapabilityException ex)
            {
                var jobId = IOPath.GetFileNameWithoutExtension(claimed);
                WriteResult(new BacktestResult
                {
                    JobId = jobId,
                    Status = "unsupported_capability",
                    Error = ex.Message,
                    PreciseConversionRequested = ex.Capability == "PreciseConversion",
                    PreciseConversionApplied = false,
                    CommissionAutoRequested = ex.Capability == "AutomaticCommission",
                    CommissionAutoApplied = false,
                    Capabilities = BacktestMapper.Capabilities,
                    StartedUtc = DateTime.UtcNow,
                    EndedUtc = DateTime.UtcNow,
                }, FailedDir);
            }
            catch (Exception ex)
            {
                var jobId = IOPath.GetFileNameWithoutExtension(claimed);
                WriteResult(new BacktestResult
                {
                    JobId = jobId,
                    Status = ex is InvalidOperationException or FormatException
                        ? "invalid_job"
                        : "plugin_error",
                    Error = ex is InvalidOperationException or FormatException
                        ? ex.Message
                        : ex.ToString(),
                    Capabilities = BacktestMapper.Capabilities,
                    StartedUtc = DateTime.UtcNow,
                    EndedUtc = DateTime.UtcNow,
                }, FailedDir);
            }
        }
    }

    private void StartClaimedJob(string claimedPath)
    {
        var text = IOFile.ReadAllText(claimedPath);
        var job = JsonSerializer.Deserialize<BacktestJob>(text, _jsonOptions)
                  ?? throw new InvalidOperationException("Job JSON is empty");
        if (!string.Equals(job.Schema, BridgeSchemas.Job, StringComparison.Ordinal))
            throw new InvalidOperationException($"Unsupported job schema: {job.Schema}");
        if (string.IsNullOrWhiteSpace(job.JobId))
            throw new InvalidOperationException("jobId is required");

        var robotType = ResolveRobotType(job);
        var timeFrame = TimeFrame.Parse(job.Timeframe);
        var settings = BacktestMapper.BuildSettings(job);
        var parameterValues = BacktestMapper.BuildParameterValues(robotType, job);

        var started = DateTime.UtcNow;
        var process = Backtesting.Start(robotType, job.Symbol, timeFrame, settings, parameterValues);
        var context = new JobContext(job, claimedPath, started);
        _running[process] = context;

        process.ProgressChanged += args => context.LastProgress = args.Progress;
        process.Completed += OnBacktestingCompleted;

        Print($"Started job {job.JobId}: {job.RobotName} {job.Symbol} {job.Timeframe}");
        if (process.IsCompleted)
            CompleteProcess(process, process.JsonReport, process.HtmlReport);
    }

    private RobotType ResolveRobotType(BacktestJob job)
    {
        if (AlgoRegistry.Get(job.RobotName, AlgoKind.Robot) is RobotType robotType)
            return robotType;

        if (!string.IsNullOrWhiteSpace(job.AlgoPath) && IOFile.Exists(job.AlgoPath))
        {
            var result = AlgoRegistry.Install(job.AlgoPath);
            if (!result.Succeeded)
                throw new InvalidOperationException($"Install failed for {job.AlgoPath}: {result.Error}");
            if (AlgoRegistry.Get(job.RobotName, AlgoKind.Robot) is RobotType installed)
                return installed;
        }

        throw new InvalidOperationException($"RobotType not found: {job.RobotName}");
    }

    private void OnBacktestingCompleted(BacktestingCompletedEventArgs args)
    {
        CompleteProcess(args.Process, args.JsonReport, args.HtmlReport);
    }

    private void CompleteProcess(BacktestingProcess process, string jsonReport, string htmlReport)
    {
        if (!_running.TryGetValue(process, out var context))
            return;

        _running.Remove(process);
        var ended = DateTime.UtcNow;
        var error = process.BacktestingError.ToString();
        var ok = string.Equals(error, "None", StringComparison.OrdinalIgnoreCase)
                 && !string.IsNullOrWhiteSpace(jsonReport);
        string? contractError = null;
        if (ok)
        {
            try
            {
                ReportValidator.Validate(context.Job, jsonReport);
            }
            catch (Exception ex)
            {
                ok = false;
                contractError = ex.Message;
            }
        }

        WriteResult(new BacktestResult
        {
            JobId = context.Job.JobId,
            Status = ok ? "ok" : contractError is null ? "backtesting_error" : "report_mismatch",
            BacktestingError = error,
            JsonReport = jsonReport,
            HtmlReport = htmlReport,
            Error = contractError,
            StartedUtc = context.StartedUtc,
            EndedUtc = ended,
            WallSeconds = Math.Round((ended - context.StartedUtc).TotalSeconds, 3),
            LastProgress = context.LastProgress,
            PreciseConversionRequested = context.Job.PreciseConversion == true,
            PreciseConversionApplied = context.Job.PreciseConversion == true
                                       && BacktestMapper.SupportsPreciseConversion,
            CommissionAutoRequested = context.Job.CommissionAuto == true,
            CommissionAutoApplied = context.Job.CommissionAuto == true
                                    && BacktestMapper.SupportsAutomaticCommission,
            Capabilities = BacktestMapper.Capabilities,
            Diagnostics = BuildDiagnostics(context.Job),
        }, ResultsDir);

        Print($"Completed job {context.Job.JobId}: {error}");
    }

    private RuntimeDiagnostics BuildDiagnostics(BacktestJob job)
    {
        var diagnostics = new RuntimeDiagnostics
        {
            AccountBrokerName = Account.BrokerName,
            AccountPreciseLeverage = Account.PreciseLeverage,
            AccountStopOutLevel = Account.StopOutLevel,
            AccountTotalMarginCalculationType = Account.TotalMarginCalculationType.ToString(),
        };

        try
        {
            var symbol = Symbols.GetSymbol(job.Symbol);
            diagnostics.SymbolDynamicLeverage = symbol.DynamicLeverage
                .Select(tier => new LeverageTierInfo
                {
                    Volume = tier.Volume,
                    Leverage = tier.Leverage,
                })
                .ToArray();

            foreach (var volume in new[] { 1.0, 2.0, 3.0, 3.22, 3.95, 4.85 })
            {
                diagnostics.EstimatedMargins[$"sell_{volume.ToString(CultureInfo.InvariantCulture)}"] =
                    symbol.GetEstimatedMargin(TradeType.Sell, volume);
                diagnostics.EstimatedMargins[$"buy_{volume.ToString(CultureInfo.InvariantCulture)}"] =
                    symbol.GetEstimatedMargin(TradeType.Buy, volume);
            }
        }
        catch (Exception ex)
        {
            diagnostics.EstimatedMargins["diagnostics_error"] = -1;
            Print($"Diagnostics failed for {job.Symbol}: {ex.Message}");
        }

        return diagnostics;
    }

    private void WriteResult(BacktestResult result, string directory)
    {
        result.Schema = BridgeSchemas.Result;
        IODirectory.CreateDirectory(directory);
        var finalPath = IOPath.Combine(directory, $"{result.JobId}.json");
        WriteJsonAtomic(finalPath, result);
    }

    private void EnsureBridgeDirs()
    {
        IODirectory.CreateDirectory(JobsDir);
        IODirectory.CreateDirectory(ClaimedDir);
        IODirectory.CreateDirectory(ResultsDir);
        IODirectory.CreateDirectory(FailedDir);
        IODirectory.CreateDirectory(HeartbeatDir);
    }

    private void WriteHeartbeat(string status)
    {
        try
        {
            IODirectory.CreateDirectory(HeartbeatDir);
            IOFile.WriteAllText(IOPath.Combine(HeartbeatDir, "BoBacktestRunner.json"),
                JsonSerializer.Serialize(new
                {
                    schema = "bo-backtest-api-heartbeat/v1",
                    status,
                    utc = DateTime.UtcNow,
                    running = _running.Count,
                    maxConcurrent = EffectiveMaxConcurrent,
                    capabilities = BacktestMapper.Capabilities,
                }, _jsonOptions));
        }
        catch (Exception ex)
        {
            Print($"Heartbeat write failed: {ex.Message}");
        }
    }

    private void WriteJsonAtomic<T>(string finalPath, T value)
    {
        var tempPath = finalPath + $".{Environment.ProcessId}.tmp";
        IOFile.WriteAllText(tempPath, JsonSerializer.Serialize(value, _jsonOptions));
        if (IOFile.Exists(finalPath))
            IOFile.Delete(finalPath);
        IOFile.Move(tempPath, finalPath);
    }
}
