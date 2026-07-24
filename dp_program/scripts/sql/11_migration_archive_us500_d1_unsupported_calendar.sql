/* dp-program controlled migration: archive the approved US500/D1 rows
   outside DWH.Dim_Date, then delete only byte-for-byte archived rows.

   Required invocation (SQLCMD mode):
     sqlcmd -b -S localhost -E -d SEN05_AutoTrading \
       -v DeploymentCommit="<40-char-git-sha>" \
       -i scripts\sql\11_migration_archive_us500_d1_unsupported_calendar.sql

   Business decision: keep DWH.Dim_Date starting in 2008; archive exactly
   2,231 historical US500/D1 staging rows rather than extending the calendar.
*/

USE SEN05_AutoTrading;
GO

SET NOCOUNT ON;
SET XACT_ABORT ON;

DECLARE @ExpectedRows INT = 2231;
DECLARE @US500SymbolID INT = 8;
DECLARE @SourceTable SYSNAME = N'SEN.TF_D1';
DECLARE @Reason NVARCHAR(200) = N'unsupported_calendar_date_before_2008';
DECLARE @DeploymentCommit VARCHAR(40) = '$(DeploymentCommit)';
DECLARE @SourceBefore INT = 0;
DECLARE @ArchiveBefore INT = 0;
DECLARE @Inserted INT = 0;
DECLARE @Verified INT = 0;
DECLARE @Deleted INT = 0;
DECLARE @UnsupportedAfter INT = 0;
DECLARE @ArchivedTotal INT = 0;
DECLARE @LockResult INT;

IF @DeploymentCommit NOT LIKE REPLICATE('[0-9a-f]', 40)
   OR LEN(@DeploymentCommit) <> 40
    THROW 51100, 'DeploymentCommit must be a full 40-character lowercase git SHA.', 1;

