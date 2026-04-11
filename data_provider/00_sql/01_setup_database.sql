/* ============================================================
   01_setup_database.sql
   Project   : Auto Trading Data Warehouse
   Database  : SEN05_AutoTrading  (SQL Server 2022)

   PURPOSE:
     Step 1 of 5 — Create the database and all schemas.
     Run this file FIRST before any other setup file.

     WHAT THIS FILE DOES IN PLAIN ENGLISH:
       1. Creates the database (the container that holds all tables).
          Think of it as creating an empty filing cabinet.
       2. Creates 3 "schemas" inside the database — these are like
          labeled drawers in the filing cabinet:
            SEN  = "Inbox" drawer   (raw data arrives here)
            DWH  = "Archive" drawer (clean, permanent storage)
            MART = "Display" drawer (ready-to-use data for consumers)

     WHY THIS FILE MUST RUN FIRST:
       All other files create tables and procedures INSIDE this database.
       Without the database and schemas, those files have nowhere to put things.

   SAFE TO RE-RUN:
     All statements check "does this already exist?" before creating.
     Running this file twice does nothing harmful — it just says "already exists."

   RUN ORDER:
     01_setup_database.sql  ← YOU ARE HERE
     02_core_tables.sql
     03_staging_tables.sql
     04_business_objects.sql
     05_verify.sql
   ============================================================ */


/* ============================================================
   BLOCK 1: CREATE DATABASE

   Creates the main database named "SEN05_AutoTrading".

   WHAT IS A DATABASE?
     A database is a container on the SQL Server that holds all your
     tables, views, procedures, and data. Each project typically has
     its own database to keep things isolated.

   WHAT IS "COLLATE Latin1_General_CI_AS"?
     This controls how the database compares text (letters and strings):
       CI = Case Insensitive → "EURUSD" and "eurusd" are treated as the same
       AS = Accent Sensitive → "café" and "cafe" are treated as different
     This is the standard setting for financial data.

   WHY THE "IF NOT EXISTS" CHECK?
     Without it, running this script a second time would crash with an error
     saying "database already exists." The check makes the script safe to
     re-run anytime.
   ============================================================ */

-- Look up the server's list of databases. If ours isn't there, create it.
IF NOT EXISTS (SELECT name FROM sys.databases WHERE name = 'SEN05_AutoTrading')
BEGIN
    CREATE DATABASE SEN05_AutoTrading COLLATE Latin1_General_CI_AS;
    PRINT 'Database SEN05_AutoTrading created.';
END
ELSE
    PRINT 'Database SEN05_AutoTrading already exists — skipping.';
GO

-- From this point on, all commands run INSIDE the SEN05_AutoTrading database.
-- Without this line, SQL Server would try to create tables in the wrong database.
USE SEN05_AutoTrading;
GO


/* ============================================================
   BLOCK 2: CREATE SCHEMAS

   WHAT IS A SCHEMA?
     A schema is a named grouping inside a database — like a folder
     on your computer. Tables in different schemas can have the same
     name without conflicting, and you can control who has access
     to each schema independently.

   THE THREE SCHEMAS AND THEIR ROLES:

     SEN (Staging ENtry)
       The "inbox." Raw price data from TradingView lands here first.
       Data may be incomplete, duplicated, or not yet validated.
       The Python pipeline writes here; nothing else should.

     DWH (Data WareHouse)
       The "archive." Clean, validated, deduplicated candle data.
       The central Fact_OHLCV table and all dimension (lookup) tables
       live here. ETL procedures move data from SEN → DWH after validation.

     MART (Data MARt)
       The "display case." Pre-built views and stored procedures that
       make the data easy to consume. The trading engine and strategy
       code read ONLY from MART — they never touch SEN or DWH directly.
       This means we can reorganize the warehouse without breaking anything.

   WHY IS CREATE SCHEMA WRAPPED IN EXEC()?
     SQL Server has a quirk: CREATE SCHEMA must be the ONLY statement
     in its batch. But we want to check IF NOT EXISTS first.
     Wrapping it in EXEC('...') runs it as a separate mini-batch,
     which satisfies SQL Server's requirement while letting us use
     the IF check.
   ============================================================ */

-- Create SEN schema (staging inbox) if it doesn't already exist.
IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'SEN')
    EXEC('CREATE SCHEMA SEN');
GO

-- Create DWH schema (warehouse archive) if it doesn't already exist.
IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'DWH')
    EXEC('CREATE SCHEMA DWH');
GO

-- Create MART schema (consumer display) if it doesn't already exist.
IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'MART')
    EXEC('CREATE SCHEMA MART');
GO
PRINT 'Schemas SEN, DWH, MART ready.';
GO
