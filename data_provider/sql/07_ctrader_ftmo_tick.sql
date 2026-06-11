/* ============================================================
   07_ctrader_ftmo_tick.sql
   Project   : Auto Trading Data Warehouse
   Database  : SEN05_AutoTrading  (SQL Server 2022)

   PURPOSE:
     Create the cTrader/FTMO tick-data area inside the existing
     SEN05_AutoTrading database.

   DESIGN:
     - No new database is created.
     - Schema name is short: tick.
     - Existing SymbolID values from DWH.Dim_Symbol are reused.
     - One physical tick table is created per initial target symbol.

   INITIAL SYMBOLS:
     9 Indices + GOLD + BTCUSD from the current SEN05 config:
       FR40, DE40, HK50, J225, SP35, UK100, US500, US100, US30, GOLD, BTCUSD

   RUN ORDER:
     01_setup_database.sql through 06_active_task.sql first, then this file.
   ============================================================ */

SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO

USE SEN05_AutoTrading;
GO

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'tick')
    EXEC('CREATE SCHEMA tick');
GO

/* ============================================================
   Metadata tables
   ============================================================ */

IF OBJECT_ID('tick.AccountProfile', 'U') IS NULL
BEGIN
    CREATE TABLE tick.AccountProfile (
        AccountProfileID     INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        Environment          VARCHAR(10) NOT NULL,
        CtidTraderAccountId  BIGINT NULL,
        TraderLogin          BIGINT NULL,
        IsLive               BIT NULL,
        BrokerTitleShort     NVARCHAR(80) NULL,
        BrokerName           NVARCHAR(120) NULL,
        AccountType          NVARCHAR(40) NULL,
        DepositAsset         NVARCHAR(20) NULL,
        AccessRights         NVARCHAR(40) NULL,
        RawAccountJson       NVARCHAR(MAX) NULL,
        IsActive             BIT NOT NULL CONSTRAINT DF_tick_AccountProfile_IsActive DEFAULT 1,
        CreatedAtUtc         DATETIME2(3) NOT NULL CONSTRAINT DF_tick_AccountProfile_Created DEFAULT SYSUTCDATETIME(),
        LastSeenAtUtc        DATETIME2(3) NULL,
        CONSTRAINT CK_tick_AccountProfile_Environment CHECK (Environment IN ('demo','live'))
    );

END
GO

IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE object_id = OBJECT_ID('tick.AccountProfile')
      AND name = 'UX_tick_AccountProfile_Env_Account'
)
BEGIN
    CREATE UNIQUE INDEX UX_tick_AccountProfile_Env_Account
        ON tick.AccountProfile (Environment, CtidTraderAccountId)
        WHERE CtidTraderAccountId IS NOT NULL;
END
GO

IF OBJECT_ID('tick.SymbolMap', 'U') IS NULL
BEGIN
    CREATE TABLE tick.SymbolMap (
        SymbolID             INT NOT NULL,
        SenSymbol            NVARCHAR(20) NOT NULL,
        AssetType            NVARCHAR(20) NOT NULL,
        CTraderSymbolId      BIGINT NULL,
        CTraderSymbolName    NVARCHAR(80) NULL,
        CTraderDescription   NVARCHAR(200) NULL,
        CTraderEnabled       BIT NULL,
        Digits               INT NULL,
        PipPosition          INT NULL,
        LotSize              BIGINT NULL,
        MinVolume            BIGINT NULL,
        MaxVolume            BIGINT NULL,
        StepVolume           BIGINT NULL,
        MappingStatus        VARCHAR(20) NOT NULL CONSTRAINT DF_tick_SymbolMap_Status DEFAULT 'PENDING',
        MappingScore         INT NULL,
        Enabled              BIT NOT NULL CONSTRAINT DF_tick_SymbolMap_Enabled DEFAULT 1,
        LastSyncedAtUtc      DATETIME2(3) NULL,
        Notes                NVARCHAR(400) NULL,
        CONSTRAINT PK_tick_SymbolMap PRIMARY KEY CLUSTERED (SymbolID),
        CONSTRAINT FK_tick_SymbolMap_DimSymbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID),
        CONSTRAINT CK_tick_SymbolMap_Status CHECK (MappingStatus IN ('PENDING','MATCHED','AMBIGUOUS','NOT_FOUND','DISABLED'))
    );

    CREATE UNIQUE INDEX UX_tick_SymbolMap_CTraderSymbolId
        ON tick.SymbolMap (CTraderSymbolId)
        WHERE CTraderSymbolId IS NOT NULL;
END
GO

