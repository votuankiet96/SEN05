/* dp-program: usp_LoadDirect v4 bounded execution-plan migration.

   v3 correctly fenced DateKey through DWH.Dim_Date, but its dynamic SQL
   reused parameter-sniffed plans. A plan first compiled for the 2008
   default could be reused by five-minute live calls, scanning years of
   staging/Fact data and consuming parallel SQL workers until THREADPOOL
   and CXSYNC waits caused 30-second command timeouts.

   v4 preserves the v3 transaction, idempotency and calendar fence while
   forcing each of the two data statements to compile for the supplied
   @FromTime and use one SQL worker. This migration is safe to re-run.
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
          )
        OPTION (RECOMPILE, MAXDOP 1);

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
          )
        OPTION (RECOMPILE, MAXDOP 1);

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
    EXEC sp_updateextendedproperty @name = N'DPContractVersion', @value = N'4',
        @level0type = N'SCHEMA', @level0name = N'DWH',
        @level1type = N'PROCEDURE', @level1name = N'usp_LoadDirect';
ELSE
    EXEC sp_addextendedproperty @name = N'DPContractVersion', @value = N'4',
        @level0type = N'SCHEMA', @level0name = N'DWH',
        @level1type = N'PROCEDURE', @level1name = N'usp_LoadDirect';
GO

PRINT 'usp_LoadDirect v4 deployed: bounded per-window, single-worker plans enabled.';
GO
