/* ============================================================
   08_migration_usp_loaddirect_v2.sql
   Project   : SEN05 Data Provider
   Database  : SEN05_AutoTrading

   *** DO NOT RUN THIS AUTOMATICALLY. THIS IS A CONTROLLED-DEPLOY SCRIPT. ***

   WHY THIS SCRIPT EXISTS:
     Round-2 audit (2026-07, two independent reviews) found that this
     database's currently-deployed DWH.usp_LoadDirect is an OLDER shape
     than the one defined in scripts/sql/04_business_objects.sql: it only
     INSERTs and returns no result set. The Python ETL caller
     (core_engine.shared.warehouse.writer.run_etl_direct) always does
     cursor.fetchone() expecting a 3-column row-count result set, so
     EVERY call against the stale procedure raises "No results. Previous
     SQL was not a query." This was confirmed to be the direct cause of
     an active, ongoing Fact_OHLCV staleness (12+ days on some pairs at
     the time of the audit) - not a hypothetical risk.

     04_business_objects.sql is CREATE OR ALTER and always defines the
     correct (v2) procedure shape, but it is normally only run once
     during initial provisioning via 00_run_all.sql. This script exists
     so an operator can redeploy *only* the ETL procedure against an
     already-provisioned, already-running production database, as part
     of a controlled maintenance window - without re-running the rest of
     the setup chain.

   REQUIRED DEPLOY PROCEDURE (do not skip steps):
     1. Confirm no write-path process is currently active:
          python -m core_engine conflict-status --kind live
          python -m core_engine conflict-status --kind historical
        Stop DP Program first if either is active:
          python -m core_engine stop --reason usp_loaddirect_migration
     2. Backup (or snapshot) the SEN05_AutoTrading database, or at minimum
        script out the CURRENT procedure body for rollback:
          SELECT OBJECT_DEFINITION(OBJECT_ID('DWH.usp_LoadDirect'));
        Save that output somewhere before proceeding.
     3. Run THIS script against the target database (sqlcmd or SSMS).
     4. Validate the contract before restarting the service:
          python -m core_engine doctor
        doctor now includes a "db_contract" check
        (core_engine.shared.warehouse.connection.verify_database_contract) that
        fails loudly if the extended property below does not match.
        Confirm it reports OK.
     5. Deploy/restart the application code for this same change window
        (this script only changes the database; the Python contract
        check ships with the refactor/production-structure branch).
     6. Smoke test before resuming full 24/7 operation:
          python -m core_engine live --smoke-seconds 90
        Confirm runtime/logs/live.log shows
        fact_inserted > 0 for at least one batch, and that
        runtime/logs/alerts.log has no new "run_etl_direct failed"
        entries after the smoke window starts.
     7. Run the new reconciliation tool to catch up any staging rows that
        were stuck behind the stale procedure before this migration:
          python -m core_engine reconcile-fact --apply
        Confirm it reports missing_count = 0 across all timeframes before
        re-enabling scheduled staging purge for any table that was
        affected.
     8. Only after 4-7 pass, resume 24/7 operation
        (python -m core_engine run / start the Windows service).

   ROLLBACK:
     Re-apply the procedure body captured in step 2 with a plain
     CREATE OR ALTER PROCEDURE statement, then re-run step 4 (doctor
     will fail the contract check again, which is expected and correct -
     it means the application must not resume writing until forward-
     migrated again).

   SAFE TO RE-RUN:
     Yes - CREATE OR ALTER, and the extended-property block below handles
     both "property does not exist yet" and "property already exists".
   ============================================================ */

USE SEN05_AutoTrading;
GO

PRINT '=== usp_LoadDirect v2 migration starting ===';
PRINT 'Current procedure definition (for your records, copy this before continuing):';
GO
SELECT OBJECT_DEFINITION(OBJECT_ID('DWH.usp_LoadDirect')) AS CurrentDefinitionBeforeMigration;
GO

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
            CONVERT(INT, CONVERT(VARCHAR, CAST(BarTime AS DATE), 112)),
            BarTime, [Open], High, Low, [Close], Volume,
            1
        FROM ' + @StagingTable + N' AS src
        WHERE src.SymbolID    = @SymbolID
          AND src.BarTime    >= @FromTime
          AND src.IsProcessed = 1
          AND NOT EXISTS (
              SELECT 1 FROM DWH.Fact_OHLCV f WITH (UPDLOCK, HOLDLOCK)
              WHERE f.SymbolID    = @SymbolID
                AND f.TimeframeID = @TimeframeID
                AND f.BarTime     = src.BarTime
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

    PRINT 'usp_LoadDirect OK: TF=' + @TFCode
          + ' SymbolID=' + CAST(@SymbolID AS VARCHAR)
          + ' Updated=' + CAST(@UpdatedRows AS VARCHAR)
          + ' Inserted=' + CAST(@InsertedRows AS VARCHAR);
END
GO

IF EXISTS (
    SELECT 1 FROM sys.extended_properties
    WHERE major_id = OBJECT_ID('DWH.usp_LoadDirect')
      AND minor_id = 0
      AND class = 1
      AND name = 'DPContractVersion'
)
    EXEC sp_updateextendedproperty @name = N'DPContractVersion', @value = N'2',
        @level0type = N'SCHEMA', @level0name = N'DWH',
        @level1type = N'PROCEDURE', @level1name = N'usp_LoadDirect';
ELSE
    EXEC sp_addextendedproperty @name = N'DPContractVersion', @value = N'2',
        @level0type = N'SCHEMA', @level0name = N'DWH',
        @level1type = N'PROCEDURE', @level1name = N'usp_LoadDirect';
GO

PRINT '=== usp_LoadDirect v2 migration complete. Now run: python -m core_engine doctor ===';
GO
