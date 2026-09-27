namespace cAlgo.Plugins;

internal static class ReportValidator
{
    public static void Validate(BacktestJob job, string jsonReport)
    {
        using var document = JsonDocument.Parse(jsonReport);
        var root = document.RootElement;
        var main = root.GetProperty("main");
        RequireText(main, "cBotName", job.RobotName);
        RequireText(main, "symbol", job.Symbol);
        RequireText(main, "period", job.Timeframe);

        var capital = main.GetProperty("startingCapital").GetDouble();
        if (Math.Abs(capital - job.Balance) > 0.000001)
            throw Mismatch("startingCapital", job.Balance, capital);

        var period = main.GetProperty("testingPeriod");
        RequireEpoch(period, "startDate", ParseUtc(job.StartUtc));
        RequireEpoch(period, "endDate", ParseUtc(job.EndUtc));
        ValidateParameters(job, root.GetProperty("parameters"));
    }

    private static void ValidateParameters(BacktestJob job, JsonElement reportParameters)
    {
        var actual = reportParameters.EnumerateArray().ToDictionary(
            item => item.GetProperty("propertyName").GetString() ?? "",
            item => item.GetProperty("value"),
            StringComparer.Ordinal);

        foreach (var (name, expected) in job.Parameters)
        {
            if (!actual.TryGetValue(name, out var value) || !Equivalent(expected, value))
                throw Mismatch($"parameter {name}", expected.ToString(),
                    actual.TryGetValue(name, out value) ? value.ToString() : "<missing>");
        }
    }

    private static bool Equivalent(JsonElement expected, JsonElement actual)
    {
        var left = expected.ValueKind == JsonValueKind.String
            ? expected.GetString() ?? ""
            : expected.ToString();
        var right = actual.ValueKind == JsonValueKind.String
            ? actual.GetString() ?? ""
            : actual.ToString();
        if (bool.TryParse(left, out var leftBool) && bool.TryParse(right, out var rightBool))
            return leftBool == rightBool;
        if (decimal.TryParse(left, NumberStyles.Number, CultureInfo.InvariantCulture, out var leftNumber)
            && decimal.TryParse(right, NumberStyles.Number, CultureInfo.InvariantCulture, out var rightNumber))
            return leftNumber == rightNumber;
        return string.Equals(left, right, StringComparison.OrdinalIgnoreCase);
    }

    private static void RequireText(JsonElement parent, string name, string expected)
    {
        var actual = parent.GetProperty(name).GetString();
        if (!string.Equals(actual, expected, StringComparison.OrdinalIgnoreCase))
            throw Mismatch(name, expected, actual);
    }

    private static void RequireEpoch(JsonElement parent, string name, DateTime expected)
    {
        var actual = parent.GetProperty(name).GetInt64();
        var expectedEpoch = new DateTimeOffset(expected).ToUnixTimeMilliseconds();
        if (actual != expectedEpoch)
            throw Mismatch(name, expectedEpoch, actual);
    }

    private static DateTime ParseUtc(string text) =>
        DateTime.Parse(text, CultureInfo.InvariantCulture, DateTimeStyles.AdjustToUniversal);

    private static InvalidOperationException Mismatch(string name, object? expected, object? actual) =>
        new($"Report {name} mismatch: expected {expected}, got {actual}");
}
