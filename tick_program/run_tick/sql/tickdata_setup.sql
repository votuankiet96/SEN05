/* ============================================================
   tickdata_setup.sql
   Project   : SEN05 Auto Trading â€” Tick Data Module
   Database  : SEN05_AutoTrading  (SQL Server 2022)

   PURPOSE:
     One-file idempotent setup for the tick_program module.
     Run this script once on any new environment before starting
     the tick_program service.

   WHAT THIS CREATES:
     tick (schema)
       SymbolMap           â€” cTrader â†’ SEN05 symbol mapping
       IngestRun           â€” per-startup audit log
       IngestState         â€” per-symbol health state
       ThinState           â€” per-symbol zigzag-pivot thinning cursor
       FR40 â€¦ BTCUSD       â€” 11 per-symbol tick tables

   No SEN.ActiveTask: an earlier version of this file created a
   SEN.ActiveTask advisory-lock table under the assumption it was shared
   SEN05 infrastructure. Checked directly against dp_program_v3 (the
   project that actually owns the SEN/DWH schemas): its own SQL installer
   never creates this table, its test suite asserts
   `"SEN.ActiveTask" not in deploy_sql`, and its own docs say V3 uses an
   OS file lock (runtime/run/engine.lock) instead. tick_program's own
   code never called it either (grep-verified) -- it was dead weight
   based on a stale assumption, not real shared infrastructure.

   No views: the dashboard that once queried v_IngestHealth/v_*_Quote/
   v_LatestQuote was removed, and nothing else in tick_program reads them
   (confirmed via sys.dm_sql_referencing_entities before dropping) -- see
   git history for the last version that had them if ever needed again.

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
   PART 1: tick schema
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

/* Drop unused volume/lot columns on existing installs -- never populated
   (tick_program is backfill-only, never places trades) */
IF COL_LENGTH('tick.SymbolMap', 'LotSize') IS NOT NULL
    ALTER TABLE tick.SymbolMap DROP COLUMN LotSize, MinVolume, MaxVolume, StepVolume;
GO

/* Real trading-schedule + holiday data fetched from cTrader (ProtoOASymbolByIdReq),
   synced daily by `symbol-sync --apply`. Replaces the hand-maintained
   SESSION_RULES table in notify.py as the primary source for "is this
   symbol's market open right now" -- SESSION_RULES stays only as a fallback
   for symbols with no synced schedule yet. ScheduleJson/HolidayJson are
   compact JSON (see src/ctrader_client.py::schedule_from_proto); NULL means
   "not synced yet", not "always open". */
IF COL_LENGTH('tick.SymbolMap', 'ScheduleTimeZone') IS NULL
    ALTER TABLE tick.SymbolMap ADD
        ScheduleTimeZone NVARCHAR(50)  NULL,
        ScheduleJson     NVARCHAR(MAX) NULL,
        HolidayJson      NVARCHAR(MAX) NULL;
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
        CONSTRAINT CK_tick_IngestRun_Status CHECK (Status IN ('RUNNING','STOPPED','FAILED','DONE'))
    );
END
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
        LastHistoricalTickTimeUtc DATETIME2(3)  NULL,
        LastSourceTimestampMs     BIGINT        NULL,
        LastBid                   DECIMAL(19,8) NULL,
        LastAsk                   DECIMAL(19,8) NULL,
        LastWriteAtUtc            DATETIME2(3)  NULL,
        TotalTicksInserted        BIGINT        NOT NULL CONSTRAINT DF_tick_IngestState_Total   DEFAULT 0,
        Status                    VARCHAR(20)   NOT NULL CONSTRAINT DF_tick_IngestState_Status  DEFAULT 'INIT',
        UpdatedAtUtc              DATETIME2(3)  NOT NULL CONSTRAINT DF_tick_IngestState_Updated DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_tick_IngestState PRIMARY KEY CLUSTERED (SymbolID),
        CONSTRAINT FK_tick_IngestState_DimSymbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID),
        CONSTRAINT CK_tick_IngestState_Status CHECK (Status IN ('INIT','SYNCED','LIVE','STALE','ERROR','DISABLED'))
    );
END
GO

/* Drop unused columns on existing installs: LastLiveTickTimeUtc is a
   leftover from a live-streaming mode tick_program doesn't have (backfill
   only); ConsecutiveErrors/LastError were only ever reset, never actually
   populated; LastHeartbeatAtUtc always duplicated LastWriteAtUtc verbatim
   and was never read back. */
IF COL_LENGTH('tick.IngestState', 'LastLiveTickTimeUtc') IS NOT NULL
BEGIN
    IF EXISTS (SELECT 1 FROM sys.default_constraints WHERE name = 'DF_tick_IngestState_Errors')
        ALTER TABLE tick.IngestState DROP CONSTRAINT DF_tick_IngestState_Errors;
    ALTER TABLE tick.IngestState DROP COLUMN LastLiveTickTimeUtc, ConsecutiveErrors, LastError, LastHeartbeatAtUtc;
END
GO

/* Last time a fetch attempt for this symbol completed WITHOUT error, whether
   or not it found any new ticks that cycle. Distinct from
   LastHistoricalTickTimeUtc (which only advances when new ticks land):
   this lets `check` tell "data legitimately has nothing new right now"
   (a clean attempt, however old the last real tick is) apart from "the
   fetch mechanism itself has been failing" -- see notify.py::run_tick_check. */
IF COL_LENGTH('tick.IngestState', 'LastAttemptAtUtc') IS NULL
    ALTER TABLE tick.IngestState ADD LastAttemptAtUtc DATETIME2(3) NULL;
GO

/* ---- tick.ThinState ----
   Resume cursor + in-flight zigzag state for the offline tick-thinning
   pass (src/thinning.py). One row per symbol. ExtremeTickId/ExtremeTickTimeUtc
   identify the single "undecided" row (the current running high/low that
   hasn't yet been confirmed as a pivot or superseded) -- thinning must
   never delete that row and must always resume strictly after it. */
IF OBJECT_ID('tick.ThinState', 'U') IS NULL
BEGIN
    CREATE TABLE tick.ThinState (
        SymbolID            INT           NOT NULL,
        LastPivotMid        DECIMAL(19,8) NOT NULL,
        DirectionKnown      BIT           NOT NULL,
        ExtremeIsHigh       BIT           NOT NULL,
        ExtremeMid          DECIMAL(19,8) NOT NULL,
        ExtremeTickId       BIGINT        NULL,
        ExtremeTickTimeUtc  DATETIME2(3)  NULL,
        UpdatedAtUtc        DATETIME2(3)  NOT NULL CONSTRAINT DF_tick_ThinState_Updated DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_tick_ThinState PRIMARY KEY CLUSTERED (SymbolID),
        CONSTRAINT FK_tick_ThinState_DimSymbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID)
    );
END
GO

/* ============================================================
   PART 2: per-symbol tick tables (11 tables, identical structure)

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

PRINT 'tickdata_setup complete: tick schema (4 meta tables, 11 tick tables, no views).';
GO
