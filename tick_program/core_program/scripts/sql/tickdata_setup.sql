/* ============================================================
   tickdata_setup.sql
   Project   : SEN05 Auto Trading — Tick Data Module
   Database  : SEN05_AutoTrading  (SQL Server 2022)

   PURPOSE:
     One-file idempotent setup for the tick_program module.
     Run this script once on any new environment before starting
     the tick_program service.

   WHAT THIS CREATES:
     tick (schema)
       SymbolMap           — cTrader ↔ SEN05 symbol mapping
       IngestRun           — per-startup audit log
       IngestState         — per-symbol health state
       FR40 … BTCUSD       — 11 per-symbol tick tables (Clustered Columnstore)

   STORAGE DESIGN (2026-09, replaces the earlier zigzag-pivot thinning):
     Every tick table is a Clustered Columnstore Index (CCI), not a
     rowstore table. This keeps 100% of every ingested tick forever —
     nothing is ever deleted for space reasons — while still compressing
     10-18x versus the old rowstore + covering-index design (measured:
     tick schema went from 38GB to ~880MB after the 2026-08-24 rowstore
     rebuild; CCI is expected to beat even that without discarding a
     single row). Deleting ticks to save space (the old thin-history job)
     was dropped because it destroys backtest fidelity: a strategy with
     any logic finer than the pivot-confirmation threshold (trailing
     stops, multi-touch counting, exact fill timing) would silently get
     wrong results replayed against thinned data. See `compress-ticks`
     (src/__main__.py) for the periodic maintenance this design needs
     instead of thinning.
     - A CCI cannot enforce a UNIQUE constraint by itself (constraints on
       a columnstore-indexed table are always enforced via an accompanying
       row-store B-tree index — supported since SQL Server 2016). Each
       table keeps a unique nonclustered index for the same reconnect/
       overlap dedup purpose the old design needed.
     - IX_*_Time (the old covering index, INCLUDE'ing every other column)
       is gone. It existed only to serve thin-history's cursor-based
       fetch, which no longer exists; a CCI's own per-segment min/max
       metadata makes ordinary time-range scans efficient without it.

   DEDUP KEY (2026-09a, EventHash removed): a tick's identity is
   (TickTimeUtc, Bid, Ask) directly, not a derived SHA-256 hash column.
   A symbol's own table already fixes SymbolID, and cTrader's raw tick
   feed (ProtoOATickData) is just (timestamp, price) per side — so the
   merged two-sided quote is fully identified by its own stored columns;
   a hash of exactly that information added nothing a direct index
   couldn't already enforce. Measured cost of keeping it: EventHash was
   the single largest column in a tick table (~66% of on-disk size on
   tick.US100, SHA-256 being high-entropy and resisting columnstore
   compression), versus ~0% for a genuinely redundant column like
   SymbolID (a constant per table compresses away almost completely).
   This also removed price_from_raw()'s digits-based rounding
   (src/pipeline.py): digits is a display-precision hint from cTrader
   (ProtoOASymbol: "Number of price digits to be displayed"), not a
   ceiling on the real precision the tick feed carries, so quantizing to
   it would have silently discarded genuine sub-pip movement for any
   symbol whose digits is coarser than the raw /100000 scale — the same
   fidelity-loss concern that ruled out zigzag-thinning.

   DEDUP ENFORCEMENT (2026-09b, replaces the full-history UX_*_TimeBidAsk
   index): a unique index on (TickTimeUtc, Bid, Ask) covering the ENTIRE
   table turned out to cost more than the data itself once PAGE-compressed
   (measured: 2,937MB of index vs 2,627MB of actual columnstore data
   across all 11 tables) -- a plain B-tree doesn't get columnstore's
   compression no matter which columns it indexes. But duplicates can only
   ever arise from a fetch window being re-processed, and every routine
   job is itself time-bounded (see src/backfill.py), so a persisted index
   only needs to cover the last few days, not the whole table. This design
   moves the primary dedup check into the application (src/sql_store.py::
   insert_ticks queries the exact batch's own time range before inserting
   -- cheap even with no index, since a CCI's per-segment min/max metadata
   skips whole segments outside the range) and keeps only a small, rolling,
   filtered unique index (UX_*_RecentDedup, WHERE TickTimeUtc >= a recent
   cutoff, rolled forward daily by `refresh-dedup-window` /
   sql_store.py::refresh_dedup_window) as a backstop against a real but
   narrow race: spool-drain and a live backfill both retrying the same
   rows after a transient SQL failure. See insert_ticks()'s own docstring
   for the full reasoning, including the one accepted gap (a duplicate
   landing outside the filtered window requires a human deliberately
   re-running two overlapping ad-hoc historical backfills at once).
   This index is created/maintained entirely by refresh_dedup_window(),
   not by this script -- its filter boundary is a literal date computed at
   call time, which a static SQL file can't express, so this script only
   ever removes the old full-history index below; run
   `refresh-dedup-window` once after deploying to create the new one.

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
     Run all DWH setup scripts (01–04) first, then this file.

   SAFE TO RE-RUN:
     All CREATE statements are guarded by IF NOT EXISTS / CREATE OR ALTER.
     This script only ever CREATEs objects that don't exist yet -- it does
     NOT convert an existing rowstore tick table to columnstore in place.
     Migrating an existing rowstore install to this columnstore design is
     a one-time, explicit DROP + re-run of this script (see
     docs/ARCHITECTURE.md), never done implicitly by this file.
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
    RAISERROR('ABORT: DWH.Dim_Symbol is missing SymbolIDs: %s — seed them before running this script.', 16, 1, @missing);
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

/* ============================================================
   PART 2: per-symbol tick tables (11 tables, identical structure)

   Schema (6 stored):
     TickID, SymbolID, TickTimeUtc, Bid, Ask, ReceivedAtUtc

   Storage: Clustered Columnstore Index (CCI) -- see header comment.
   Indexes:
     CCI_*              CLUSTERED COLUMNSTORE — primary storage, every
                         tick kept forever, no deletes.
     UX_*_RecentDedup   UNIQUE NONCLUSTERED (TickTimeUtc, Bid, Ask),
                         filtered to the last few days only — created and
                         rolled forward exclusively by
                         sql_store.py::refresh_dedup_window(), not by this
                         script. See the DEDUP ENFORCEMENT note in the
                         header comment for the full design.
   ============================================================ */

