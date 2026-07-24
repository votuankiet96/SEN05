/* ============================================================
   tickdata_setup.sql
   Project   : SEN05 Auto Trading â€” Tick Data Module
   Database  : SEN05_AutoTrading  (SQL Server 2022)

   PURPOSE:
     One-file idempotent setup for the tick_program module.
     Run this script once on any new environment before starting
     the tick_program service.

   WHAT THIS CREATES:
     SEN.ActiveTask        â€” distributed advisory lock table
     tick (schema)
       SymbolMap           â€” cTrader â†’ SEN05 symbol mapping
       IngestRun           â€” per-startup audit log
       IngestState         â€” per-symbol health state
       FR40 â€¦ BTCUSD       â€” 11 per-symbol tick tables
       v_IngestHealth      â€” symbol health join view
       v_FR40_Quote â€¦ v_BTCUSD_Quote  â€” 11 forward-fill quote views
       v_LatestQuote       â€” current bid/ask snapshot (1 row/symbol)

   PREREQUISITES:
     - SEN05_AutoTrading database must exist
     - DWH schema must exist (run DWH setup scripts first)
     - DWH.Dim_Symbol must contain SymbolIDs: 2,3,4,5,6,7,8,9,10,56,81

   RUN ORDER:
     Run all DWH setup scripts (01â€“04) first, then this file.

   SAFE TO RE-RUN:
     All CREATE statements are guarded by IF NOT EXISTS / CREATE OR ALTER.
   ============================================================ */

SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO

USE SEN05_AutoTrading;
GO

/* ---- prerequisite guard ---- */
IF OBJECT_ID('DWH.Dim_Symbol', 'U') IS NULL
BEGIN
    RAISERROR(
        'ABORT: DWH.Dim_Symbol not found. Run DWH setup scripts (01-04) before this file.',
        16, 1
    );
    RETURN;
END
GO

DECLARE @missing NVARCHAR(200) = '';
SELECT @missing = @missing + CAST(need.id AS NVARCHAR(10)) + ', '
FROM (VALUES (2),(3),(4),(5),(6),(7),(8),(9),(10),(56),(81)) AS need(id)
WHERE NOT EXISTS (SELECT 1 FROM DWH.Dim_Symbol WHERE SymbolID = need.id);
IF @missing <> ''
BEGIN
    RAISERROR('ABORT: DWH.Dim_Symbol is missing SymbolIDs: %s â€” seed them before running this script.', 16, 1, @missing);
    RETURN;
END
GO

/* ============================================================
   PART 1: SEN.ActiveTask
   Distributed advisory lock used by checker, WS ETL, and pipeline
   to prevent write conflicts across processes.
   ============================================================ */

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'SEN')
    EXEC('CREATE SCHEMA SEN');
GO

IF OBJECT_ID('SEN.ActiveTask', 'U') IS NULL
BEGIN
    CREATE TABLE SEN.ActiveTask (
        TaskName  NVARCHAR(50)  NOT NULL,
        StartedAt DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
        ExpiresAt DATETIME2     NOT NULL,
        Payload   NVARCHAR(500) NULL,
        CONSTRAINT PK_ActiveTask PRIMARY KEY CLUSTERED (TaskName)
    );
    PRINT 'SEN.ActiveTask created.';
END
ELSE
    PRINT 'SEN.ActiveTask already exists â€” skipped.';
GO

/* ============================================================
   PART 2: tick schema
   ============================================================ */

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'tick')
    EXEC('CREATE SCHEMA tick');
GO

/* ---- tick.SymbolMap ---- */
IF OBJECT_ID('tick.SymbolMap', 'U') IS NULL
BEGIN
    CREATE TABLE tick.SymbolMap (
        SymbolID           INT           NOT NULL,
        SenSymbol          NVARCHAR(20)  NOT NULL,
        AssetType          NVARCHAR(20)  NOT NULL,
        CTraderSymbolId    BIGINT        NULL,
        CTraderSymbolName  NVARCHAR(80)  NULL,
        CTraderDescription NVARCHAR(200) NULL,
        CTraderEnabled     BIT           NULL,
        Digits             INT           NULL,
        PipPosition        INT           NULL,
        LotSize            BIGINT        NULL,
        MinVolume          BIGINT        NULL,
        MaxVolume          BIGINT        NULL,
        StepVolume         BIGINT        NULL,
        MappingStatus      VARCHAR(20)   NOT NULL CONSTRAINT DF_tick_SymbolMap_Status  DEFAULT 'PENDING',
        MappingScore       INT           NULL,
        Enabled            BIT           NOT NULL CONSTRAINT DF_tick_SymbolMap_Enabled DEFAULT 1,
        LastSyncedAtUtc    DATETIME2(3)  NULL,
        Notes              NVARCHAR(400) NULL,
        CONSTRAINT PK_tick_SymbolMap PRIMARY KEY CLUSTERED (SymbolID),
        CONSTRAINT FK_tick_SymbolMap_DimSymbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID),
        CONSTRAINT CK_tick_SymbolMap_Status CHECK (MappingStatus IN ('PENDING','MATCHED','AMBIGUOUS','NOT_FOUND','DISABLED'))
    );
    CREATE UNIQUE INDEX UX_tick_SymbolMap_CTraderSymbolId
        ON tick.SymbolMap (CTraderSymbolId)
        WHERE CTraderSymbolId IS NOT NULL;
END
GO

/* Seed initial 11 symbols. symbol-sync fills CTraderSymbolId after OAuth. */
MERGE tick.SymbolMap AS target
USING (VALUES
    ( 2, 'FR40',   'Indice'),
    ( 3, 'DE40',   'Indice'),
    ( 4, 'HK50',   'Indice'),
    ( 5, 'J225',   'Indice'),
    ( 6, 'SP35',   'Indice'),
    ( 7, 'UK100',  'Indice'),
    ( 8, 'US500',  'Indice'),
    ( 9, 'US100',  'Indice'),
    (10, 'US30',   'Indice'),
    (56, 'GOLD',   'Metal'),
    (81, 'BTCUSD', 'Crypto')
) AS src (SymbolID, SenSymbol, AssetType)
ON target.SymbolID = src.SymbolID
WHEN NOT MATCHED THEN
    INSERT (SymbolID, SenSymbol, AssetType) VALUES (src.SymbolID, src.SenSymbol, src.AssetType)