IF OBJECT_ID('tick.IngestRun', 'U') IS NULL
BEGIN
    CREATE TABLE tick.IngestRun (
        IngestRunID          UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_tick_IngestRun_ID DEFAULT NEWID(),
        AppName              NVARCHAR(80) NOT NULL,
        Environment          VARCHAR(10) NOT NULL,
        CtidTraderAccountId  BIGINT NULL,
        StartedAtUtc         DATETIME2(3) NOT NULL CONSTRAINT DF_tick_IngestRun_Started DEFAULT SYSUTCDATETIME(),
        StoppedAtUtc         DATETIME2(3) NULL,
        Status               VARCHAR(20) NOT NULL CONSTRAINT DF_tick_IngestRun_Status DEFAULT 'RUNNING',
        StopReason           NVARCHAR(400) NULL,
        RowsInserted         BIGINT NULL,
        RowsSpooled          BIGINT NULL,
        HostName             NVARCHAR(128) NULL,
        ProcessID            INT NULL,
        CONSTRAINT PK_tick_IngestRun PRIMARY KEY CLUSTERED (IngestRunID),
        CONSTRAINT CK_tick_IngestRun_Status CHECK (Status IN ('RUNNING','STOPPED','FAILED','DONE'))
    );
END
GO

/* Existing installations created before historical backfill used DONE need the
   same status contract as fresh installs. */
IF OBJECT_ID('tick.IngestRun', 'U') IS NOT NULL
   AND EXISTS (
        SELECT 1 FROM sys.check_constraints
        WHERE parent_object_id = OBJECT_ID('tick.IngestRun')
          AND name = 'CK_tick_IngestRun_Status'
          AND definition NOT LIKE '%DONE%'
   )
BEGIN
    ALTER TABLE tick.IngestRun DROP CONSTRAINT CK_tick_IngestRun_Status;
END
GO

IF OBJECT_ID('tick.IngestRun', 'U') IS NOT NULL
   AND NOT EXISTS (
        SELECT 1 FROM sys.check_constraints
        WHERE parent_object_id = OBJECT_ID('tick.IngestRun')
          AND name = 'CK_tick_IngestRun_Status'
   )
BEGIN
    ALTER TABLE tick.IngestRun WITH CHECK ADD CONSTRAINT CK_tick_IngestRun_Status
        CHECK (Status IN ('RUNNING','STOPPED','FAILED','DONE'));
END
GO

IF COL_LENGTH('tick.IngestRun', 'RowsInserted') IS NULL
    ALTER TABLE tick.IngestRun ADD RowsInserted BIGINT NULL;
GO

IF COL_LENGTH('tick.IngestRun', 'RowsSpooled') IS NULL
    ALTER TABLE tick.IngestRun ADD RowsSpooled BIGINT NULL;
GO

IF OBJECT_ID('tick.IngestState', 'U') IS NULL
BEGIN
    CREATE TABLE tick.IngestState (
        SymbolID                 INT NOT NULL,
        CTraderSymbolId          BIGINT NULL,
        LastLiveTickTimeUtc      DATETIME2(3) NULL,
        LastHistoricalTickTimeUtc DATETIME2(3) NULL,
        LastSourceTimestampMs    BIGINT NULL,
        LastBid                  DECIMAL(19,8) NULL,
        LastAsk                  DECIMAL(19,8) NULL,
        LastWriteAtUtc           DATETIME2(3) NULL,
        LastHeartbeatAtUtc       DATETIME2(3) NULL,
        TotalTicksInserted       BIGINT NOT NULL CONSTRAINT DF_tick_IngestState_Total DEFAULT 0,
        ConsecutiveErrors        INT NOT NULL CONSTRAINT DF_tick_IngestState_Errors DEFAULT 0,
        Status                   VARCHAR(20) NOT NULL CONSTRAINT DF_tick_IngestState_Status DEFAULT 'INIT',
        UpdatedAtUtc             DATETIME2(3) NOT NULL CONSTRAINT DF_tick_IngestState_Updated DEFAULT SYSUTCDATETIME(),
        LastError                NVARCHAR(1000) NULL,
        CONSTRAINT PK_tick_IngestState PRIMARY KEY CLUSTERED (SymbolID),
        CONSTRAINT FK_tick_IngestState_DimSymbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID),
        CONSTRAINT CK_tick_IngestState_Status CHECK (Status IN ('INIT','SYNCED','LIVE','STALE','ERROR','DISABLED'))
    );
END
GO

/* ============================================================
   Seed SymbolMap from the current SEN05 runtime symbol contract.

   Note:
   Some local databases may still have legacy Dim_Symbol names such as
   CAC40/DAX/XAU for these SymbolID values. Tick storage uses the current
   config/runtime symbols while preserving the existing SymbolID lineage.
   ============================================================ */