IF OBJECT_ID('tick.FR40', 'U') IS NULL
BEGIN
    CREATE TABLE tick.FR40 (
        TickID        BIGINT        NOT NULL IDENTITY(1,1),
        SymbolID      INT           NOT NULL,
        TickTimeUtc   DATETIME2(3)  NOT NULL,
        Bid           DECIMAL(19,8) NULL,
        Ask           DECIMAL(19,8) NULL,
        ReceivedAtUtc DATETIME2(3)  NOT NULL CONSTRAINT DF_tick_FR40_Received DEFAULT SYSUTCDATETIME(),
        CONSTRAINT FK_tick_FR40_Symbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID),
        INDEX CCI_tick_FR40 CLUSTERED COLUMNSTORE
    );
END
GO
IF EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.FR40') AND name='UX_tick_FR40_EventHash')
    DROP INDEX UX_tick_FR40_EventHash ON tick.FR40;
IF COL_LENGTH('tick.FR40', 'EventHash') IS NOT NULL
    ALTER TABLE tick.FR40 DROP COLUMN EventHash;
IF EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.FR40') AND name='UX_tick_FR40_TimeBidAsk')
    DROP INDEX UX_tick_FR40_TimeBidAsk ON tick.FR40;
GO

IF OBJECT_ID('tick.DE40', 'U') IS NULL
BEGIN
    CREATE TABLE tick.DE40 (
        TickID        BIGINT        NOT NULL IDENTITY(1,1),
        SymbolID      INT           NOT NULL,
        TickTimeUtc   DATETIME2(3)  NOT NULL,
        Bid           DECIMAL(19,8) NULL,
        Ask           DECIMAL(19,8) NULL,
        ReceivedAtUtc DATETIME2(3)  NOT NULL CONSTRAINT DF_tick_DE40_Received DEFAULT SYSUTCDATETIME(),
        CONSTRAINT FK_tick_DE40_Symbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID),
        INDEX CCI_tick_DE40 CLUSTERED COLUMNSTORE
    );