WHEN MATCHED AND EXISTS (
    SELECT target.SenSymbol, target.AssetType EXCEPT SELECT src.SenSymbol, src.AssetType
) THEN
    UPDATE SET SenSymbol = src.SenSymbol, AssetType = src.AssetType;
GO

/* ---- tick.IngestRun ---- */
IF OBJECT_ID('tick.IngestRun', 'U') IS NULL
BEGIN
    CREATE TABLE tick.IngestRun (
        IngestRunID         UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_tick_IngestRun_ID      DEFAULT NEWID(),
        AppName             NVARCHAR(80)     NOT NULL,
        Environment         VARCHAR(10)      NOT NULL,
        CtidTraderAccountId BIGINT           NULL,
        StartedAtUtc        DATETIME2(3)     NOT NULL CONSTRAINT DF_tick_IngestRun_Started DEFAULT SYSUTCDATETIME(),
        StoppedAtUtc        DATETIME2(3)     NULL,
        Status              VARCHAR(20)      NOT NULL CONSTRAINT DF_tick_IngestRun_Status  DEFAULT 'RUNNING',
        StopReason          NVARCHAR(400)    NULL,
        RowsInserted        BIGINT           NULL,
        RowsSpooled         BIGINT           NULL,
        HostName            NVARCHAR(128)    NULL,
        ProcessID           INT              NULL,
        CONSTRAINT PK_tick_IngestRun PRIMARY KEY CLUSTERED (IngestRunID),
        CONSTRAINT CK_tick_IngestRun_Status CHECK (Status IN ('RUNNING','STOPPED','FAILED','DONE','INTERRUPTED'))
    );
END
GO

/* Keep constraint current on existing installs */
IF OBJECT_ID('tick.IngestRun', 'U') IS NOT NULL
   AND EXISTS (
        SELECT 1 FROM sys.check_constraints
        WHERE parent_object_id = OBJECT_ID('tick.IngestRun')
          AND name = 'CK_tick_IngestRun_Status'
          AND definition NOT LIKE '%INTERRUPTED%'
   )
    ALTER TABLE tick.IngestRun DROP CONSTRAINT CK_tick_IngestRun_Status;
GO
IF OBJECT_ID('tick.IngestRun', 'U') IS NOT NULL
   AND NOT EXISTS (
        SELECT 1 FROM sys.check_constraints
        WHERE parent_object_id = OBJECT_ID('tick.IngestRun')
          AND name = 'CK_tick_IngestRun_Status'
   )
    ALTER TABLE tick.IngestRun WITH CHECK ADD CONSTRAINT CK_tick_IngestRun_Status
        CHECK (Status IN ('RUNNING','STOPPED','FAILED','DONE','INTERRUPTED'));
GO
IF COL_LENGTH('tick.IngestRun', 'RowsInserted') IS NULL ALTER TABLE tick.IngestRun ADD RowsInserted BIGINT NULL;
GO
IF COL_LENGTH('tick.IngestRun', 'RowsSpooled')  IS NULL ALTER TABLE tick.IngestRun ADD RowsSpooled  BIGINT NULL;
GO

/* ---- tick.IngestState ---- */
IF OBJECT_ID('tick.IngestState', 'U') IS NULL
BEGIN
    CREATE TABLE tick.IngestState (
        SymbolID                  INT           NOT NULL,
        CTraderSymbolId           BIGINT        NULL,
        LastLiveTickTimeUtc       DATETIME2(3)  NULL,
        LastHistoricalTickTimeUtc DATETIME2(3)  NULL,
        LastSourceTimestampMs     BIGINT        NULL,
        LastBid                   DECIMAL(19,8) NULL,
        LastAsk                   DECIMAL(19,8) NULL,
        LastWriteAtUtc            DATETIME2(3)  NULL,
        LastHeartbeatAtUtc        DATETIME2(3)  NULL,
        TotalTicksInserted        BIGINT        NOT NULL CONSTRAINT DF_tick_IngestState_Total   DEFAULT 0,
        ConsecutiveErrors         INT           NOT NULL CONSTRAINT DF_tick_IngestState_Errors  DEFAULT 0,
        Status                    VARCHAR(20)   NOT NULL CONSTRAINT DF_tick_IngestState_Status  DEFAULT 'INIT',
        UpdatedAtUtc              DATETIME2(3)  NOT NULL CONSTRAINT DF_tick_IngestState_Updated DEFAULT SYSUTCDATETIME(),
        LastError                 NVARCHAR(1000) NULL,
        CONSTRAINT PK_tick_IngestState PRIMARY KEY CLUSTERED (SymbolID),
        CONSTRAINT FK_tick_IngestState_DimSymbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID),
        CONSTRAINT CK_tick_IngestState_Status CHECK (Status IN ('INIT','SYNCED','LIVE','STALE','ERROR','DISABLED'))
    );
END
GO

/* ============================================================
   PART 3: per-symbol tick tables (11 tables, identical structure)

   Schema (7 stored):
     TickID, SymbolID, TickTimeUtc, Bid, Ask,
     ReceivedAtUtc, EventHash

   Indexes:
     UX_*_EventHash  UNIQUE IGNORE_DUP_KEY  â€” dedup on reconnect
     IX_*_Time       (TickTimeUtc DESC)     â€” time-range covering index
   ============================================================ */