MERGE tick.SymbolMap AS target
USING (
    VALUES
        (2,  'FR40',   'Indice'),
        (3,  'DE40',   'Indice'),
        (4,  'HK50',   'Indice'),
        (5,  'J225',   'Indice'),
        (6,  'SP35',   'Indice'),
        (7,  'UK100',  'Indice'),
        (8,  'US500',  'Indice'),
        (9,  'US100',  'Indice'),
        (10, 'US30',   'Indice'),
        (56, 'GOLD',   'Metal'),
        (81, 'BTCUSD', 'Crypto')
) AS src
    (SymbolID, SenSymbol, AssetType)
ON target.SymbolID = src.SymbolID
WHEN NOT MATCHED THEN
    INSERT (SymbolID, SenSymbol, AssetType)
    VALUES (src.SymbolID, src.SenSymbol, src.AssetType)
WHEN MATCHED AND EXISTS (
    SELECT target.SenSymbol, target.AssetType
    EXCEPT
    SELECT src.SenSymbol, src.AssetType
) THEN
    UPDATE SET
        SenSymbol = src.SenSymbol,
        AssetType = src.AssetType;
GO

/* ============================================================
   Per-symbol tick tables.
   All tables share the same shape. EventHash is computed in Python and
   has an IGNORE_DUP_KEY unique index to make replays/reconnects idempotent.
   ============================================================ */

IF OBJECT_ID('tick.FR40', 'U') IS NULL
BEGIN
    CREATE TABLE tick.FR40 (
        TickID BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY CLUSTERED,
        SymbolID INT NOT NULL,
        CTraderSymbolId BIGINT NOT NULL,
        CTraderSymbolName NVARCHAR(80) NOT NULL,
        TickTimeUtc DATETIME2(3) NOT NULL,
        SourceTimestampMs BIGINT NULL,
        BidRaw BIGINT NULL,
        AskRaw BIGINT NULL,
        Bid DECIMAL(19,8) NULL,
        Ask DECIMAL(19,8) NULL,
        Mid AS CAST(CASE WHEN Bid IS NOT NULL AND Ask IS NOT NULL THEN (Bid + Ask) / 2 ELSE NULL END AS DECIMAL(19,8)) PERSISTED,
        Spread AS CAST(CASE WHEN Bid IS NOT NULL AND Ask IS NOT NULL THEN Ask - Bid ELSE NULL END AS DECIMAL(19,8)) PERSISTED,
        BidUpdated BIT NOT NULL,
        AskUpdated BIT NOT NULL,
        QuoteType VARCHAR(10) NOT NULL,
        SourceMode VARCHAR(16) NOT NULL,
        SessionCloseRaw BIGINT NULL,
        SessionClose DECIMAL(19,8) NULL,
        IsTechnicalEvent BIT NOT NULL CONSTRAINT DF_tick_FR40_Technical DEFAULT 0,
        ReceivedAtUtc DATETIME2(3) NOT NULL CONSTRAINT DF_tick_FR40_Received DEFAULT SYSUTCDATETIME(),
        IngestRunID UNIQUEIDENTIFIER NULL,
        EventHash BINARY(32) NOT NULL,
        CONSTRAINT FK_tick_FR40_Symbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID)
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('tick.FR40') AND name = 'UX_tick_FR40_EventHash')
    CREATE UNIQUE NONCLUSTERED INDEX UX_tick_FR40_EventHash ON tick.FR40(EventHash) WITH (IGNORE_DUP_KEY = ON);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('tick.FR40') AND name = 'IX_tick_FR40_Time')
    CREATE NONCLUSTERED INDEX IX_tick_FR40_Time ON tick.FR40(TickTimeUtc DESC) INCLUDE (Bid, Ask, Mid, Spread, SourceMode);
GO

IF OBJECT_ID('tick.DE40', 'U') IS NULL
BEGIN
    CREATE TABLE tick.DE40 (
        TickID BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY CLUSTERED,
        SymbolID INT NOT NULL,
        CTraderSymbolId BIGINT NOT NULL,
        CTraderSymbolName NVARCHAR(80) NOT NULL,
        TickTimeUtc DATETIME2(3) NOT NULL,
        SourceTimestampMs BIGINT NULL,
        BidRaw BIGINT NULL,
        AskRaw BIGINT NULL,
        Bid DECIMAL(19,8) NULL,
        Ask DECIMAL(19,8) NULL,
        Mid AS CAST(CASE WHEN Bid IS NOT NULL AND Ask IS NOT NULL THEN (Bid + Ask) / 2 ELSE NULL END AS DECIMAL(19,8)) PERSISTED,
        Spread AS CAST(CASE WHEN Bid IS NOT NULL AND Ask IS NOT NULL THEN Ask - Bid ELSE NULL END AS DECIMAL(19,8)) PERSISTED,
        BidUpdated BIT NOT NULL,
        AskUpdated BIT NOT NULL,
        QuoteType VARCHAR(10) NOT NULL,
        SourceMode VARCHAR(16) NOT NULL,
        SessionCloseRaw BIGINT NULL,
        SessionClose DECIMAL(19,8) NULL,
        IsTechnicalEvent BIT NOT NULL CONSTRAINT DF_tick_DE40_Technical DEFAULT 0,
        ReceivedAtUtc DATETIME2(3) NOT NULL CONSTRAINT DF_tick_DE40_Received DEFAULT SYSUTCDATETIME(),
        IngestRunID UNIQUEIDENTIFIER NULL,
        EventHash BINARY(32) NOT NULL,
        CONSTRAINT FK_tick_DE40_Symbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID)
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('tick.DE40') AND name = 'UX_tick_DE40_EventHash')
    CREATE UNIQUE NONCLUSTERED INDEX UX_tick_DE40_EventHash ON tick.DE40(EventHash) WITH (IGNORE_DUP_KEY = ON);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('tick.DE40') AND name = 'IX_tick_DE40_Time')
    CREATE NONCLUSTERED INDEX IX_tick_DE40_Time ON tick.DE40(TickTimeUtc DESC) INCLUDE (Bid, Ask, Mid, Spread, SourceMode);
