CREATE OR ALTER PROCEDURE DWH.usp_AggregateFromStaging
    @SymbolID        INT,
    @SourceTable     VARCHAR(50),
    @TargetTFCode    VARCHAR(5),
    @FromTime        DATETIME2 = NULL
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @TFMinutes     INT;
    DECLARE @TimeframeID   TINYINT;
    DECLARE @ExpectedCount INT;

    SELECT @TFMinutes = Minutes, @TimeframeID = TimeframeID
    FROM DWH.Dim_Timeframe
    WHERE Code = @TargetTFCode;

    IF @TimeframeID IS NULL
    BEGIN
        RAISERROR('usp_AggregateFromStaging: TFCode [%s] not found.', 16, 1, @TargetTFCode);
        RETURN;
    END

    IF @FromTime IS NULL
        SET @FromTime = '2008-01-01';

    IF @SourceTable NOT IN ('SEN.TF_M5', 'SEN.TF_M30', 'SEN.TF_H3', 'SEN.TF_H4')
    BEGIN
        RAISERROR('usp_AggregateFromStaging: Invalid source table [%s].', 16, 1, @SourceTable);
        RETURN;
    END

    SET @ExpectedCount = CASE @TargetTFCode
        WHEN 'M10' THEN 2
        WHEN 'M20' THEN 4
        WHEN 'M90' THEN 3
        WHEN 'H6'  THEN 2
        WHEN 'H8'  THEN 2
        ELSE NULL
    END;

    IF @ExpectedCount IS NULL
    BEGIN
        RAISERROR('usp_AggregateFromStaging: Unsupported computed TF [%s].', 16, 1, @TargetTFCode);
        RETURN;
    END

    DECLARE @sql NVARCHAR(MAX) = N'
    ;WITH Bucketed AS (
        SELECT
            BarTime,
            [Open],
            High,
            Low,
            [Close],
            Volume,
            DATEADD(
                MINUTE,
                (DATEDIFF(MINUTE, ''2000-01-01'', BarTime) / @TFMinutes) * @TFMinutes,
                ''2000-01-01''
            ) AS BucketTime
        FROM ' + @SourceTable + N'
        WHERE SymbolID = @SymbolID
          AND BarTime >= @FromTime
          AND IsProcessed = 1
    ),
    WithOHLC AS (
        SELECT
            BucketTime,
            BarTime,
            High,
            Low,
            Volume,
            FIRST_VALUE([Open]) OVER (
                PARTITION BY BucketTime
                ORDER BY BarTime ASC
                ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
            ) AS CandleOpen,
            LAST_VALUE([Close]) OVER (
                PARTITION BY BucketTime
                ORDER BY BarTime ASC
                ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
            ) AS CandleClose
        FROM Bucketed
    ),
    Aggregated AS (
        SELECT
            BucketTime,
            MIN(BarTime)     AS FirstBarTime,
            MIN(CandleOpen)  AS [Open],
            MAX(High)        AS High,
            MIN(Low)         AS Low,
            MIN(CandleClose) AS [Close],
            SUM(Volume)      AS Volume,
            COUNT(*)         AS BarCount
        FROM WithOHLC
        GROUP BY BucketTime
    ),
    ValidAggregated AS (
        SELECT
            FirstBarTime,
            [Open],
            High,
            Low,
            [Close],
            Volume,
            BarCount
        FROM Aggregated
        WHERE BarCount = @ExpectedCount
    )
    MERGE DWH.Fact_OHLCV AS tgt
    USING (
        SELECT
            @SymbolID AS SymbolID,
            @TimeframeID AS TimeframeID,
            CONVERT(INT, CONVERT(VARCHAR, CAST(FirstBarTime AS DATE), 112)) AS DateKey,
            FirstBarTime AS BarTime,
            [Open],
            High,
            Low,
            [Close],
            Volume,
            BarCount
        FROM ValidAggregated
    ) AS src
      ON tgt.SymbolID = src.SymbolID
     AND tgt.TimeframeID = src.TimeframeID
     AND tgt.BarTime = src.BarTime
    WHEN MATCHED AND (
        ISNULL(tgt.[Open], 0) <> ISNULL(src.[Open], 0)
        OR ISNULL(tgt.High, 0) <> ISNULL(src.High, 0)
        OR ISNULL(tgt.Low, 0) <> ISNULL(src.Low, 0)
        OR ISNULL(tgt.[Close], 0) <> ISNULL(src.[Close], 0)
        OR ISNULL(tgt.Volume, 0) <> ISNULL(src.Volume, 0)
        OR ISNULL(tgt.TickCount, -1) <> ISNULL(src.BarCount, -1)
    ) THEN
        UPDATE SET
            tgt.DateKey   = src.DateKey,
            tgt.[Open]    = src.[Open],
            tgt.High      = src.High,
            tgt.Low       = src.Low,
            tgt.[Close]   = src.[Close],
            tgt.Volume    = src.Volume,
            tgt.TickCount = src.BarCount
    WHEN NOT MATCHED THEN
        INSERT (
            SymbolID, TimeframeID, DateKey, BarTime,
            [Open], High, Low, [Close], Volume, TickCount
        )
        VALUES (
            src.SymbolID, src.TimeframeID, src.DateKey, src.BarTime,
            src.[Open], src.High, src.Low, src.[Close], src.Volume, src.BarCount
        );';

    EXEC sp_executesql
        @sql,
        N'@SymbolID INT, @TimeframeID TINYINT, @TFMinutes INT, @ExpectedCount INT, @FromTime DATETIME2',
        @SymbolID, @TimeframeID, @TFMinutes, @ExpectedCount, @FromTime;

    PRINT 'usp_AggregateFromStaging PATCHED OK: ' + @SourceTable + ' -> ' + @TargetTFCode
          + ' SymbolID=' + CAST(@SymbolID AS VARCHAR);
END
GO
