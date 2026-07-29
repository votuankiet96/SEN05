/* ============================================================
   05_seed_symbols.sql
   Project   : SEN05 Data Provider
   Database  : SEN05_AutoTrading

   PURPOSE:
     Step 5 of 6 - seed DWH.Dim_Symbol with the 37 instruments used by the
     DP Program V3 runtime.

   SOURCE OF TRUTH:
     The canonical V3 runtime list is configuration.py::_SYMBOLS.
     This SQL file is the install-time database seed for a fresh warehouse.
     If the approved symbol contract changes in configuration.py, update this seed
     in the same change.

   SAFE TO RE-RUN:
     MERGE keeps existing rows aligned without deleting historical data.
   ============================================================ */

USE SEN05_AutoTrading;
GO

MERGE DWH.Dim_Symbol AS target
USING (VALUES
    ( 2,  N'FR40',   NULL, N'Indice', 1),
    ( 3,  N'DE40',   NULL, N'Indice', 1),
    ( 4,  N'HK50',   NULL, N'Indice', 1),
    ( 5,  N'J225',   NULL, N'Indice', 1),
    ( 6,  N'SP35',   NULL, N'Indice', 1),
    ( 7,  N'UK100',  NULL, N'Indice', 1),
    ( 8,  N'US500',  NULL, N'Indice', 1),
    ( 9,  N'US100',  NULL, N'Indice', 1),
    (10,  N'US30',   NULL, N'Indice', 1),
    (11,  N'AUDCAD', NULL, N'FOREX',  1),
    (12,  N'AUDJPY', NULL, N'FOREX',  1),
    (13,  N'AUDNZD', NULL, N'FOREX',  1),
    (14,  N'AUDCHF', NULL, N'FOREX',  1),
    (15,  N'AUDUSD', NULL, N'FOREX',  1),
    (16,  N'GBPAUD', NULL, N'FOREX',  1),
    (17,  N'GBPCAD', NULL, N'FOREX',  1),
    (18,  N'GBPJPY', NULL, N'FOREX',  1),
    (19,  N'GBPNZD', NULL, N'FOREX',  1),
    (20,  N'GBPCHF', NULL, N'FOREX',  1),
    (21,  N'GBPUSD', NULL, N'FOREX',  1),
    (22,  N'CADJPY', NULL, N'FOREX',  1),
    (23,  N'CADCHF', NULL, N'FOREX',  1),
    (24,  N'EURAUD', NULL, N'FOREX',  1),
    (25,  N'EURGBP', NULL, N'FOREX',  1),
    (26,  N'EURCAD', NULL, N'FOREX',  1),
    (27,  N'EURJPY', NULL, N'FOREX',  1),
    (28,  N'EURNZD', NULL, N'FOREX',  1),
    (32,  N'EURCHF', NULL, N'FOREX',  1),
    (33,  N'EURUSD', NULL, N'FOREX',  1),
    (34,  N'NZDCAD', NULL, N'FOREX',  1),
    (35,  N'NZDJPY', NULL, N'FOREX',  1),
    (36,  N'NZDUSD', NULL, N'FOREX',  1),
    (37,  N'USDCAD', NULL, N'FOREX',  1),
    (41,  N'USDJPY', NULL, N'FOREX',  1),
    (48,  N'USDCHF', NULL, N'FOREX',  1),
    (56,  N'GOLD',   NULL, N'Metal',  1),
    (81,  N'BTCUSD', NULL, N'Crypto', 1)
) AS src (SymbolID, Symbol, RefName, AssetType, IsActive)
ON target.SymbolID = src.SymbolID
WHEN MATCHED AND EXISTS (
    SELECT target.Symbol, target.RefName, target.AssetType, target.IsActive
    EXCEPT
    SELECT src.Symbol, src.RefName, src.AssetType, src.IsActive
) THEN
    UPDATE SET
        Symbol = src.Symbol,
        RefName = src.RefName,
        AssetType = src.AssetType,
        IsActive = src.IsActive
WHEN NOT MATCHED THEN
    INSERT (SymbolID, Symbol, RefName, AssetType, IsActive)
    VALUES (src.SymbolID, src.Symbol, src.RefName, src.AssetType, src.IsActive);
GO

PRINT 'DWH.Dim_Symbol seeded/merged (37 instruments).';
GO