GO

IF OBJECT_ID('tick.HK50', 'U') IS NULL
BEGIN
    CREATE TABLE tick.HK50 (
        TickID BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY CLUSTERED,
        SymbolID INT NOT NULL,
        CTraderSymbolId BIGINT NOT NULL,
        CTraderSymbolName NVARCHAR(80) NOT NULL,
        TickTimeUtc DATETIME2(3) NOT NULL,
        SourceTimestampMs BIGINT NULL,
        BidRaw BIGINT NULL,
        AskRaw BIGINT NULL,
        Bid DECIMAL(19,8) NULL,
        Ask DECIMAL(19,8) NULL,
        Mid AS CAST(CASE WHEN Bid IS NOT NULL AND Ask IS NOT NULL THEN (Bid + Ask) / 2 ELSE NULL END AS DECIMAL(19,8)) PERSISTED,
        Spread AS CAST(CASE WHEN Bid IS NOT NULL AND Ask IS NOT NULL THEN Ask - Bid ELSE NULL END AS DECIMAL(19,8)) PERSISTED,
        BidUpdated BIT NOT NULL,
        AskUpdated BIT NOT NULL,
        QuoteType VARCHAR(10) NOT NULL,
        SourceMode VARCHAR(16) NOT NULL,
        SessionCloseRaw BIGINT NULL,
        SessionClose DECIMAL(19,8) NULL,
        IsTechnicalEvent BIT NOT NULL CONSTRAINT DF_tick_HK50_Technical DEFAULT 0,
        ReceivedAtUtc DATETIME2(3) NOT NULL CONSTRAINT DF_tick_HK50_Received DEFAULT SYSUTCDATETIME(),
        IngestRunID UNIQUEIDENTIFIER NULL,
        EventHash BINARY(32) NOT NULL,
        CONSTRAINT FK_tick_HK50_Symbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID)
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('tick.HK50') AND name = 'UX_tick_HK50_EventHash')
    CREATE UNIQUE NONCLUSTERED INDEX UX_tick_HK50_EventHash ON tick.HK50(EventHash) WITH (IGNORE_DUP_KEY = ON);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('tick.HK50') AND name = 'IX_tick_HK50_Time')
    CREATE NONCLUSTERED INDEX IX_tick_HK50_Time ON tick.HK50(TickTimeUtc DESC) INCLUDE (Bid, Ask, Mid, Spread, SourceMode);
GO

IF OBJECT_ID('tick.J225', 'U') IS NULL
BEGIN
    CREATE TABLE tick.J225 (
        TickID BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY CLUSTERED,
        SymbolID INT NOT NULL,
        CTraderSymbolId BIGINT NOT NULL,
        CTraderSymbolName NVARCHAR(80) NOT NULL,
        TickTimeUtc DATETIME2(3) NOT NULL,
        SourceTimestampMs BIGINT NULL,
        BidRaw BIGINT NULL,
        AskRaw BIGINT NULL,
        Bid DECIMAL(19,8) NULL,
        Ask DECIMAL(19,8) NULL,
        Mid AS CAST(CASE WHEN Bid IS NOT NULL AND Ask IS NOT NULL THEN (Bid + Ask) / 2 ELSE NULL END AS DECIMAL(19,8)) PERSISTED,
        Spread AS CAST(CASE WHEN Bid IS NOT NULL AND Ask IS NOT NULL THEN Ask - Bid ELSE NULL END AS DECIMAL(19,8)) PERSISTED,
        BidUpdated BIT NOT NULL,
        AskUpdated BIT NOT NULL,
        QuoteType VARCHAR(10) NOT NULL,
        SourceMode VARCHAR(16) NOT NULL,
        SessionCloseRaw BIGINT NULL,
        SessionClose DECIMAL(19,8) NULL,
        IsTechnicalEvent BIT NOT NULL CONSTRAINT DF_tick_J225_Technical DEFAULT 0,
        ReceivedAtUtc DATETIME2(3) NOT NULL CONSTRAINT DF_tick_J225_Received DEFAULT SYSUTCDATETIME(),
        IngestRunID UNIQUEIDENTIFIER NULL,
        EventHash BINARY(32) NOT NULL,
        CONSTRAINT FK_tick_J225_Symbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID)
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('tick.J225') AND name = 'UX_tick_J225_EventHash')
    CREATE UNIQUE NONCLUSTERED INDEX UX_tick_J225_EventHash ON tick.J225(EventHash) WITH (IGNORE_DUP_KEY = ON);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('tick.J225') AND name = 'IX_tick_J225_Time')
    CREATE NONCLUSTERED INDEX IX_tick_J225_Time ON tick.J225(TickTimeUtc DESC) INCLUDE (Bid, Ask, Mid, Spread, SourceMode);