END
GO
IF EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.DE40') AND name='UX_tick_DE40_EventHash')
    DROP INDEX UX_tick_DE40_EventHash ON tick.DE40;
IF COL_LENGTH('tick.DE40', 'EventHash') IS NOT NULL
    ALTER TABLE tick.DE40 DROP COLUMN EventHash;
IF EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.DE40') AND name='UX_tick_DE40_TimeBidAsk')
    DROP INDEX UX_tick_DE40_TimeBidAsk ON tick.DE40;
GO

IF OBJECT_ID('tick.HK50', 'U') IS NULL
BEGIN
    CREATE TABLE tick.HK50 (
        TickID        BIGINT        NOT NULL IDENTITY(1,1),
        SymbolID      INT           NOT NULL,
        TickTimeUtc   DATETIME2(3)  NOT NULL,
        Bid           DECIMAL(19,8) NULL,
        Ask           DECIMAL(19,8) NULL,
        ReceivedAtUtc DATETIME2(3)  NOT NULL CONSTRAINT DF_tick_HK50_Received DEFAULT SYSUTCDATETIME(),
        CONSTRAINT FK_tick_HK50_Symbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID),
        INDEX CCI_tick_HK50 CLUSTERED COLUMNSTORE
    );
END
GO
IF EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.HK50') AND name='UX_tick_HK50_EventHash')
    DROP INDEX UX_tick_HK50_EventHash ON tick.HK50;
IF COL_LENGTH('tick.HK50', 'EventHash') IS NOT NULL
    ALTER TABLE tick.HK50 DROP COLUMN EventHash;
IF EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.HK50') AND name='UX_tick_HK50_TimeBidAsk')
    DROP INDEX UX_tick_HK50_TimeBidAsk ON tick.HK50;
GO

IF OBJECT_ID('tick.J225', 'U') IS NULL
BEGIN
    CREATE TABLE tick.J225 (
        TickID        BIGINT        NOT NULL IDENTITY(1,1),
        SymbolID      INT           NOT NULL,
        TickTimeUtc   DATETIME2(3)  NOT NULL,
        Bid           DECIMAL(19,8) NULL,
        Ask           DECIMAL(19,8) NULL,
        ReceivedAtUtc DATETIME2(3)  NOT NULL CONSTRAINT DF_tick_J225_Received DEFAULT SYSUTCDATETIME(),
        CONSTRAINT FK_tick_J225_Symbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID),
        INDEX CCI_tick_J225 CLUSTERED COLUMNSTORE
    );
END
GO
IF EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.J225') AND name='UX_tick_J225_EventHash')
    DROP INDEX UX_tick_J225_EventHash ON tick.J225;
IF COL_LENGTH('tick.J225', 'EventHash') IS NOT NULL
    ALTER TABLE tick.J225 DROP COLUMN EventHash;
IF EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.J225') AND name='UX_tick_J225_TimeBidAsk')
    DROP INDEX UX_tick_J225_TimeBidAsk ON tick.J225;
GO

IF OBJECT_ID('tick.SP35', 'U') IS NULL
BEGIN
    CREATE TABLE tick.SP35 (
        TickID        BIGINT        NOT NULL IDENTITY(1,1),
        SymbolID      INT           NOT NULL,
        TickTimeUtc   DATETIME2(3)  NOT NULL,
        Bid           DECIMAL(19,8) NULL,
        Ask           DECIMAL(19,8) NULL,
        ReceivedAtUtc DATETIME2(3)  NOT NULL CONSTRAINT DF_tick_SP35_Received DEFAULT SYSUTCDATETIME(),
        CONSTRAINT FK_tick_SP35_Symbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID),
        INDEX CCI_tick_SP35 CLUSTERED COLUMNSTORE
    );