IF OBJECT_ID('tick.FR40', 'U') IS NULL
BEGIN
    CREATE TABLE tick.FR40 (
        TickID        BIGINT        NOT NULL IDENTITY(1,1) PRIMARY KEY CLUSTERED,
        SymbolID      INT           NOT NULL,
        TickTimeUtc   DATETIME2(3)  NOT NULL,
        Bid           DECIMAL(19,8) NULL,
        Ask           DECIMAL(19,8) NULL,
        ReceivedAtUtc DATETIME2(3)  NOT NULL CONSTRAINT DF_tick_FR40_Received DEFAULT SYSUTCDATETIME(),
        EventHash     BINARY(32)    NOT NULL,
        CONSTRAINT FK_tick_FR40_Symbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID)
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.FR40') AND name='UX_tick_FR40_EventHash')
    CREATE UNIQUE NONCLUSTERED INDEX UX_tick_FR40_EventHash ON tick.FR40(EventHash) WITH (IGNORE_DUP_KEY=ON);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.FR40') AND name='IX_tick_FR40_Time')
    CREATE NONCLUSTERED INDEX IX_tick_FR40_Time ON tick.FR40(TickTimeUtc DESC) INCLUDE(Bid,Ask,ReceivedAtUtc,EventHash);
GO

IF OBJECT_ID('tick.DE40', 'U') IS NULL
BEGIN
    CREATE TABLE tick.DE40 (
        TickID        BIGINT        NOT NULL IDENTITY(1,1) PRIMARY KEY CLUSTERED,
        SymbolID      INT           NOT NULL,
        TickTimeUtc   DATETIME2(3)  NOT NULL,
        Bid           DECIMAL(19,8) NULL,
        Ask           DECIMAL(19,8) NULL,
        ReceivedAtUtc DATETIME2(3)  NOT NULL CONSTRAINT DF_tick_DE40_Received DEFAULT SYSUTCDATETIME(),
        EventHash     BINARY(32)    NOT NULL,
        CONSTRAINT FK_tick_DE40_Symbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID)
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.DE40') AND name='UX_tick_DE40_EventHash')
    CREATE UNIQUE NONCLUSTERED INDEX UX_tick_DE40_EventHash ON tick.DE40(EventHash) WITH (IGNORE_DUP_KEY=ON);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.DE40') AND name='IX_tick_DE40_Time')
    CREATE NONCLUSTERED INDEX IX_tick_DE40_Time ON tick.DE40(TickTimeUtc DESC) INCLUDE(Bid,Ask,ReceivedAtUtc,EventHash);
GO

IF OBJECT_ID('tick.HK50', 'U') IS NULL
BEGIN
    CREATE TABLE tick.HK50 (
        TickID        BIGINT        NOT NULL IDENTITY(1,1) PRIMARY KEY CLUSTERED,
        SymbolID      INT           NOT NULL,
        TickTimeUtc   DATETIME2(3)  NOT NULL,
        Bid           DECIMAL(19,8) NULL,
        Ask           DECIMAL(19,8) NULL,
        ReceivedAtUtc DATETIME2(3)  NOT NULL CONSTRAINT DF_tick_HK50_Received DEFAULT SYSUTCDATETIME(),
        EventHash     BINARY(32)    NOT NULL,
        CONSTRAINT FK_tick_HK50_Symbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID)
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.HK50') AND name='UX_tick_HK50_EventHash')
    CREATE UNIQUE NONCLUSTERED INDEX UX_tick_HK50_EventHash ON tick.HK50(EventHash) WITH (IGNORE_DUP_KEY=ON);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.HK50') AND name='IX_tick_HK50_Time')
    CREATE NONCLUSTERED INDEX IX_tick_HK50_Time ON tick.HK50(TickTimeUtc DESC) INCLUDE(Bid,Ask,ReceivedAtUtc,EventHash);
GO

IF OBJECT_ID('tick.J225', 'U') IS NULL
BEGIN
    CREATE TABLE tick.J225 (
        TickID        BIGINT        NOT NULL IDENTITY(1,1) PRIMARY KEY CLUSTERED,
        SymbolID      INT           NOT NULL,
        TickTimeUtc   DATETIME2(3)  NOT NULL,
        Bid           DECIMAL(19,8) NULL,
        Ask           DECIMAL(19,8) NULL,
        ReceivedAtUtc DATETIME2(3)  NOT NULL CONSTRAINT DF_tick_J225_Received DEFAULT SYSUTCDATETIME(),
        EventHash     BINARY(32)    NOT NULL,
        CONSTRAINT FK_tick_J225_Symbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID)
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.J225') AND name='UX_tick_J225_EventHash')
    CREATE UNIQUE NONCLUSTERED INDEX UX_tick_J225_EventHash ON tick.J225(EventHash) WITH (IGNORE_DUP_KEY=ON);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.J225') AND name='IX_tick_J225_Time')
    CREATE NONCLUSTERED INDEX IX_tick_J225_Time ON tick.J225(TickTimeUtc DESC) INCLUDE(Bid,Ask,ReceivedAtUtc,EventHash);
GO

IF OBJECT_ID('tick.SP35', 'U') IS NULL
BEGIN
    CREATE TABLE tick.SP35 (
        TickID        BIGINT        NOT NULL IDENTITY(1,1) PRIMARY KEY CLUSTERED,
        SymbolID      INT           NOT NULL,
        TickTimeUtc   DATETIME2(3)  NOT NULL,
        Bid           DECIMAL(19,8) NULL,
        Ask           DECIMAL(19,8) NULL,
        ReceivedAtUtc DATETIME2(3)  NOT NULL CONSTRAINT DF_tick_SP35_Received DEFAULT SYSUTCDATETIME(),
        EventHash     BINARY(32)    NOT NULL,
        CONSTRAINT FK_tick_SP35_Symbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID)
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.SP35') AND name='UX_tick_SP35_EventHash')
    CREATE UNIQUE NONCLUSTERED INDEX UX_tick_SP35_EventHash ON tick.SP35(EventHash) WITH (IGNORE_DUP_KEY=ON);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.SP35') AND name='IX_tick_SP35_Time')
    CREATE NONCLUSTERED INDEX IX_tick_SP35_Time ON tick.SP35(TickTimeUtc DESC) INCLUDE(Bid,Ask,ReceivedAtUtc,EventHash);