BEGIN TRY
    BEGIN TRANSACTION;

    EXEC @LockResult = sys.sp_getapplock
        @Resource = N'dp-program:archive-us500-d1-unsupported-calendar',
        @LockMode = N'Exclusive',
        @LockOwner = N'Transaction',
        @LockTimeout = 30000;
    IF @LockResult < 0
        THROW 51101, 'Could not acquire archive migration application lock.', 1;

    IF OBJECT_ID('SEN.OHLCV_UnsupportedCalendar', 'U') IS NULL
    BEGIN
        CREATE TABLE SEN.OHLCV_UnsupportedCalendar (
            ArchiveID        BIGINT         NOT NULL IDENTITY(1,1),
            SourceTable      SYSNAME        NOT NULL,
            RawID            BIGINT         NOT NULL,
            SymbolID         INT            NOT NULL,
            BarTime          DATETIME2(0)   NOT NULL,
            [Open]           DECIMAL(18,8)  NOT NULL,
            High             DECIMAL(18,8)  NOT NULL,
            Low              DECIMAL(18,8)  NOT NULL,
            [Close]          DECIMAL(18,8)  NOT NULL,
            Volume           DECIMAL(20,4)  NULL,
            ReceivedAt       DATETIME2      NOT NULL,
            IsProcessed      BIT            NOT NULL,
            ArchivedAt       DATETIME2(0)   NOT NULL CONSTRAINT DF_UnsupportedCalendar_ArchivedAt DEFAULT SYSUTCDATETIME(),
            Reason           NVARCHAR(200)  NOT NULL,
            DeploymentCommit CHAR(40)       NOT NULL,
            CONSTRAINT PK_OHLCV_UnsupportedCalendar PRIMARY KEY CLUSTERED (ArchiveID),
            CONSTRAINT UQ_UnsupportedCalendar_Source UNIQUE (SourceTable, RawID)
        );
        CREATE NONCLUSTERED INDEX IX_UnsupportedCalendar_SymbolBarTime
            ON SEN.OHLCV_UnsupportedCalendar (SymbolID, BarTime)
            INCLUDE (SourceTable, Reason, DeploymentCommit);
    END;

    SELECT @SourceBefore = COUNT(*)
    FROM SEN.TF_D1 AS s WITH (UPDLOCK, HOLDLOCK)
    LEFT JOIN DWH.Dim_Date AS d ON d.FullDate = CAST(s.BarTime AS DATE)
    WHERE s.SymbolID = @US500SymbolID
      AND d.DateKey IS NULL;

    SELECT @ArchiveBefore = COUNT(*)
    FROM SEN.OHLCV_UnsupportedCalendar
    WHERE SourceTable = @SourceTable
      AND SymbolID = @US500SymbolID
      AND Reason = @Reason;

    -- Safe idempotent rerun after a previously completed deployment.
    IF @SourceBefore = 0 AND @ArchiveBefore = @ExpectedRows
    BEGIN
        SET @ArchivedTotal = @ArchiveBefore;
        COMMIT TRANSACTION;
        SELECT @SourceBefore AS source_before, 0 AS archive_inserted,
               0 AS source_deleted, 0 AS unsupported_after,
               @ArchivedTotal AS archived_unsupported_rows,
               'already_applied' AS result;
        RETURN;
    END;

    IF @SourceBefore <> @ExpectedRows OR @ArchiveBefore <> 0
        THROW 51102, 'Precondition failed: expected exactly 2231 source rows and zero prior archive rows.', 1;

    INSERT SEN.OHLCV_UnsupportedCalendar
        (SourceTable, RawID, SymbolID, BarTime, [Open], High, Low, [Close],
         Volume, ReceivedAt, IsProcessed, Reason, DeploymentCommit)
    SELECT @SourceTable, s.RawID, s.SymbolID, s.BarTime, s.[Open], s.High,
           s.Low, s.[Close], s.Volume, s.ReceivedAt, s.IsProcessed,
           @Reason, @DeploymentCommit
    FROM SEN.TF_D1 AS s
    LEFT JOIN DWH.Dim_Date AS d ON d.FullDate = CAST(s.BarTime AS DATE)
    WHERE s.SymbolID = @US500SymbolID
      AND d.DateKey IS NULL;
    SET @Inserted = @@ROWCOUNT;

    IF @Inserted <> @SourceBefore
        THROW 51103, 'Archive insert count does not match locked source count.', 1;

    SELECT @Verified = COUNT(*)
    FROM SEN.TF_D1 AS s
    JOIN SEN.OHLCV_UnsupportedCalendar AS a
      ON a.SourceTable = @SourceTable
     AND a.RawID = s.RawID
     AND a.SymbolID = s.SymbolID
     AND a.BarTime = s.BarTime
     AND a.[Open] = s.[Open]
     AND a.High = s.High
     AND a.Low = s.Low
     AND a.[Close] = s.[Close]
     AND (a.Volume = s.Volume OR (a.Volume IS NULL AND s.Volume IS NULL))
     AND a.ReceivedAt = s.ReceivedAt
     AND a.IsProcessed = s.IsProcessed
     AND a.Reason = @Reason
    WHERE s.SymbolID = @US500SymbolID;

    IF @Verified <> @SourceBefore
        THROW 51104, 'Archive verification failed: not every source row has an exact archived copy.', 1;

    DELETE s
    FROM SEN.TF_D1 AS s
    JOIN SEN.OHLCV_UnsupportedCalendar AS a
      ON a.SourceTable = @SourceTable
     AND a.RawID = s.RawID
     AND a.SymbolID = s.SymbolID
     AND a.BarTime = s.BarTime
     AND a.[Open] = s.[Open]
     AND a.High = s.High
     AND a.Low = s.Low
     AND a.[Close] = s.[Close]
     AND (a.Volume = s.Volume OR (a.Volume IS NULL AND s.Volume IS NULL))
     AND a.ReceivedAt = s.ReceivedAt
     AND a.IsProcessed = s.IsProcessed
     AND a.Reason = @Reason
    WHERE s.SymbolID = @US500SymbolID;
    SET @Deleted = @@ROWCOUNT;

    IF @Deleted <> @SourceBefore
        THROW 51105, 'Delete count does not match the verified archive count.', 1;

    SELECT @UnsupportedAfter = COUNT(*)
    FROM SEN.TF_D1 AS s
    LEFT JOIN DWH.Dim_Date AS d ON d.FullDate = CAST(s.BarTime AS DATE)
    WHERE s.SymbolID = @US500SymbolID
      AND d.DateKey IS NULL;

    SELECT @ArchivedTotal = COUNT(*)
    FROM SEN.OHLCV_UnsupportedCalendar
    WHERE SourceTable = @SourceTable
      AND SymbolID = @US500SymbolID
      AND Reason = @Reason;

    IF @UnsupportedAfter <> 0 OR @ArchivedTotal <> @ExpectedRows
        THROW 51106, 'Postcondition failed: staging must be clean and archive must contain exactly 2231 rows.', 1;

    COMMIT TRANSACTION;

    SELECT @SourceBefore AS source_before,
           @Inserted AS archive_inserted,
           @Deleted AS source_deleted,
           @UnsupportedAfter AS unsupported_after,
           @ArchivedTotal AS archived_unsupported_rows,
           'committed' AS result;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
GO