END
GO
IF EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.SP35') AND name='UX_tick_SP35_EventHash')
    DROP INDEX UX_tick_SP35_EventHash ON tick.SP35;
IF COL_LENGTH('tick.SP35', 'EventHash') IS NOT NULL
    ALTER TABLE tick.SP35 DROP COLUMN EventHash;
IF EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.SP35') AND name='UX_tick_SP35_TimeBidAsk')
    DROP INDEX UX_tick_SP35_TimeBidAsk ON tick.SP35;
GO

IF OBJECT_ID('tick.UK100', 'U') IS NULL
BEGIN
    CREATE TABLE tick.UK100 (
        TickID        BIGINT        NOT NULL IDENTITY(1,1),
        SymbolID      INT           NOT NULL,
        TickTimeUtc   DATETIME2(3)  NOT NULL,
        Bid           DECIMAL(19,8) NULL,
        Ask           DECIMAL(19,8) NULL,
        ReceivedAtUtc DATETIME2(3)  NOT NULL CONSTRAINT DF_tick_UK100_Received DEFAULT SYSUTCDATETIME(),
        CONSTRAINT FK_tick_UK100_Symbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID),
        INDEX CCI_tick_UK100 CLUSTERED COLUMNSTORE
    );
END
GO
IF EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.UK100') AND name='UX_tick_UK100_EventHash')
    DROP INDEX UX_tick_UK100_EventHash ON tick.UK100;
IF COL_LENGTH('tick.UK100', 'EventHash') IS NOT NULL
    ALTER TABLE tick.UK100 DROP COLUMN EventHash;
IF EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.UK100') AND name='UX_tick_UK100_TimeBidAsk')
    DROP INDEX UX_tick_UK100_TimeBidAsk ON tick.UK100;
GO

IF OBJECT_ID('tick.US500', 'U') IS NULL
BEGIN
    CREATE TABLE tick.US500 (
        TickID        BIGINT        NOT NULL IDENTITY(1,1),
        SymbolID      INT           NOT NULL,
        TickTimeUtc   DATETIME2(3)  NOT NULL,
        Bid           DECIMAL(19,8) NULL,
        Ask           DECIMAL(19,8) NULL,
        ReceivedAtUtc DATETIME2(3)  NOT NULL CONSTRAINT DF_tick_US500_Received DEFAULT SYSUTCDATETIME(),
        CONSTRAINT FK_tick_US500_Symbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID),
        INDEX CCI_tick_US500 CLUSTERED COLUMNSTORE
    );
END
GO
IF EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.US500') AND name='UX_tick_US500_EventHash')
    DROP INDEX UX_tick_US500_EventHash ON tick.US500;
IF COL_LENGTH('tick.US500', 'EventHash') IS NOT NULL
    ALTER TABLE tick.US500 DROP COLUMN EventHash;
IF EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.US500') AND name='UX_tick_US500_TimeBidAsk')
    DROP INDEX UX_tick_US500_TimeBidAsk ON tick.US500;
GO

IF OBJECT_ID('tick.US100', 'U') IS NULL
BEGIN
    CREATE TABLE tick.US100 (
        TickID        BIGINT        NOT NULL IDENTITY(1,1),
        SymbolID      INT           NOT NULL,
        TickTimeUtc   DATETIME2(3)  NOT NULL,
        Bid           DECIMAL(19,8) NULL,
        Ask           DECIMAL(19,8) NULL,
        ReceivedAtUtc DATETIME2(3)  NOT NULL CONSTRAINT DF_tick_US100_Received DEFAULT SYSUTCDATETIME(),
        CONSTRAINT FK_tick_US100_Symbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID),
        INDEX CCI_tick_US100 CLUSTERED COLUMNSTORE
    );
END
GO
IF EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.US100') AND name='UX_tick_US100_EventHash')
    DROP INDEX UX_tick_US100_EventHash ON tick.US100;
IF COL_LENGTH('tick.US100', 'EventHash') IS NOT NULL
    ALTER TABLE tick.US100 DROP COLUMN EventHash;
IF EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.US100') AND name='UX_tick_US100_TimeBidAsk')
    DROP INDEX UX_tick_US100_TimeBidAsk ON tick.US100;