GO

IF OBJECT_ID('tick.SP35', 'U') IS NULL
BEGIN
    CREATE TABLE tick.SP35 (
        TickID BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY CLUSTERED,
        SymbolID INT NOT NULL,
        CTraderSymbolId BIGINT NOT NULL,
        CTraderSymbolName NVARCHAR(80) NOT NULL,
        TickTimeUtc DATETIME2(3) NOT NULL,
        SourceTimestampMs BIGINT NULL,
        BidRaw BIGINT NULL,
        AskRaw BIGINT NULL,
        Bid DECIMAL(19,8) NULL,
        Ask DECIMAL(19,8) NULL,
        Mid AS CAST(CASE WHEN Bid IS NOT NULL AND Ask IS NOT NULL THEN (Bid + Ask) / 2 ELSE NULL END AS DECIMAL(19,8)) PERSISTED,
        Spread AS CAST(CASE WHEN Bid IS NOT NULL AND Ask IS NOT NULL THEN Ask - Bid ELSE NULL END AS DECIMAL(19,8)) PERSISTED,
        BidUpdated BIT NOT NULL,
        AskUpdated BIT NOT NULL,
        QuoteType VARCHAR(10) NOT NULL,
        SourceMode VARCHAR(16) NOT NULL,
        SessionCloseRaw BIGINT NULL,
        SessionClose DECIMAL(19,8) NULL,
        IsTechnicalEvent BIT NOT NULL CONSTRAINT DF_tick_SP35_Technical DEFAULT 0,
        ReceivedAtUtc DATETIME2(3) NOT NULL CONSTRAINT DF_tick_SP35_Received DEFAULT SYSUTCDATETIME(),
        IngestRunID UNIQUEIDENTIFIER NULL,
        EventHash BINARY(32) NOT NULL,
        CONSTRAINT FK_tick_SP35_Symbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID)
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('tick.SP35') AND name = 'UX_tick_SP35_EventHash')
    CREATE UNIQUE NONCLUSTERED INDEX UX_tick_SP35_EventHash ON tick.SP35(EventHash) WITH (IGNORE_DUP_KEY = ON);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('tick.SP35') AND name = 'IX_tick_SP35_Time')
    CREATE NONCLUSTERED INDEX IX_tick_SP35_Time ON tick.SP35(TickTimeUtc DESC) INCLUDE (Bid, Ask, Mid, Spread, SourceMode);
GO

IF OBJECT_ID('tick.UK100', 'U') IS NULL
BEGIN
    CREATE TABLE tick.UK100 (
        TickID BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY CLUSTERED,
        SymbolID INT NOT NULL,
        CTraderSymbolId BIGINT NOT NULL,
        CTraderSymbolName NVARCHAR(80) NOT NULL,
        TickTimeUtc DATETIME2(3) NOT NULL,
        SourceTimestampMs BIGINT NULL,
        BidRaw BIGINT NULL,
        AskRaw BIGINT NULL,
        Bid DECIMAL(19,8) NULL,
        Ask DECIMAL(19,8) NULL,
        Mid AS CAST(CASE WHEN Bid IS NOT NULL AND Ask IS NOT NULL THEN (Bid + Ask) / 2 ELSE NULL END AS DECIMAL(19,8)) PERSISTED,
        Spread AS CAST(CASE WHEN Bid IS NOT NULL AND Ask IS NOT NULL THEN Ask - Bid ELSE NULL END AS DECIMAL(19,8)) PERSISTED,
        BidUpdated BIT NOT NULL,
        AskUpdated BIT NOT NULL,
        QuoteType VARCHAR(10) NOT NULL,
        SourceMode VARCHAR(16) NOT NULL,
        SessionCloseRaw BIGINT NULL,
        SessionClose DECIMAL(19,8) NULL,
        IsTechnicalEvent BIT NOT NULL CONSTRAINT DF_tick_UK100_Technical DEFAULT 0,
        ReceivedAtUtc DATETIME2(3) NOT NULL CONSTRAINT DF_tick_UK100_Received DEFAULT SYSUTCDATETIME(),
        IngestRunID UNIQUEIDENTIFIER NULL,
        EventHash BINARY(32) NOT NULL,
        CONSTRAINT FK_tick_UK100_Symbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID)
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('tick.UK100') AND name = 'UX_tick_UK100_EventHash')
    CREATE UNIQUE NONCLUSTERED INDEX UX_tick_UK100_EventHash ON tick.UK100(EventHash) WITH (IGNORE_DUP_KEY = ON);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('tick.UK100') AND name = 'IX_tick_UK100_Time')
    CREATE NONCLUSTERED INDEX IX_tick_UK100_Time ON tick.UK100(TickTimeUtc DESC) INCLUDE (Bid, Ask, Mid, Spread, SourceMode);