GO

IF OBJECT_ID('tick.UK100', 'U') IS NULL
BEGIN
    CREATE TABLE tick.UK100 (
        TickID        BIGINT        NOT NULL IDENTITY(1,1) PRIMARY KEY CLUSTERED,
        SymbolID      INT           NOT NULL,
        TickTimeUtc   DATETIME2(3)  NOT NULL,
        Bid           DECIMAL(19,8) NULL,
        Ask           DECIMAL(19,8) NULL,
        ReceivedAtUtc DATETIME2(3)  NOT NULL CONSTRAINT DF_tick_UK100_Received DEFAULT SYSUTCDATETIME(),
        EventHash     BINARY(32)    NOT NULL,
        CONSTRAINT FK_tick_UK100_Symbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID)
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.UK100') AND name='UX_tick_UK100_EventHash')
    CREATE UNIQUE NONCLUSTERED INDEX UX_tick_UK100_EventHash ON tick.UK100(EventHash) WITH (IGNORE_DUP_KEY=ON);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.UK100') AND name='IX_tick_UK100_Time')
    CREATE NONCLUSTERED INDEX IX_tick_UK100_Time ON tick.UK100(TickTimeUtc DESC) INCLUDE(Bid,Ask,ReceivedAtUtc,EventHash);
GO

IF OBJECT_ID('tick.US500', 'U') IS NULL
BEGIN
    CREATE TABLE tick.US500 (
        TickID        BIGINT        NOT NULL IDENTITY(1,1) PRIMARY KEY CLUSTERED,
        SymbolID      INT           NOT NULL,
        TickTimeUtc   DATETIME2(3)  NOT NULL,
        Bid           DECIMAL(19,8) NULL,
        Ask           DECIMAL(19,8) NULL,
        ReceivedAtUtc DATETIME2(3)  NOT NULL CONSTRAINT DF_tick_US500_Received DEFAULT SYSUTCDATETIME(),
        EventHash     BINARY(32)    NOT NULL,
        CONSTRAINT FK_tick_US500_Symbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID)
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.US500') AND name='UX_tick_US500_EventHash')
    CREATE UNIQUE NONCLUSTERED INDEX UX_tick_US500_EventHash ON tick.US500(EventHash) WITH (IGNORE_DUP_KEY=ON);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.US500') AND name='IX_tick_US500_Time')
    CREATE NONCLUSTERED INDEX IX_tick_US500_Time ON tick.US500(TickTimeUtc DESC) INCLUDE(Bid,Ask,ReceivedAtUtc,EventHash);
GO

IF OBJECT_ID('tick.US100', 'U') IS NULL
BEGIN
    CREATE TABLE tick.US100 (
        TickID        BIGINT        NOT NULL IDENTITY(1,1) PRIMARY KEY CLUSTERED,
        SymbolID      INT           NOT NULL,
        TickTimeUtc   DATETIME2(3)  NOT NULL,
        Bid           DECIMAL(19,8) NULL,
        Ask           DECIMAL(19,8) NULL,
        ReceivedAtUtc DATETIME2(3)  NOT NULL CONSTRAINT DF_tick_US100_Received DEFAULT SYSUTCDATETIME(),
        EventHash     BINARY(32)    NOT NULL,
        CONSTRAINT FK_tick_US100_Symbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID)
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.US100') AND name='UX_tick_US100_EventHash')
    CREATE UNIQUE NONCLUSTERED INDEX UX_tick_US100_EventHash ON tick.US100(EventHash) WITH (IGNORE_DUP_KEY=ON);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.US100') AND name='IX_tick_US100_Time')
    CREATE NONCLUSTERED INDEX IX_tick_US100_Time ON tick.US100(TickTimeUtc DESC) INCLUDE(Bid,Ask,ReceivedAtUtc,EventHash);
GO

IF OBJECT_ID('tick.US30', 'U') IS NULL
BEGIN
    CREATE TABLE tick.US30 (
        TickID        BIGINT        NOT NULL IDENTITY(1,1) PRIMARY KEY CLUSTERED,
        SymbolID      INT           NOT NULL,
        TickTimeUtc   DATETIME2(3)  NOT NULL,
        Bid           DECIMAL(19,8) NULL,
        Ask           DECIMAL(19,8) NULL,
        ReceivedAtUtc DATETIME2(3)  NOT NULL CONSTRAINT DF_tick_US30_Received DEFAULT SYSUTCDATETIME(),
        EventHash     BINARY(32)    NOT NULL,
        CONSTRAINT FK_tick_US30_Symbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID)
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.US30') AND name='UX_tick_US30_EventHash')
    CREATE UNIQUE NONCLUSTERED INDEX UX_tick_US30_EventHash ON tick.US30(EventHash) WITH (IGNORE_DUP_KEY=ON);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.US30') AND name='IX_tick_US30_Time')
    CREATE NONCLUSTERED INDEX IX_tick_US30_Time ON tick.US30(TickTimeUtc DESC) INCLUDE(Bid,Ask,ReceivedAtUtc,EventHash);
GO

