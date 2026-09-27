namespace cAlgo.Plugins;

internal static class BacktestMapper
{
    private static readonly Type SettingsType = typeof(BacktestingSettings);
    private static readonly PropertyInfo? PreciseConversionProperty = GetSetting("PreciseConversion");
    private static readonly PropertyInfo? CommissionAutoProperty = GetSetting("ApplyCommissionAutomatically");
    private static readonly PropertyInfo? CommissionProperty = GetSetting("Commission");
    private static readonly PropertyInfo? CommissionTypeProperty = GetSetting("CommissionType");
    private static readonly PropertyInfo? LegacyCommissionProperty = GetSetting("CommissionUsdPerMillionUsd");

    public static bool SupportsPreciseConversion => PreciseConversionProperty?.CanWrite == true;
    public static bool SupportsAutomaticCommission => CommissionAutoProperty?.CanWrite == true;

    public static BacktestingCapabilities Capabilities => new()
    {
        ApiAssemblyVersion = SettingsType.Assembly.GetName().Version?.ToString() ?? "unknown",
        PreciseConversion = SupportsPreciseConversion,
        AutomaticCommission = SupportsAutomaticCommission,
        TypedCommission = CommissionProperty?.CanWrite == true && CommissionTypeProperty?.CanWrite == true,
        LegacyUsdPerMillionCommission = LegacyCommissionProperty?.CanWrite == true,
        TickCsv = Enum.TryParse<BacktestingDataMode>("TickCsv", ignoreCase: true, out _),
    };

    public static BacktestingSettings BuildSettings(BacktestJob job)
    {
        ValidateJob(job);
        if (job.PreciseConversion is null || job.CommissionAuto is null)
            throw new InvalidOperationException(
                "preciseConversion and commissionAuto must be explicitly specified");

        if (job.PreciseConversion == true && !SupportsPreciseConversion)
        {
            throw new UnsupportedBacktestingCapabilityException(
                "PreciseConversion",
                "This cTrader Desktop runtime does not expose " +
                "BacktestingSettings.PreciseConversion. Refusing an approximate " +
                "conversion result for a job that requires precise conversion.");
        }
        if (job.CommissionAuto == true && !SupportsAutomaticCommission)
        {
            throw new UnsupportedBacktestingCapabilityException(
                "AutomaticCommission",
                "This cTrader Desktop runtime does not expose " +
                "BacktestingSettings.ApplyCommissionAutomatically. Refusing to " +
                "replace GUI automatic commission with an implicit zero commission.");
        }

        var settings = new BacktestingSettings
        {
            StartTimeUtc = ParseUtc(job.StartUtc),
            EndTimeUtc = ParseUtc(job.EndUtc),
            Balance = job.Balance,
            DataMode = ParseDataMode(job.DataMode),
            DataFile = job.DataFile,
        };

        if (job.PreciseConversion == true)
            SetSetting(settings, PreciseConversionProperty!, true);

        if (job.CommissionAuto == true)
        {
            SetSetting(settings, CommissionAutoProperty!, true);
        }
        else
        {
            ApplyManualCommission(settings, job);
        }

        if (job.Spread is not null)
            settings.SpreadPips = job.Spread.Value;

        return settings;
    }

    public static object[] BuildParameterValues(RobotType robotType, BacktestJob job)
    {
        var knownNames = robotType.Parameters
            .Select(parameter => parameter.Name)
            .ToHashSet(StringComparer.Ordinal);
        var unknownNames = job.Parameters.Keys
            .Where(name => !knownNames.Contains(name))
            .OrderBy(name => name, StringComparer.Ordinal)
            .ToArray();
        if (unknownNames.Length > 0)
            throw new InvalidOperationException(
                $"Unknown parameter(s) for {robotType.Name}: {string.Join(", ", unknownNames)}");

        var values = new object[robotType.Parameters.Count];
        for (var index = 0; index < robotType.Parameters.Count; index++)
        {
            var parameter = robotType.Parameters[index];
            values[index] = job.Parameters.TryGetValue(parameter.Name, out var raw)
                ? ConvertParameter(parameter, raw)
                : parameter.DefaultValue;
        }
        return values;
    }

    private static void ValidateJob(BacktestJob job)
    {
        var start = ParseUtc(job.StartUtc);
        var end = ParseUtc(job.EndUtc);
        if (end <= start)
            throw new InvalidOperationException("endUtc must be later than startUtc");
        if (!double.IsFinite(job.Balance) || job.Balance <= 0)
            throw new InvalidOperationException("balance must be a positive finite number");
        if (job.DataMode.EndsWith("-csv", StringComparison.OrdinalIgnoreCase)
            && string.IsNullOrWhiteSpace(job.DataFile))
            throw new InvalidOperationException($"dataFile is required for {job.DataMode}");
    }

