/* ============================================================
   05_seed_symbols.sql
   Project   : SEN05 Data Provider
   Database  : SEN05_AutoTrading

   PURPOSE:
     Step 5 of 6 - seed DWH.Dim_Symbol with the 37 instruments used by the
     DP Program V3 runtime.

   SOURCE OF TRUTH:
     DWH.Dim_Symbol itself is the canonical V3 runtime symbol list; the
     Python runtime reads it directly and holds no separate symbol list.
     This SQL file is the install-time database seed for a fresh warehouse.
     If the reviewed symbol contract changes, update this seed in the same
     change so a fresh install matches the running warehouse.

   SAFE TO RE-RUN:
     MERGE keeps existing rows aligned without deleting historical data.
   ============================================================ */

USE SEN05_AutoTrading;
GO

MERGE DWH.Dim_Symbol AS target
USING (VALUES
    ( 2,  N'FR40',   N'CAPITALCOM', N'Indice', 1),
    ( 3,  N'DE40',   N'CAPITALCOM', N'Indice', 1),
    ( 4,  N'HK50',   N'CAPITALCOM', N'Indice', 1),
    ( 5,  N'J225',   N'CAPITALCOM', N'Indice', 1),
    ( 6,  N'SP35',   N'CAPITALCOM', N'Indice', 1),
    ( 7,  N'UK100',  N'CAPITALCOM', N'Indice', 1),
    ( 8,  N'US500',  N'CAPITALCOM', N'Indice', 1),
    ( 9,  N'US100',  N'CAPITALCOM', N'Indice', 1),
    (10,  N'US30',   N'CAPITALCOM', N'Indice', 1),
    (11,  N'AUDCAD', N'CAPITALCOM', N'FOREX',  1),
    (12,  N'AUDJPY', N'CAPITALCOM', N'FOREX',  1),
    (13,  N'AUDNZD', N'CAPITALCOM', N'FOREX',  1),
    (14,  N'AUDCHF', N'CAPITALCOM', N'FOREX',  1),
    (15,  N'AUDUSD', N'CAPITALCOM', N'FOREX',  1),
    (16,  N'GBPAUD', N'CAPITALCOM', N'FOREX',  1),
    (17,  N'GBPCAD', N'CAPITALCOM', N'FOREX',  1),
    (18,  N'GBPJPY', N'CAPITALCOM', N'FOREX',  1),
    (19,  N'GBPNZD', N'CAPITALCOM', N'FOREX',  1),
    (20,  N'GBPCHF', N'CAPITALCOM', N'FOREX',  1),
    (21,  N'GBPUSD', N'CAPITALCOM', N'FOREX',  1),
    (22,  N'CADJPY', N'CAPITALCOM', N'FOREX',  1),
    (23,  N'CADCHF', N'CAPITALCOM', N'FOREX',  1),
    (24,  N'EURAUD', N'CAPITALCOM', N'FOREX',  1),
    (25,  N'EURGBP', N'CAPITALCOM', N'FOREX',  1),
    (26,  N'EURCAD', N'CAPITALCOM', N'FOREX',  1),
    (27,  N'EURJPY', N'CAPITALCOM', N'FOREX',  1),
    (28,  N'EURNZD', N'CAPITALCOM', N'FOREX',  1),
    (32,  N'EURCHF', N'CAPITALCOM', N'FOREX',  1),
    (33,  N'EURUSD', N'CAPITALCOM', N'FOREX',  1),
    (34,  N'NZDCAD', N'CAPITALCOM', N'FOREX',  1),
    (35,  N'NZDJPY', N'CAPITALCOM', N'FOREX',  1),
    (36,  N'NZDUSD', N'CAPITALCOM', N'FOREX',  1),
    (37,  N'USDCAD', N'CAPITALCOM', N'FOREX',  1),
    (41,  N'USDJPY', N'CAPITALCOM', N'FOREX',  1),
    (48,  N'USDCHF', N'CAPITALCOM', N'FOREX',  1),
    (56,  N'GOLD',   N'CAPITALCOM', N'Metal',  1),
    (81,  N'BTCUSD', N'CAPITALCOM', N'Crypto', 1)
) AS src (SymbolID, Symbol, BrokerChannel, AssetType, IsActive)
ON target.SymbolID = src.SymbolID
WHEN MATCHED AND EXISTS (
    SELECT target.Symbol, target.BrokerChannel, target.AssetType, target.IsActive
    EXCEPT
    SELECT src.Symbol, src.BrokerChannel, src.AssetType, src.IsActive
) THEN
    UPDATE SET
        Symbol = src.Symbol,
        BrokerChannel = src.BrokerChannel,
        AssetType = src.AssetType,
        IsActive = src.IsActive
WHEN NOT MATCHED THEN
    INSERT (SymbolID, Symbol, BrokerChannel, AssetType, IsActive)
    VALUES (src.SymbolID, src.Symbol, src.BrokerChannel, src.AssetType, src.IsActive);
GO

PRINT 'DWH.Dim_Symbol seeded/merged (37 instruments).';
GO