GO

IF OBJECT_ID('tick.US500', 'U') IS NULL
BEGIN
    CREATE TABLE tick.US500 (
        TickID BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY CLUSTERED,
        SymbolID INT NOT NULL,
        CTraderSymbolId BIGINT NOT NULL,
        CTraderSymbolName NVARCHAR(80) NOT NULL,
        TickTimeUtc DATETIME2(3) NOT NULL,
        SourceTimestampMs BIGINT NULL,
        BidRaw BIGINT NULL,
        AskRaw BIGINT NULL,
        Bid DECIMAL(19,8) NULL,
        Ask DECIMAL(19,8) NULL,
        Mid AS CAST(CASE WHEN Bid IS NOT NULL AND Ask IS NOT NULL THEN (Bid + Ask) / 2 ELSE NULL END AS DECIMAL(19,8)) PERSISTED,
        Spread AS CAST(CASE WHEN Bid IS NOT NULL AND Ask IS NOT NULL THEN Ask - Bid ELSE NULL END AS DECIMAL(19,8)) PERSISTED,
        BidUpdated BIT NOT NULL,
        AskUpdated BIT NOT NULL,
        QuoteType VARCHAR(10) NOT NULL,
        SourceMode VARCHAR(16) NOT NULL,
        SessionCloseRaw BIGINT NULL,
        SessionClose DECIMAL(19,8) NULL,
        IsTechnicalEvent BIT NOT NULL CONSTRAINT DF_tick_US500_Technical DEFAULT 0,
        ReceivedAtUtc DATETIME2(3) NOT NULL CONSTRAINT DF_tick_US500_Received DEFAULT SYSUTCDATETIME(),
        IngestRunID UNIQUEIDENTIFIER NULL,
        EventHash BINARY(32) NOT NULL,
        CONSTRAINT FK_tick_US500_Symbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID)
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('tick.US500') AND name = 'UX_tick_US500_EventHash')
    CREATE UNIQUE NONCLUSTERED INDEX UX_tick_US500_EventHash ON tick.US500(EventHash) WITH (IGNORE_DUP_KEY = ON);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('tick.US500') AND name = 'IX_tick_US500_Time')
    CREATE NONCLUSTERED INDEX IX_tick_US500_Time ON tick.US500(TickTimeUtc DESC) INCLUDE (Bid, Ask, Mid, Spread, SourceMode);
GO

IF OBJECT_ID('tick.US100', 'U') IS NULL
BEGIN
    CREATE TABLE tick.US100 (
        TickID BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY CLUSTERED,
        SymbolID INT NOT NULL,
        CTraderSymbolId BIGINT NOT NULL,
        CTraderSymbolName NVARCHAR(80) NOT NULL,
        TickTimeUtc DATETIME2(3) NOT NULL,
        SourceTimestampMs BIGINT NULL,
        BidRaw BIGINT NULL,
        AskRaw BIGINT NULL,
        Bid DECIMAL(19,8) NULL,
        Ask DECIMAL(19,8) NULL,
        Mid AS CAST(CASE WHEN Bid IS NOT NULL AND Ask IS NOT NULL THEN (Bid + Ask) / 2 ELSE NULL END AS DECIMAL(19,8)) PERSISTED,
        Spread AS CAST(CASE WHEN Bid IS NOT NULL AND Ask IS NOT NULL THEN Ask - Bid ELSE NULL END AS DECIMAL(19,8)) PERSISTED,
        BidUpdated BIT NOT NULL,
        AskUpdated BIT NOT NULL,
        QuoteType VARCHAR(10) NOT NULL,
        SourceMode VARCHAR(16) NOT NULL,
        SessionCloseRaw BIGINT NULL,
        SessionClose DECIMAL(19,8) NULL,
        IsTechnicalEvent BIT NOT NULL CONSTRAINT DF_tick_US100_Technical DEFAULT 0,
        ReceivedAtUtc DATETIME2(3) NOT NULL CONSTRAINT DF_tick_US100_Received DEFAULT SYSUTCDATETIME(),
        IngestRunID UNIQUEIDENTIFIER NULL,
        EventHash BINARY(32) NOT NULL,
        CONSTRAINT FK_tick_US100_Symbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID)
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('tick.US100') AND name = 'UX_tick_US100_EventHash')
    CREATE UNIQUE NONCLUSTERED INDEX UX_tick_US100_EventHash ON tick.US100(EventHash) WITH (IGNORE_DUP_KEY = ON);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('tick.US100') AND name = 'IX_tick_US100_Time')
    CREATE NONCLUSTERED INDEX IX_tick_US100_Time ON tick.US100(TickTimeUtc DESC) INCLUDE (Bid, Ask, Mid, Spread, SourceMode);