    private static void ApplyManualCommission(BacktestingSettings settings, BacktestJob job)
    {
        if (job.Commission is null)
        {
            if (!string.IsNullOrWhiteSpace(job.CommissionType))
                throw new InvalidOperationException("commissionType requires commission");
            return;
        }

        if (CommissionProperty?.CanWrite == true)
        {
            SetSetting(settings, CommissionProperty, job.Commission.Value);
            if (!string.IsNullOrWhiteSpace(job.CommissionType))
                SetSetting(settings, CommissionTypeProperty
                    ?? throw Unsupported("TypedCommission"), job.CommissionType);
            return;
        }

        if (LegacyCommissionProperty?.CanWrite != true || !IsLegacyCommissionType(job.CommissionType))
            throw Unsupported("TypedCommission");
        SetSetting(settings, LegacyCommissionProperty, job.Commission.Value);
    }

    private static PropertyInfo? GetSetting(string name) =>
        SettingsType.GetProperty(name, BindingFlags.Instance | BindingFlags.Public);

    private static void SetSetting(BacktestingSettings settings, PropertyInfo property, object value)
    {
        object converted = value;
        var targetType = Nullable.GetUnderlyingType(property.PropertyType) ?? property.PropertyType;
        if (targetType.IsEnum)
            converted = value is string text
                ? Enum.Parse(targetType, text, ignoreCase: true)
                : Enum.ToObject(targetType, value);
        else if (targetType != typeof(string) && value.GetType() != targetType)
            converted = Convert.ChangeType(value, targetType, CultureInfo.InvariantCulture);
        property.SetValue(settings, converted);
    }

    private static UnsupportedBacktestingCapabilityException Unsupported(string capability) =>
        new(capability, $"This cTrader Desktop runtime does not support {capability}.");

    private static bool IsLegacyCommissionType(string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
            return true;
        var normalized = new string(value.Where(char.IsLetterOrDigit).ToArray()).ToLowerInvariant();
        return normalized is "usdpermillionusd" or "usdpermillionusdvolume";
    }

    private static object ConvertParameter(AlgoParameter parameter, JsonElement raw)
    {
        var text = raw.ValueKind == JsonValueKind.String
            ? raw.GetString() ?? string.Empty
            : raw.ToString();
        return parameter.Type switch
        {
            AlgoParameterType.Integer => int.Parse(text, CultureInfo.InvariantCulture),
            AlgoParameterType.Double => double.Parse(text, CultureInfo.InvariantCulture),
            AlgoParameterType.Boolean => ParseBool(text),
            AlgoParameterType.Enum => ConvertEnum(parameter, text),
            AlgoParameterType.TimeFrame => TimeFrame.Parse(text),
            AlgoParameterType.DateTime => ParseUtc(text),
            _ => text,
        };
    }

    private static object ConvertEnum(AlgoParameter parameter, string text)
    {
        var enumType = parameter.DefaultValue?.GetType();
        if (enumType is not null && enumType.IsEnum)
        {
            if (int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out var ordinal))
            {
                var enumValue = Enum.ToObject(enumType, ordinal);
                if (!Enum.IsDefined(enumType, enumValue))
                    throw new FormatException($"Invalid {parameter.Name} enum ordinal: {ordinal}");
                return enumValue;
            }
            return Enum.Parse(enumType, text, ignoreCase: false);
        }
        if (int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out _))
            throw new FormatException(
                $"{parameter.Name} requires an enum name; this runtime cannot validate ordinals");
        if (string.IsNullOrWhiteSpace(text))
            throw new FormatException($"{parameter.Name} enum name is empty");
        return text;
    }

    private static bool ParseBool(string text)
    {
        if (bool.TryParse(text, out var value))
            return value;
        if (text == "1")
            return true;
        if (text == "0")
            return false;
        throw new FormatException($"Invalid boolean: {text}");
    }

    private static DateTime ParseUtc(string text)
    {
        return DateTime.Parse(text, CultureInfo.InvariantCulture, DateTimeStyles.AdjustToUniversal);
    }

    private static BacktestingDataMode ParseDataMode(string text)
    {
        return text.Trim().ToLowerInvariant() switch
        {
            "ticks" => BacktestingDataMode.Ticks,
            "m1" => BacktestingDataMode.M1,
            "m1-csv" => BacktestingDataMode.M1Csv,
            "open" or "openprices" or "open-prices" => BacktestingDataMode.OpenPrices,
            "tick-csv" => Enum.TryParse<BacktestingDataMode>("TickCsv", true, out var mode)
                ? mode
                : throw Unsupported("TickCsv"),
            _ => throw new FormatException($"Unsupported dataMode: {text}"),
        };
    }
}
