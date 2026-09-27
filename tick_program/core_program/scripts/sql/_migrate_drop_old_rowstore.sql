/* One-time migration: drop the old rowstore tick tables + ThinState so
   tickdata_setup.sql can recreate them as Clustered Columnstore. Run once,
   immediately followed by tickdata_setup.sql. Not idempotent by design --
   this is a deliberate one-time reset (2026-09), not part of routine deploy. */
USE SEN05_AutoTrading;
GO
IF OBJECT_ID('tick.FR40','U')   IS NOT NULL DROP TABLE tick.FR40;
IF OBJECT_ID('tick.DE40','U')   IS NOT NULL DROP TABLE tick.DE40;
IF OBJECT_ID('tick.HK50','U')   IS NOT NULL DROP TABLE tick.HK50;
IF OBJECT_ID('tick.J225','U')   IS NOT NULL DROP TABLE tick.J225;
IF OBJECT_ID('tick.SP35','U')   IS NOT NULL DROP TABLE tick.SP35;
IF OBJECT_ID('tick.UK100','U')  IS NOT NULL DROP TABLE tick.UK100;
IF OBJECT_ID('tick.US500','U')  IS NOT NULL DROP TABLE tick.US500;
IF OBJECT_ID('tick.US100','U')  IS NOT NULL DROP TABLE tick.US100;
IF OBJECT_ID('tick.US30','U')   IS NOT NULL DROP TABLE tick.US30;
IF OBJECT_ID('tick.GOLD','U')   IS NOT NULL DROP TABLE tick.GOLD;
IF OBJECT_ID('tick.BTCUSD','U') IS NOT NULL DROP TABLE tick.BTCUSD;
IF OBJECT_ID('tick.ThinState','U') IS NOT NULL DROP TABLE tick.ThinState;
GO
PRINT 'old rowstore tick tables + ThinState dropped.';
GO
