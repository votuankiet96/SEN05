/* dp-program: usp_LoadDirect v3 date-dimension fence.

   v2 derived DateKey from BarTime. A staging row outside DWH.Dim_Date
   therefore aborted the whole transaction through FK_Fact_Date. v3 resolves
   the key through Dim_Date, leaving unsupported dates in staging for review
   without poisoning valid ETL rows.
*/

USE SEN05_AutoTrading;
GO

CREATE OR ALTER PROCEDURE DWH.usp_LoadDirect
    @SymbolID     INT,
    @TFCode       VARCHAR(5),
    @StagingTable VARCHAR(50),
    @FromTime     DATETIME2 = NULL
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @TimeframeID TINYINT;
    SELECT @TimeframeID = TimeframeID
    FROM DWH.Dim_Timeframe WHERE Code = @TFCode;

    IF @TimeframeID IS NULL
    BEGIN
        RAISERROR('usp_LoadDirect: TFCode [%s] not found in Dim_Timeframe.', 16, 1, @TFCode);
        RETURN;
    END

    IF @FromTime IS NULL SET @FromTime = '2008-01-01';

    IF @StagingTable NOT IN (
        'SEN.TF_M5', 'SEN.TF_M10','SEN.TF_M15','SEN.TF_M20','SEN.TF_M30',
        'SEN.TF_M45','SEN.TF_H1', 'SEN.TF_M90','SEN.TF_H2', 'SEN.TF_H3',
        'SEN.TF_H4', 'SEN.TF_H6', 'SEN.TF_H8', 'SEN.TF_D1', 'SEN.TF_W'
    )
    BEGIN
        RAISERROR('usp_LoadDirect: Invalid staging table [%s].', 16, 1, @StagingTable);
        RETURN;
    END

    DECLARE @UpdatedRows INT = 0;
    DECLARE @InsertedRows INT = 0;

    DECLARE @sql NVARCHAR(MAX) = N'
        UPDATE f
        SET f.[Open]  = s.[Open],
            f.High    = s.High,
            f.Low     = s.Low,
            f.[Close] = s.[Close],
            f.Volume  = s.Volume
        FROM DWH.Fact_OHLCV AS f
        INNER JOIN ' + @StagingTable + N' AS s
            ON s.SymbolID = f.SymbolID
           AND s.BarTime = f.BarTime
        WHERE f.SymbolID = @SymbolID
          AND f.TimeframeID = @TimeframeID
          AND s.BarTime >= @FromTime
          AND s.IsProcessed = 1
          AND (
              f.[Open] <> s.[Open]
              OR f.High <> s.High
              OR f.Low <> s.Low
              OR f.[Close] <> s.[Close]
              OR ISNULL(f.Volume, -1) <> ISNULL(s.Volume, -1)
          );

        SET @UpdatedRows = @@ROWCOUNT;

        INSERT INTO DWH.Fact_OHLCV
            (SymbolID, TimeframeID, DateKey, BarTime,
             [Open], High, Low, [Close], Volume, TickCount)
        SELECT @SymbolID, @TimeframeID,
            d.DateKey,
            src.BarTime, src.[Open], src.High, src.Low, src.[Close], src.Volume,
            1
        FROM ' + @StagingTable + N' AS src
        INNER JOIN DWH.Dim_Date AS d
            ON d.FullDate = CAST(src.BarTime AS DATE)
        WHERE src.SymbolID = @SymbolID
          AND src.BarTime >= @FromTime
          AND src.IsProcessed = 1
          AND NOT EXISTS (
              SELECT 1 FROM DWH.Fact_OHLCV f WITH (UPDLOCK, HOLDLOCK)
              WHERE f.SymbolID = @SymbolID
                AND f.TimeframeID = @TimeframeID
                AND f.BarTime = src.BarTime
          );

        SET @InsertedRows = @@ROWCOUNT;
    ';

    BEGIN TRY
        BEGIN TRANSACTION;

        EXEC sp_executesql @sql,
            N'@SymbolID INT, @TimeframeID TINYINT, @FromTime DATETIME2,
              @UpdatedRows INT OUTPUT, @InsertedRows INT OUTPUT',
            @SymbolID, @TimeframeID, @FromTime,
            @UpdatedRows OUTPUT, @InsertedRows OUTPUT;

        COMMIT TRANSACTION;
    END TRY
    BEGIN CATCH
        IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH

    SELECT @UpdatedRows AS UpdatedRows,
           @InsertedRows AS InsertedRows,
           @UpdatedRows + @InsertedRows AS AffectedRows;
END
GO

IF EXISTS (
    SELECT 1 FROM sys.extended_properties
    WHERE major_id = OBJECT_ID('DWH.usp_LoadDirect')
      AND minor_id = 0
      AND class = 1
      AND name = 'DPContractVersion'
)
    EXEC sp_updateextendedproperty @name = N'DPContractVersion', @value = N'3',
        @level0type = N'SCHEMA', @level0name = N'DWH',
        @level1type = N'PROCEDURE', @level1name = N'usp_LoadDirect';
ELSE
    EXEC sp_addextendedproperty @name = N'DPContractVersion', @value = N'3',
        @level0type = N'SCHEMA', @level0name = N'DWH',
        @level1type = N'PROCEDURE', @level1name = N'usp_LoadDirect';
GO

PRINT 'usp_LoadDirect v3 deployed: DateKey is fenced by DWH.Dim_Date.';
GO