GO

IF OBJECT_ID('tick.US30', 'U') IS NULL
BEGIN
    CREATE TABLE tick.US30 (
        TickID BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY CLUSTERED,
        SymbolID INT NOT NULL,
        CTraderSymbolId BIGINT NOT NULL,
        CTraderSymbolName NVARCHAR(80) NOT NULL,
        TickTimeUtc DATETIME2(3) NOT NULL,
        SourceTimestampMs BIGINT NULL,
        BidRaw BIGINT NULL,
        AskRaw BIGINT NULL,
        Bid DECIMAL(19,8) NULL,
        Ask DECIMAL(19,8) NULL,
        Mid AS CAST(CASE WHEN Bid IS NOT NULL AND Ask IS NOT NULL THEN (Bid + Ask) / 2 ELSE NULL END AS DECIMAL(19,8)) PERSISTED,
        Spread AS CAST(CASE WHEN Bid IS NOT NULL AND Ask IS NOT NULL THEN Ask - Bid ELSE NULL END AS DECIMAL(19,8)) PERSISTED,
        BidUpdated BIT NOT NULL,
        AskUpdated BIT NOT NULL,
        QuoteType VARCHAR(10) NOT NULL,
        SourceMode VARCHAR(16) NOT NULL,
        SessionCloseRaw BIGINT NULL,
        SessionClose DECIMAL(19,8) NULL,
        IsTechnicalEvent BIT NOT NULL CONSTRAINT DF_tick_US30_Technical DEFAULT 0,
        ReceivedAtUtc DATETIME2(3) NOT NULL CONSTRAINT DF_tick_US30_Received DEFAULT SYSUTCDATETIME(),
        IngestRunID UNIQUEIDENTIFIER NULL,
        EventHash BINARY(32) NOT NULL,
        CONSTRAINT FK_tick_US30_Symbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID)
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('tick.US30') AND name = 'UX_tick_US30_EventHash')
    CREATE UNIQUE NONCLUSTERED INDEX UX_tick_US30_EventHash ON tick.US30(EventHash) WITH (IGNORE_DUP_KEY = ON);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('tick.US30') AND name = 'IX_tick_US30_Time')
    CREATE NONCLUSTERED INDEX IX_tick_US30_Time ON tick.US30(TickTimeUtc DESC) INCLUDE (Bid, Ask, Mid, Spread, SourceMode);
GO

IF OBJECT_ID('tick.GOLD', 'U') IS NULL
BEGIN
    CREATE TABLE tick.GOLD (
        TickID BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY CLUSTERED,
        SymbolID INT NOT NULL,
        CTraderSymbolId BIGINT NOT NULL,
        CTraderSymbolName NVARCHAR(80) NOT NULL,
        TickTimeUtc DATETIME2(3) NOT NULL,
        SourceTimestampMs BIGINT NULL,
        BidRaw BIGINT NULL,
        AskRaw BIGINT NULL,
        Bid DECIMAL(19,8) NULL,
        Ask DECIMAL(19,8) NULL,
        Mid AS CAST(CASE WHEN Bid IS NOT NULL AND Ask IS NOT NULL THEN (Bid + Ask) / 2 ELSE NULL END AS DECIMAL(19,8)) PERSISTED,
        Spread AS CAST(CASE WHEN Bid IS NOT NULL AND Ask IS NOT NULL THEN Ask - Bid ELSE NULL END AS DECIMAL(19,8)) PERSISTED,
        BidUpdated BIT NOT NULL,
        AskUpdated BIT NOT NULL,
        QuoteType VARCHAR(10) NOT NULL,
        SourceMode VARCHAR(16) NOT NULL,
        SessionCloseRaw BIGINT NULL,
        SessionClose DECIMAL(19,8) NULL,
        IsTechnicalEvent BIT NOT NULL CONSTRAINT DF_tick_GOLD_Technical DEFAULT 0,
        ReceivedAtUtc DATETIME2(3) NOT NULL CONSTRAINT DF_tick_GOLD_Received DEFAULT SYSUTCDATETIME(),
        IngestRunID UNIQUEIDENTIFIER NULL,
        EventHash BINARY(32) NOT NULL,
        CONSTRAINT FK_tick_GOLD_Symbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID)
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('tick.GOLD') AND name = 'UX_tick_GOLD_EventHash')
    CREATE UNIQUE NONCLUSTERED INDEX UX_tick_GOLD_EventHash ON tick.GOLD(EventHash) WITH (IGNORE_DUP_KEY = ON);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('tick.GOLD') AND name = 'IX_tick_GOLD_Time')
    CREATE NONCLUSTERED INDEX IX_tick_GOLD_Time ON tick.GOLD(TickTimeUtc DESC) INCLUDE (Bid, Ask, Mid, Spread, SourceMode);