GO

IF OBJECT_ID('tick.US30', 'U') IS NULL
BEGIN
    CREATE TABLE tick.US30 (
        TickID        BIGINT        NOT NULL IDENTITY(1,1),
        SymbolID      INT           NOT NULL,
        TickTimeUtc   DATETIME2(3)  NOT NULL,
        Bid           DECIMAL(19,8) NULL,
        Ask           DECIMAL(19,8) NULL,
        ReceivedAtUtc DATETIME2(3)  NOT NULL CONSTRAINT DF_tick_US30_Received DEFAULT SYSUTCDATETIME(),
        CONSTRAINT FK_tick_US30_Symbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID),
        INDEX CCI_tick_US30 CLUSTERED COLUMNSTORE
    );
END
GO
IF EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.US30') AND name='UX_tick_US30_EventHash')
    DROP INDEX UX_tick_US30_EventHash ON tick.US30;
IF COL_LENGTH('tick.US30', 'EventHash') IS NOT NULL
    ALTER TABLE tick.US30 DROP COLUMN EventHash;
IF EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.US30') AND name='UX_tick_US30_TimeBidAsk')
    DROP INDEX UX_tick_US30_TimeBidAsk ON tick.US30;
GO

IF OBJECT_ID('tick.GOLD', 'U') IS NULL
BEGIN
    CREATE TABLE tick.GOLD (
        TickID        BIGINT        NOT NULL IDENTITY(1,1),
        SymbolID      INT           NOT NULL,
        TickTimeUtc   DATETIME2(3)  NOT NULL,
        Bid           DECIMAL(19,8) NULL,
        Ask           DECIMAL(19,8) NULL,
        ReceivedAtUtc DATETIME2(3)  NOT NULL CONSTRAINT DF_tick_GOLD_Received DEFAULT SYSUTCDATETIME(),
        CONSTRAINT FK_tick_GOLD_Symbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID),
        INDEX CCI_tick_GOLD CLUSTERED COLUMNSTORE
    );
END
GO
IF EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.GOLD') AND name='UX_tick_GOLD_EventHash')
    DROP INDEX UX_tick_GOLD_EventHash ON tick.GOLD;
IF COL_LENGTH('tick.GOLD', 'EventHash') IS NOT NULL
    ALTER TABLE tick.GOLD DROP COLUMN EventHash;
IF EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.GOLD') AND name='UX_tick_GOLD_TimeBidAsk')
    DROP INDEX UX_tick_GOLD_TimeBidAsk ON tick.GOLD;
GO

IF OBJECT_ID('tick.BTCUSD', 'U') IS NULL
BEGIN
    CREATE TABLE tick.BTCUSD (
        TickID        BIGINT        NOT NULL IDENTITY(1,1),
        SymbolID      INT           NOT NULL,
        TickTimeUtc   DATETIME2(3)  NOT NULL,
        Bid           DECIMAL(19,8) NULL,
        Ask           DECIMAL(19,8) NULL,
        ReceivedAtUtc DATETIME2(3)  NOT NULL CONSTRAINT DF_tick_BTCUSD_Received DEFAULT SYSUTCDATETIME(),
        CONSTRAINT FK_tick_BTCUSD_Symbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID),
        INDEX CCI_tick_BTCUSD CLUSTERED COLUMNSTORE
    );
END
GO
IF EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.BTCUSD') AND name='UX_tick_BTCUSD_EventHash')
    DROP INDEX UX_tick_BTCUSD_EventHash ON tick.BTCUSD;
IF COL_LENGTH('tick.BTCUSD', 'EventHash') IS NOT NULL
    ALTER TABLE tick.BTCUSD DROP COLUMN EventHash;
IF EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID('tick.BTCUSD') AND name='UX_tick_BTCUSD_TimeBidAsk')
    DROP INDEX UX_tick_BTCUSD_TimeBidAsk ON tick.BTCUSD;
GO

PRINT 'tickdata_setup complete: tick schema (3 meta tables, 11 columnstore tick tables, no views).';
GO
