/* ============================================================
   INSTALLER NOTE:
   - This file includes the full data_provider runtime schema, including
     `06_active_task.sql`.
   - The current runtime pulls all 15 timeframes directly from
     TradingView/Capital.com by default. Computed timeframe aggregation remains
     available only as a fallback/maintenance path.
   ============================================================ */

/* ============================================================
   00_run_all.sql
   Project   : Auto Trading Data Warehouse
   Database  : SEN05_AutoTrading  (SQL Server 2022)

   ============================================================
   HOW TO RUN THIS FILE:
     This file uses SQLCMD mode (:r command) to include other files.
     In SSMS: Query menu → SQLCMD Mode → then press F5.
     In sqlcmd.exe: sqlcmd -S <server> -i "00_run_all.sql"

     Alternatively, run each file individually in the order below.
   ============================================================


   ============================================================
   OVERVIEW — WHAT IS THIS DATABASE AND WHY DOES IT EXIST?
   ============================================================

   This database stores historical price data (candles/bars) for
   37 financial instruments across 15 different timeframes.

   WHAT IS A "CANDLE" (OHLCV BAR)?
     Every financial instrument (e.g. EURUSD, Gold, S&P 500) has a
     price that changes every second. Instead of storing every tick,
     we group price movements into fixed time windows called "candles".

     For example, a 1-hour (H1) candle for EURUSD at 14:00 says:
       Open  = 1.08500  (the price at 14:00:00 when the hour started)
       High  = 1.08750  (the highest price reached during 14:00–15:00)
       Low   = 1.08300  (the lowest price reached during 14:00–15:00)
       Close = 1.08600  (the price at 14:59:59 when the hour ended)
       Volume = 15,000  (how many units were traded during that hour)

     This "OHLCV" format (Open, High, Low, Close, Volume) is the
     universal standard in trading. Every chart you see on TradingView,
     Bloomberg, or any broker platform uses candles in this format.

   WHAT ARE "TIMEFRAMES"?
     The same price data can be viewed at different zoom levels:
       M5  = 5-minute candle   (very zoomed in, for fast trading)
       H1  = 1-hour candle     (medium zoom)
       D1  = 1-day candle      (zoomed out, for big-picture trends)
       W   = 1-week candle     (very zoomed out)

     This database supports 15 timeframes. The current runtime pulls all 15
     directly from TradingView/Capital.com by default. The former computed
     timeframes (M10, M20, M90, H6, H8) can still be rebuilt from smaller
     bars only when the fallback mode is explicitly enabled.

   WHAT ARE THE 37 INSTRUMENTS?
       9 Stock Indices : FR40 (France), DE40 (Germany), HK50 (HK),
                         J225 (Japan), SP35 (Spain), UK100 (UK),
                         US500, US100, US30 (US)
      26 FOREX Pairs   : EURUSD, GBPUSD, USDJPY, etc. (currency exchanges)
       1 Metal          : Gold (GOLD)
       1 Crypto         : Bitcoin (BTC/USD)

   TOTAL DATA VOLUME:
     37 instruments × 15 timeframes × thousands of historical bars each
     = millions of rows in the central Fact_OHLCV table.


   ============================================================
   DATABASE ARCHITECTURE — THE THREE-LAYER DESIGN
   ============================================================

   The database is organized into 3 layers (called "schemas"):

   ┌─────────────────────────────────────────────────────────┐
   │  LAYER 1: SEN (Staging Entry)                           │
   │                                                         │
   │  15 staging tables — one per timeframe.                 │
   │  e.g. SEN.TF_M5, SEN.TF_H1, SEN.TF_D1, etc.          │
   │                                                         │
   │  PURPOSE: Temporary landing zone for raw data.          │
   │  The Python pipeline downloads candles from TV/Capital  │
   │  and dumps them here FIRST. Data may be incomplete or   │
   │  contain duplicates at this stage — that's OK.          │
   │                                                         │
   │  Think of it like a mailroom: packages arrive here      │
   │  before being sorted and delivered to the right place.  │
   └────────────────────────┬────────────────────────────────┘
                            │
                   ETL procedures
                   (sort, validate,
                    remove duplicates)
                            │
                            ▼
   ┌─────────────────────────────────────────────────────────┐
   │  LAYER 2: DWH (Data Warehouse)                          │
   │                                                         │
   │  Central fact table: DWH.Fact_OHLCV                     │
   │  + 3 dimension tables for context:                      │
   │    - Dim_Symbol    (which instrument? EURUSD, Gold...)  │
   │    - Dim_Timeframe (which timeframe? M5, H1, D1...)    │
   │    - Dim_Date      (calendar: year, month, weekday...)  │
   │                                                         │
   │  PURPOSE: Clean, validated, deduplicated data.          │
   │  Every candle exists exactly ONCE. No duplicates.       │
   │  This is the "single source of truth."                  │
   │                                                         │
   │  Think of it like a library: books are catalogued,      │
   │  indexed, and shelved in the right place.               │
   └────────────────────────┬────────────────────────────────┘
                            │
                   Views & procedures
                   (easy-to-use interface)
                            │
                            ▼
   ┌─────────────────────────────────────────────────────────┐
   │  LAYER 3: MART (Data Mart)                              │
   │                                                         │
   │  - v_OHLCV view: human-readable candle data             │
   │  - usp_GetLatestCandles: "give me the last 200 bars"   │
   │                                                         │
   │  PURPOSE: Easy-to-use SQL-facing read interface.        │
   │  Current Python loaders read DWH.Fact_OHLCV directly;  │
   │  MART remains useful for manual SQL and light consumers.│
   │                                                         │
   │  Think of it like a library's search catalog:           │
   │  you don't go into the storage room — you use the       │
   │  search terminal to find what you need.                 │
   └─────────────────────────────────────────────────────────┘


   ============================================================
   DATA FLOW — HOW CANDLES MOVE THROUGH THE SYSTEM
   ============================================================

   Step 1: PULL DATA
     The Python pipeline (pipeline.py) connects to TradingView
     and downloads candles for all 37 symbols × 15 direct timeframes.

   Step 2: INSERT INTO STAGING
     Raw candles land in the matching SEN.TF_* staging table.
     e.g. EURUSD H1 candles go into SEN.TF_H1.
     Duplicates are blocked by a unique constraint (same symbol + same time = rejected).

   Step 3: ETL — MOVE TO FACT TABLE
     Stored procedure DWH.usp_LoadDirect reads processed rows from staging
     and inserts them into DWH.Fact_OHLCV (the central table).
     It checks "does this candle already exist?" before inserting — so it's
     safe to run multiple times without creating duplicates.

   Step 4: OPTIONAL FALLBACK DERIVED TIMEFRAMES
     Stored procedure DWH.usp_AggregateFromStaging can rebuild the former
     computed timeframes when fallback mode is explicitly enabled:
       - Takes M5 candles and merges every 2 into M10, every 4 into M20
       - Takes M30 candles and merges every 3 into M90
       - Takes H3 candles and merges every 2 into H6
       - Takes H4 candles and merges every 2 into H8
     The "merging" means: Open = first bar's open, Close = last bar's close,
     High = highest high, Low = lowest low, Volume = sum of all volumes.

   Step 5: READ BY RUNTIME AND TOOLS
     Current Python loaders query DWH.Fact_OHLCV directly for speed and repair
     workflows. MART.usp_GetLatestCandles and MART.v_OHLCV remain available as
     SQL-facing convenience APIs.


   ============================================================
   WHAT EACH FILE CREATES — COMPONENT BREAKDOWN
   ============================================================

   FILE 1: 01_setup_database.sql
   ─────────────────────────────
   WHAT IT DOES:
     - Creates the database itself (SEN05_AutoTrading)
     - Creates the 3 schemas (SEN, DWH, MART)
   WHY IT'S NEEDED:
     Everything else depends on the database and schemas existing.
     This MUST run first. It's the foundation.
   SAFE TO RE-RUN: Yes — checks "does it exist?" before creating.

   FILE 2: 02_core_tables.sql
   ─────────────────────────────
   WHAT IT DOES:
     Creates the core tables that hold all the important data:

     a) DWH.Dim_Symbol (37 rows)
        A lookup table: "SymbolID 33 = EURUSD, it's a FOREX pair"
        Every candle stores a SymbolID number, and this table tells
        you what that number means.

     b) DWH.Dim_Timeframe (15 rows)
        A lookup table: "TimeframeID 7 = H1, it's a 1-hour candle"
        Every candle stores a TimeframeID number, and this table tells
        you what that number means.

     c) DWH.Dim_Date (10,227 rows — one per calendar day, 2008 to 2035)
        A calendar reference: for any date, instantly get the year,
        month, day name, whether it's a weekend, etc.
        Pre-computed once at setup so queries don't need to calculate
        these things every time.

     d) DWH.Fact_OHLCV (millions of rows — the central table)
        THE most important table. One row = one candle.
        Stores: which symbol, which timeframe, what time, O/H/L/C/Volume.
        Has a unique constraint: you cannot have two candles with the same
        symbol + timeframe + time (prevents duplicates).

     e) dbo.Symbol (37 rows — master list)
        The "source of truth" for instrument definitions.
        Contains: ID, runtime Capital.com ticker, legacy/reference name, asset type.
        Changes here propagate to DWH.Dim_Symbol automatically.

   WHY IT'S NEEDED:
     These are the permanent storage structures. Without them,
     there's nowhere to put the candle data.

   FILE 3: 03_staging_tables.sql
   ─────────────────────────────
   WHAT IT DOES:
     Creates 15 staging tables in the SEN schema — one per timeframe.
     SEN.TF_M5, SEN.TF_M10, SEN.TF_M15, ... SEN.TF_D1, SEN.TF_W

     All 15 have the identical structure:
       - RawID (auto-generated row number)
       - SymbolID + BarTime (which instrument, what time)
       - Price columns (Open, High, Low, Close, Volume)
       - ReceivedAt (when was this row inserted — for monitoring)
       - IsProcessed (0 = just arrived, 1 = ready for ETL to pick up)

   WHY IT'S NEEDED:
     Staging tables act as a buffer between the outside world (TradingView)
     and the clean warehouse (Fact_OHLCV). This separation means:
     - If TradingView sends bad data, it's quarantined in staging
     - The pipeline can mark rows as "ready" (IsProcessed=1) only after
       confirming they're complete — partial/open candles are held back
     - If something goes wrong during ETL, the raw data is still here
       for re-processing

   FILE 4: 04_business_objects.sql
   ─────────────────────────────
   WHAT IT DOES:
     Creates the "business logic" — the procedures and views that
     make the data useful:

     a) MART.v_OHLCV (View)
        A "virtual table" that joins Fact_OHLCV with all dimension tables.
        Instead of seeing "SymbolID=33, TimeframeID=7", you see
        "Symbol=EURUSD, Timeframe=H1" — much more human-friendly.
        No data is stored; it computes the join on-the-fly when queried.

     b) MART.usp_GetLatestCandles (Stored Procedure)
        A reusable query: "Give me the latest 500 candles for EURUSD H1."
        SQL tools can call this instead of writing raw joins.
        Returns newest candles first (most recent = row 1).

     c) DWH.usp_LoadDirect (ETL Procedure)
        The "conveyor belt" that moves data from staging → Fact_OHLCV.
        Used for the 15 timeframes pulled directly from TradingView/Capital.com.
        Checks for duplicates before inserting (safe to run repeatedly).
        Has a security whitelist: only accepts the 15 known table names
        to prevent SQL injection attacks.

     d) DWH.usp_AggregateFromStaging (Fallback ETL Procedure)
        The "calculator" that can rebuild the 5 fallback computed timeframes.
        Takes smaller candles, groups them into time buckets, and
        merges them into larger candles using OHLCV aggregation rules.
        Also has whitelist security and idempotency (safe to re-run).

   WHY IT'S NEEDED:
     Raw tables are useless without logic to move, transform, and
     serve the data. These procedures are the "engine" of the system.

   FILE 5: 05_verify.sql
   ─────────────────────────────
   WHAT IT DOES:
     Runs 5 check queries to confirm everything was created correctly:
       1. Lists all tables (expect 20: 15 staging + ActiveTask + 4 DWH)
       2. Lists all procedures and views (expect 4)
       3. Shows all 37 symbols
       4. Shows all 15 timeframes
       5. Confirms the calendar has 10,227 days (2008–2035)

   WHY IT'S NEEDED:
     A quick health check. If any number is wrong, something failed
     during setup and needs investigation.


   ============================================================
   KEY DESIGN DECISIONS — WHY WAS IT BUILT THIS WAY?
   ============================================================

   Q: Why separate Staging and Fact instead of inserting directly?
   A: Safety. If TradingView sends corrupted data, it stays in staging.
      The fact table only gets data that passes validation.
      If ETL fails mid-way, you can fix the issue and re-run without
      pulling from TradingView/Capital.com again — the raw data is still in staging.

   Q: Why 3 schemas instead of putting everything in one place?
   A: Separation of concerns. The Python pipeline writes to SEN staging,
      then ETL moves data from SEN → DWH. Runtime loaders currently read
      DWH.Fact_OHLCV directly, while MART provides a stable SQL-facing
      convenience layer.

   Q: Why dimension tables instead of just storing "EURUSD" as text?
   A: Efficiency and consistency. "EURUSD" is 6 characters × millions
      of rows = lots of wasted space. SymbolID=33 is just 4 bytes.
      Also, if a symbol name changes (e.g. broker renames it), you
      update ONE row in Dim_Symbol instead of millions in Fact_OHLCV.

   Q: Why is Volume nullable?
   A: Spot FOREX has no centralized exchange, so there's no "official"
      volume number. TradingView provides estimated tick volume, but
      some instruments return nothing. NULL means "no data available."

   Q: Why does Dim_Date start from 2008?
   A: Weekly candles align to Monday boundaries. The earliest data
      might be from 2010, but a Monday-anchored week could technically
      start a few days earlier (in December 2009). Starting from 2008
      gives plenty of buffer for any edge case.

   Q: Why INTEGER keys (DateKey=20240315) instead of DATE?
   A: Integers are faster to compare, sort, and join than DATE types.
      They're also human-readable (20240315 = March 15, 2024) without
      any formatting. This is a standard data warehouse technique.


   ============================================================
   EXECUTION ORDER (dependency chain)
   ============================================================

     01_setup_database.sql  — CREATE DATABASE + CREATE SCHEMAs
     02_core_tables.sql     — Dimension tables, Fact table, Symbol master
     03_staging_tables.sql  — All 15 SEN.TF_* staging tables
     04_business_objects.sql — Views, MART procs, ETL procs
     06_active_task.sql     — Runtime lock / token relay table used by Python scripts
     05_verify.sql          — Verification queries; prints SETUP COMPLETE

   The :r paths below include each file in sequence.
   ============================================================ */

:r "01_setup_database.sql"
:r "02_core_tables.sql"
:r "03_staging_tables.sql"
:r "04_business_objects.sql"
:r "06_active_task.sql"
:r "05_verify.sql"