GO

IF OBJECT_ID('tick.BTCUSD', 'U') IS NULL
BEGIN
    CREATE TABLE tick.BTCUSD (
        TickID BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY CLUSTERED,
        SymbolID INT NOT NULL,
        CTraderSymbolId BIGINT NOT NULL,
        CTraderSymbolName NVARCHAR(80) NOT NULL,
        TickTimeUtc DATETIME2(3) NOT NULL,
        SourceTimestampMs BIGINT NULL,
        BidRaw BIGINT NULL,
        AskRaw BIGINT NULL,
        Bid DECIMAL(19,8) NULL,
        Ask DECIMAL(19,8) NULL,
        Mid AS CAST(CASE WHEN Bid IS NOT NULL AND Ask IS NOT NULL THEN (Bid + Ask) / 2 ELSE NULL END AS DECIMAL(19,8)) PERSISTED,
        Spread AS CAST(CASE WHEN Bid IS NOT NULL AND Ask IS NOT NULL THEN Ask - Bid ELSE NULL END AS DECIMAL(19,8)) PERSISTED,
        BidUpdated BIT NOT NULL,
        AskUpdated BIT NOT NULL,
        QuoteType VARCHAR(10) NOT NULL,
        SourceMode VARCHAR(16) NOT NULL,
        SessionCloseRaw BIGINT NULL,
        SessionClose DECIMAL(19,8) NULL,
        IsTechnicalEvent BIT NOT NULL CONSTRAINT DF_tick_BTCUSD_Technical DEFAULT 0,
        ReceivedAtUtc DATETIME2(3) NOT NULL CONSTRAINT DF_tick_BTCUSD_Received DEFAULT SYSUTCDATETIME(),
        IngestRunID UNIQUEIDENTIFIER NULL,
        EventHash BINARY(32) NOT NULL,
        CONSTRAINT FK_tick_BTCUSD_Symbol FOREIGN KEY (SymbolID) REFERENCES DWH.Dim_Symbol(SymbolID)
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('tick.BTCUSD') AND name = 'UX_tick_BTCUSD_EventHash')
    CREATE UNIQUE NONCLUSTERED INDEX UX_tick_BTCUSD_EventHash ON tick.BTCUSD(EventHash) WITH (IGNORE_DUP_KEY = ON);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('tick.BTCUSD') AND name = 'IX_tick_BTCUSD_Time')
    CREATE NONCLUSTERED INDEX IX_tick_BTCUSD_Time ON tick.BTCUSD(TickTimeUtc DESC) INCLUDE (Bid, Ask, Mid, Spread, SourceMode);
GO

/* ============================================================
   Convenience views
   ============================================================ */

GO
CREATE OR ALTER VIEW tick.v_SymbolMap AS
SELECT
    m.SymbolID,
    m.SenSymbol,
    m.AssetType,
    m.CTraderSymbolId,
    m.CTraderSymbolName,
    m.CTraderDescription,
    m.CTraderEnabled,
    m.Digits,
    m.PipPosition,
    m.MappingStatus,
    m.MappingScore,
    m.Enabled,
    m.LastSyncedAtUtc,
    m.Notes
FROM tick.SymbolMap m;
GO

CREATE OR ALTER VIEW tick.v_IngestHealth AS
SELECT
    m.SymbolID,
    m.SenSymbol,
    m.AssetType,
    m.CTraderSymbolId,
    m.CTraderSymbolName,
    m.MappingStatus,
    s.Status,
    s.LastLiveTickTimeUtc,
    s.LastHistoricalTickTimeUtc,
    s.LastSourceTimestampMs,
    s.LastBid,
    s.LastAsk,
    s.LastWriteAtUtc,
    s.LastHeartbeatAtUtc,
    s.TotalTicksInserted,
    s.ConsecutiveErrors,
    s.LastError,
    s.UpdatedAtUtc
FROM tick.SymbolMap m
LEFT JOIN tick.IngestState s ON s.SymbolID = m.SymbolID;
GO

PRINT 'cTrader FTMO tick schema ready: schema [tick], metadata tables, 11 per-symbol tick tables.';
GO