IF OBJECT_ID('tick.GOLD', 'U') IS NULL
BEGIN
    CREATE TABLE tick.GOLD (
        TickID        BIGINT        NOT NULL IDENTITY(1,1) PRIMARY KEY CLUSTERED,
        SymbolID      INT           NOT NULL,
        TickTimeUtc   DATETIME2(3)  NOT NULL,
        Bid           DECIMAL(19,8) NULL,
        Ask           DECIMAL(19,8) NULL,
        ReceivedAtUtc DATETIME2(3)  NOT NULL CONSTRAINT DF_tick_GOLD_Received DEFAULT SYSUTCDATETIME(),
        EventHash     BINARY(32)    NOT NULL,
        CONSTRAINT FK_tick_GOLD_Symbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID)
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.GOLD') AND name='UX_tick_GOLD_EventHash')
    CREATE UNIQUE NONCLUSTERED INDEX UX_tick_GOLD_EventHash ON tick.GOLD(EventHash) WITH (IGNORE_DUP_KEY=ON);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.GOLD') AND name='IX_tick_GOLD_Time')
    CREATE NONCLUSTERED INDEX IX_tick_GOLD_Time ON tick.GOLD(TickTimeUtc DESC) INCLUDE(Bid,Ask,ReceivedAtUtc,EventHash);
GO

IF OBJECT_ID('tick.BTCUSD', 'U') IS NULL
BEGIN
    CREATE TABLE tick.BTCUSD (
        TickID        BIGINT        NOT NULL IDENTITY(1,1) PRIMARY KEY CLUSTERED,
        SymbolID      INT           NOT NULL,
        TickTimeUtc   DATETIME2(3)  NOT NULL,
        Bid           DECIMAL(19,8) NULL,
        Ask           DECIMAL(19,8) NULL,
        ReceivedAtUtc DATETIME2(3)  NOT NULL CONSTRAINT DF_tick_BTCUSD_Received DEFAULT SYSUTCDATETIME(),
        EventHash     BINARY(32)    NOT NULL,
        CONSTRAINT FK_tick_BTCUSD_Symbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID)
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.BTCUSD') AND name='UX_tick_BTCUSD_EventHash')
    CREATE UNIQUE NONCLUSTERED INDEX UX_tick_BTCUSD_EventHash ON tick.BTCUSD(EventHash) WITH (IGNORE_DUP_KEY=ON);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.BTCUSD') AND name='IX_tick_BTCUSD_Time')
    CREATE NONCLUSTERED INDEX IX_tick_BTCUSD_Time ON tick.BTCUSD(TickTimeUtc DESC) INCLUDE(Bid,Ask,ReceivedAtUtc,EventHash);
GO

/* ============================================================
   PART 4: views

   v_IngestHealth   â€” health monitoring (used by checker + dashboard)
   v_<SYM>_Quote   â€” forward-fill Bid/Ask per symbol (used by dashboard chart)
   v_LatestQuote   â€” current quote snapshot, 1 row per symbol (used by /api/symbols)
   ============================================================ */

CREATE OR ALTER VIEW tick.v_IngestHealth AS
SELECT
    m.SymbolID, m.SenSymbol, m.AssetType, m.CTraderSymbolId, m.CTraderSymbolName,
    m.MappingStatus, s.Status,
    s.LastLiveTickTimeUtc, s.LastHistoricalTickTimeUtc, s.LastSourceTimestampMs,
    s.LastBid, s.LastAsk, s.LastWriteAtUtc, s.LastHeartbeatAtUtc,
    s.TotalTicksInserted, s.ConsecutiveErrors, s.LastError, s.UpdatedAtUtc
FROM tick.SymbolMap m
LEFT JOIN tick.IngestState s ON s.SymbolID = m.SymbolID;
GO

CREATE OR ALTER VIEW tick.v_FR40_Quote AS
WITH grouped AS (
    SELECT TickID, SymbolID, TickTimeUtc, Bid, Ask, ReceivedAtUtc,
        COUNT(Bid) OVER (ORDER BY TickTimeUtc, TickID ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS BidGrp,
        COUNT(Ask) OVER (ORDER BY TickTimeUtc, TickID ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS AskGrp
    FROM tick.FR40
), filled AS (
    SELECT TickID, SymbolID, TickTimeUtc, Bid, Ask, ReceivedAtUtc,
        MAX(Bid) OVER (PARTITION BY BidGrp) AS BidFilled,
        MAX(Ask) OVER (PARTITION BY AskGrp) AS AskFilled
    FROM grouped
)
SELECT TickID, SymbolID, TickTimeUtc, Bid, Ask, BidFilled, AskFilled,
    CAST(CASE WHEN BidFilled IS NOT NULL AND AskFilled IS NOT NULL THEN (BidFilled + AskFilled) / 2 END AS DECIMAL(19,8)) AS Mid,
    CAST(CASE WHEN BidFilled IS NOT NULL AND AskFilled IS NOT NULL THEN AskFilled - BidFilled END AS DECIMAL(19,8)) AS Spread,
    ReceivedAtUtc
FROM filled;
GO

CREATE OR ALTER VIEW tick.v_DE40_Quote AS
WITH grouped AS (
    SELECT TickID, SymbolID, TickTimeUtc, Bid, Ask, ReceivedAtUtc,
        COUNT(Bid) OVER (ORDER BY TickTimeUtc, TickID ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS BidGrp,
        COUNT(Ask) OVER (ORDER BY TickTimeUtc, TickID ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS AskGrp
    FROM tick.DE40
), filled AS (
    SELECT TickID, SymbolID, TickTimeUtc, Bid, Ask, ReceivedAtUtc,
        MAX(Bid) OVER (PARTITION BY BidGrp) AS BidFilled,
        MAX(Ask) OVER (PARTITION BY AskGrp) AS AskFilled
    FROM grouped
)
SELECT TickID, SymbolID, TickTimeUtc, Bid, Ask, BidFilled, AskFilled,
    CAST(CASE WHEN BidFilled IS NOT NULL AND AskFilled IS NOT NULL THEN (BidFilled + AskFilled) / 2 END AS DECIMAL(19,8)) AS Mid,
    CAST(CASE WHEN BidFilled IS NOT NULL AND AskFilled IS NOT NULL THEN AskFilled - BidFilled END AS DECIMAL(19,8)) AS Spread,
    ReceivedAtUtc
FROM filled;
GO

CREATE OR ALTER VIEW tick.v_HK50_Quote AS
WITH grouped AS (
    SELECT TickID, SymbolID, TickTimeUtc, Bid, Ask, ReceivedAtUtc,
        COUNT(Bid) OVER (ORDER BY TickTimeUtc, TickID ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS BidGrp,
        COUNT(Ask) OVER (ORDER BY TickTimeUtc, TickID ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS AskGrp
    FROM tick.HK50
), filled AS (
    SELECT TickID, SymbolID, TickTimeUtc, Bid, Ask, ReceivedAtUtc,
        MAX(Bid) OVER (PARTITION BY BidGrp) AS BidFilled,
        MAX(Ask) OVER (PARTITION BY AskGrp) AS AskFilled
    FROM grouped
)
SELECT TickID, SymbolID, TickTimeUtc, Bid, Ask, BidFilled, AskFilled,
    CAST(CASE WHEN BidFilled IS NOT NULL AND AskFilled IS NOT NULL THEN (BidFilled + AskFilled) / 2 END AS DECIMAL(19,8)) AS Mid,
    CAST(CASE WHEN BidFilled IS NOT NULL AND AskFilled IS NOT NULL THEN AskFilled - BidFilled END AS DECIMAL(19,8)) AS Spread,
    ReceivedAtUtc
FROM filled;
GO

CREATE OR ALTER VIEW tick.v_J225_Quote AS
WITH grouped AS (
    SELECT TickID, SymbolID, TickTimeUtc, Bid, Ask, ReceivedAtUtc,
        COUNT(Bid) OVER (ORDER BY TickTimeUtc, TickID ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS BidGrp,
        COUNT(Ask) OVER (ORDER BY TickTimeUtc, TickID ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS AskGrp
    FROM tick.J225
), filled AS (
    SELECT TickID, SymbolID, TickTimeUtc, Bid, Ask, ReceivedAtUtc,
        MAX(Bid) OVER (PARTITION BY BidGrp) AS BidFilled,
        MAX(Ask) OVER (PARTITION BY AskGrp) AS AskFilled
    FROM grouped
)
SELECT TickID, SymbolID, TickTimeUtc, Bid, Ask, BidFilled, AskFilled,
    CAST(CASE WHEN BidFilled IS NOT NULL AND AskFilled IS NOT NULL THEN (BidFilled + AskFilled) / 2 END AS DECIMAL(19,8)) AS Mid,
    CAST(CASE WHEN BidFilled IS NOT NULL AND AskFilled IS NOT NULL THEN AskFilled - BidFilled END AS DECIMAL(19,8)) AS Spread,
    ReceivedAtUtc
FROM filled;
GO

CREATE OR ALTER VIEW tick.v_SP35_Quote AS
WITH grouped AS (
    SELECT TickID, SymbolID, TickTimeUtc, Bid, Ask, ReceivedAtUtc,
        COUNT(Bid) OVER (ORDER BY TickTimeUtc, TickID ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS BidGrp,
        COUNT(Ask) OVER (ORDER BY TickTimeUtc, TickID ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS AskGrp
    FROM tick.SP35
), filled AS (
    SELECT TickID, SymbolID, TickTimeUtc, Bid, Ask, ReceivedAtUtc,
        MAX(Bid) OVER (PARTITION BY BidGrp) AS BidFilled,
        MAX(Ask) OVER (PARTITION BY AskGrp) AS AskFilled
    FROM grouped
)
SELECT TickID, SymbolID, TickTimeUtc, Bid, Ask, BidFilled, AskFilled,
    CAST(CASE WHEN BidFilled IS NOT NULL AND AskFilled IS NOT NULL THEN (BidFilled + AskFilled) / 2 END AS DECIMAL(19,8)) AS Mid,
    CAST(CASE WHEN BidFilled IS NOT NULL AND AskFilled IS NOT NULL THEN AskFilled - BidFilled END AS DECIMAL(19,8)) AS Spread,
    ReceivedAtUtc
FROM filled;
GO

CREATE OR ALTER VIEW tick.v_UK100_Quote AS
WITH grouped AS (
    SELECT TickID, SymbolID, TickTimeUtc, Bid, Ask, ReceivedAtUtc,
        COUNT(Bid) OVER (ORDER BY TickTimeUtc, TickID ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS BidGrp,
        COUNT(Ask) OVER (ORDER BY TickTimeUtc, TickID ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS AskGrp
    FROM tick.UK100
), filled AS (
    SELECT TickID, SymbolID, TickTimeUtc, Bid, Ask, ReceivedAtUtc,
        MAX(Bid) OVER (PARTITION BY BidGrp) AS BidFilled,
        MAX(Ask) OVER (PARTITION BY AskGrp) AS AskFilled
    FROM grouped
)
SELECT TickID, SymbolID, TickTimeUtc, Bid, Ask, BidFilled, AskFilled,
    CAST(CASE WHEN BidFilled IS NOT NULL AND AskFilled IS NOT NULL THEN (BidFilled + AskFilled) / 2 END AS DECIMAL(19,8)) AS Mid,
    CAST(CASE WHEN BidFilled IS NOT NULL AND AskFilled IS NOT NULL THEN AskFilled - BidFilled END AS DECIMAL(19,8)) AS Spread,
    ReceivedAtUtc
FROM filled;
GO

CREATE OR ALTER VIEW tick.v_US500_Quote AS
WITH grouped AS (
    SELECT TickID, SymbolID, TickTimeUtc, Bid, Ask, ReceivedAtUtc,
        COUNT(Bid) OVER (ORDER BY TickTimeUtc, TickID ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS BidGrp,
        COUNT(Ask) OVER (ORDER BY TickTimeUtc, TickID ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS AskGrp
    FROM tick.US500
), filled AS (
    SELECT TickID, SymbolID, TickTimeUtc, Bid, Ask, ReceivedAtUtc,
        MAX(Bid) OVER (PARTITION BY BidGrp) AS BidFilled,
        MAX(Ask) OVER (PARTITION BY AskGrp) AS AskFilled
    FROM grouped
)
SELECT TickID, SymbolID, TickTimeUtc, Bid, Ask, BidFilled, AskFilled,
    CAST(CASE WHEN BidFilled IS NOT NULL AND AskFilled IS NOT NULL THEN (BidFilled + AskFilled) / 2 END AS DECIMAL(19,8)) AS Mid,
    CAST(CASE WHEN BidFilled IS NOT NULL AND AskFilled IS NOT NULL THEN AskFilled - BidFilled END AS DECIMAL(19,8)) AS Spread,
    ReceivedAtUtc
FROM filled;
GO

CREATE OR ALTER VIEW tick.v_US100_Quote AS
WITH grouped AS (
    SELECT TickID, SymbolID, TickTimeUtc, Bid, Ask, ReceivedAtUtc,
        COUNT(Bid) OVER (ORDER BY TickTimeUtc, TickID ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS BidGrp,
        COUNT(Ask) OVER (ORDER BY TickTimeUtc, TickID ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS AskGrp
    FROM tick.US100
), filled AS (
    SELECT TickID, SymbolID, TickTimeUtc, Bid, Ask, ReceivedAtUtc,
        MAX(Bid) OVER (PARTITION BY BidGrp) AS BidFilled,
        MAX(Ask) OVER (PARTITION BY AskGrp) AS AskFilled
    FROM grouped
)
SELECT TickID, SymbolID, TickTimeUtc, Bid, Ask, BidFilled, AskFilled,
    CAST(CASE WHEN BidFilled IS NOT NULL AND AskFilled IS NOT NULL THEN (BidFilled + AskFilled) / 2 END AS DECIMAL(19,8)) AS Mid,
    CAST(CASE WHEN BidFilled IS NOT NULL AND AskFilled IS NOT NULL THEN AskFilled - BidFilled END AS DECIMAL(19,8)) AS Spread,
    ReceivedAtUtc
FROM filled;
GO

CREATE OR ALTER VIEW tick.v_US30_Quote AS
WITH grouped AS (
    SELECT TickID, SymbolID, TickTimeUtc, Bid, Ask, ReceivedAtUtc,
        COUNT(Bid) OVER (ORDER BY TickTimeUtc, TickID ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS BidGrp,
        COUNT(Ask) OVER (ORDER BY TickTimeUtc, TickID ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS AskGrp
    FROM tick.US30
), filled AS (
    SELECT TickID, SymbolID, TickTimeUtc, Bid, Ask, ReceivedAtUtc,
        MAX(Bid) OVER (PARTITION BY BidGrp) AS BidFilled,
        MAX(Ask) OVER (PARTITION BY AskGrp) AS AskFilled
    FROM grouped
)
SELECT TickID, SymbolID, TickTimeUtc, Bid, Ask, BidFilled, AskFilled,
    CAST(CASE WHEN BidFilled IS NOT NULL AND AskFilled IS NOT NULL THEN (BidFilled + AskFilled) / 2 END AS DECIMAL(19,8)) AS Mid,
    CAST(CASE WHEN BidFilled IS NOT NULL AND AskFilled IS NOT NULL THEN AskFilled - BidFilled END AS DECIMAL(19,8)) AS Spread,
    ReceivedAtUtc
FROM filled;
GO

CREATE OR ALTER VIEW tick.v_GOLD_Quote AS
WITH grouped AS (
    SELECT TickID, SymbolID, TickTimeUtc, Bid, Ask, ReceivedAtUtc,
        COUNT(Bid) OVER (ORDER BY TickTimeUtc, TickID ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS BidGrp,
        COUNT(Ask) OVER (ORDER BY TickTimeUtc, TickID ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS AskGrp
    FROM tick.GOLD
), filled AS (
    SELECT TickID, SymbolID, TickTimeUtc, Bid, Ask, ReceivedAtUtc,
        MAX(Bid) OVER (PARTITION BY BidGrp) AS BidFilled,
        MAX(Ask) OVER (PARTITION BY AskGrp) AS AskFilled
    FROM grouped
)
SELECT TickID, SymbolID, TickTimeUtc, Bid, Ask, BidFilled, AskFilled,
    CAST(CASE WHEN BidFilled IS NOT NULL AND AskFilled IS NOT NULL THEN (BidFilled + AskFilled) / 2 END AS DECIMAL(19,8)) AS Mid,
    CAST(CASE WHEN BidFilled IS NOT NULL AND AskFilled IS NOT NULL THEN AskFilled - BidFilled END AS DECIMAL(19,8)) AS Spread,
    ReceivedAtUtc
FROM filled;
GO

CREATE OR ALTER VIEW tick.v_BTCUSD_Quote AS
WITH grouped AS (
    SELECT TickID, SymbolID, TickTimeUtc, Bid, Ask, ReceivedAtUtc,
        COUNT(Bid) OVER (ORDER BY TickTimeUtc, TickID ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS BidGrp,
        COUNT(Ask) OVER (ORDER BY TickTimeUtc, TickID ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS AskGrp
    FROM tick.BTCUSD
), filled AS (
    SELECT TickID, SymbolID, TickTimeUtc, Bid, Ask, ReceivedAtUtc,
        MAX(Bid) OVER (PARTITION BY BidGrp) AS BidFilled,
        MAX(Ask) OVER (PARTITION BY AskGrp) AS AskFilled
    FROM grouped
)
SELECT TickID, SymbolID, TickTimeUtc, Bid, Ask, BidFilled, AskFilled,
    CAST(CASE WHEN BidFilled IS NOT NULL AND AskFilled IS NOT NULL THEN (BidFilled + AskFilled) / 2 END AS DECIMAL(19,8)) AS Mid,
    CAST(CASE WHEN BidFilled IS NOT NULL AND AskFilled IS NOT NULL THEN AskFilled - BidFilled END AS DECIMAL(19,8)) AS Spread,
    ReceivedAtUtc
FROM filled;
GO

CREATE OR ALTER VIEW tick.v_LatestQuote AS
WITH latest AS (
    SELECT CAST(2 AS INT) AS SymbolID, CAST('FR40' AS NVARCHAR(20)) AS SenSymbol,
        (SELECT TOP 1 Bid FROM tick.FR40 WHERE Bid IS NOT NULL ORDER BY TickTimeUtc DESC, TickID DESC) AS Bid,
        (SELECT TOP 1 Ask FROM tick.FR40 WHERE Ask IS NOT NULL ORDER BY TickTimeUtc DESC, TickID DESC) AS Ask,
        (SELECT MAX(TickTimeUtc) FROM tick.FR40) AS LastTickUtc
    UNION ALL SELECT 3, 'DE40',
        (SELECT TOP 1 Bid FROM tick.DE40 WHERE Bid IS NOT NULL ORDER BY TickTimeUtc DESC, TickID DESC),
        (SELECT TOP 1 Ask FROM tick.DE40 WHERE Ask IS NOT NULL ORDER BY TickTimeUtc DESC, TickID DESC),
        (SELECT MAX(TickTimeUtc) FROM tick.DE40)
    UNION ALL SELECT 4, 'HK50',
        (SELECT TOP 1 Bid FROM tick.HK50 WHERE Bid IS NOT NULL ORDER BY TickTimeUtc DESC, TickID DESC),
        (SELECT TOP 1 Ask FROM tick.HK50 WHERE Ask IS NOT NULL ORDER BY TickTimeUtc DESC, TickID DESC),
        (SELECT MAX(TickTimeUtc) FROM tick.HK50)
    UNION ALL SELECT 5, 'J225',
        (SELECT TOP 1 Bid FROM tick.J225 WHERE Bid IS NOT NULL ORDER BY TickTimeUtc DESC, TickID DESC),
        (SELECT TOP 1 Ask FROM tick.J225 WHERE Ask IS NOT NULL ORDER BY TickTimeUtc DESC, TickID DESC),
        (SELECT MAX(TickTimeUtc) FROM tick.J225)
    UNION ALL SELECT 6, 'SP35',
        (SELECT TOP 1 Bid FROM tick.SP35 WHERE Bid IS NOT NULL ORDER BY TickTimeUtc DESC, TickID DESC),
        (SELECT TOP 1 Ask FROM tick.SP35 WHERE Ask IS NOT NULL ORDER BY TickTimeUtc DESC, TickID DESC),
        (SELECT MAX(TickTimeUtc) FROM tick.SP35)
    UNION ALL SELECT 7, 'UK100',
        (SELECT TOP 1 Bid FROM tick.UK100 WHERE Bid IS NOT NULL ORDER BY TickTimeUtc DESC, TickID DESC),
        (SELECT TOP 1 Ask FROM tick.UK100 WHERE Ask IS NOT NULL ORDER BY TickTimeUtc DESC, TickID DESC),
        (SELECT MAX(TickTimeUtc) FROM tick.UK100)
    UNION ALL SELECT 8, 'US500',
        (SELECT TOP 1 Bid FROM tick.US500 WHERE Bid IS NOT NULL ORDER BY TickTimeUtc DESC, TickID DESC),
        (SELECT TOP 1 Ask FROM tick.US500 WHERE Ask IS NOT NULL ORDER BY TickTimeUtc DESC, TickID DESC),
        (SELECT MAX(TickTimeUtc) FROM tick.US500)
    UNION ALL SELECT 9, 'US100',
        (SELECT TOP 1 Bid FROM tick.US100 WHERE Bid IS NOT NULL ORDER BY TickTimeUtc DESC, TickID DESC),
        (SELECT TOP 1 Ask FROM tick.US100 WHERE Ask IS NOT NULL ORDER BY TickTimeUtc DESC, TickID DESC),
        (SELECT MAX(TickTimeUtc) FROM tick.US100)
    UNION ALL SELECT 10, 'US30',
        (SELECT TOP 1 Bid FROM tick.US30 WHERE Bid IS NOT NULL ORDER BY TickTimeUtc DESC, TickID DESC),
        (SELECT TOP 1 Ask FROM tick.US30 WHERE Ask IS NOT NULL ORDER BY TickTimeUtc DESC, TickID DESC),
        (SELECT MAX(TickTimeUtc) FROM tick.US30)
    UNION ALL SELECT 56, 'GOLD',
        (SELECT TOP 1 Bid FROM tick.GOLD WHERE Bid IS NOT NULL ORDER BY TickTimeUtc DESC, TickID DESC),
        (SELECT TOP 1 Ask FROM tick.GOLD WHERE Ask IS NOT NULL ORDER BY TickTimeUtc DESC, TickID DESC),
        (SELECT MAX(TickTimeUtc) FROM tick.GOLD)
    UNION ALL SELECT 81, 'BTCUSD',
        (SELECT TOP 1 Bid FROM tick.BTCUSD WHERE Bid IS NOT NULL ORDER BY TickTimeUtc DESC, TickID DESC),
        (SELECT TOP 1 Ask FROM tick.BTCUSD WHERE Ask IS NOT NULL ORDER BY TickTimeUtc DESC, TickID DESC),
        (SELECT MAX(TickTimeUtc) FROM tick.BTCUSD)
)
SELECT SymbolID, SenSymbol, Bid, Ask,
    CAST(CASE WHEN Bid IS NOT NULL AND Ask IS NOT NULL THEN (Bid + Ask) / 2 END AS DECIMAL(19,8)) AS Mid,
    CAST(CASE WHEN Bid IS NOT NULL AND Ask IS NOT NULL THEN Ask - Bid END AS DECIMAL(19,8)) AS Spread,
    LastTickUtc
FROM latest;
GO

PRINT 'tickdata_setup complete: SEN.ActiveTask + tick schema (3 meta tables, 11 tick tables, 13 views).';
GO
