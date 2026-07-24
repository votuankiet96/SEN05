/* ============================================================
   09_migration_lock_fencing.sql
   Project   : SEN05 Data Provider
   Database  : SEN05_AutoTrading

   *** DO NOT RUN THIS AUTOMATICALLY. THIS IS A CONTROLLED-DEPLOY SCRIPT. ***

   WHY THIS SCRIPT EXISTS:
     Round-2 audit (Codex) finding: SEN.ActiveTask's renew()/release() only
     ever filtered by TaskName - never by who currently holds the lock.
     Split-brain scenario: process A loses its DB connection for longer
     than the lock TTL (e.g. a long GC pause, a network blip, a VM
     freeze), process B legitimately acquires the now-expired lock and
     starts writing, then A reconnects and calls renew()/release() on the
     SAME TaskName - which matches B's row just as well as A's, since
     nothing on the row identifies an owner at all. A can then either
     silently "renew" a lock it no longer legitimately holds (extending
     B's expiry under A's belief that A owns it) or delete B's active
     lock out from under it (release()), and both processes end up
     writing concurrently against data they each believe only they own.

     This migration adds an OwnerId (set once per acquire, from the
     acquiring process's own generated identifier) and a Fence (a
     strictly-increasing generation counter via IDENTITY, surviving across
     delete+reacquire cycles for the same TaskName) so renew()/release()
     can require "TaskName AND OwnerId" instead of "TaskName" alone. A
     stale owner's renew() now returns 0 rows matched - a real, checkable
     signal - instead of silently succeeding.

   REQUIRED DEPLOY PROCEDURE:
     1. Confirm no write-path process is active (see
        08_migration_usp_loaddirect_v2.sql for the same pre-flight steps -
        conflict-status, then `python -m core_engine stop`).
     2. Run THIS script against the target database.
     3. Deploy the application code for this same change window (the
        OwnerId/Fence-aware locks.py ships with this same branch/PR).
     4. `python -m core_engine doctor` to confirm locks are readable.
     5. Resume 24/7 operation.

   SAFE TO RE-RUN:
     Yes - the column-existence checks make this idempotent. Existing rows
     get OwnerId = NEWID() (a fresh random value - any process currently
     holding a lock across this migration will fail its next renew() and
     have to re-acquire, which is safe: see coordination/locks.py, a
     failed renew makes the live runtime stop writing and let the
     supervisor restart it).
   ============================================================ */

USE SEN05_AutoTrading;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('SEN.ActiveTask') AND name = 'OwnerId'
)
BEGIN
    ALTER TABLE SEN.ActiveTask ADD OwnerId UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_ActiveTask_OwnerId DEFAULT NEWID();
    PRINT 'Added SEN.ActiveTask.OwnerId.';
END
ELSE
    PRINT 'SEN.ActiveTask.OwnerId already exists - skipped.';
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('SEN.ActiveTask') AND name = 'Fence'
)
BEGIN
    -- IDENTITY rather than a manually-maintained counter: SQL Server
    -- guarantees monotonic, gap-tolerant, race-free values across
    -- concurrent inserts without the caller needing a SELECT MAX(...)+1
    -- round trip (which would itself be a race under concurrent acquire).
    ALTER TABLE SEN.ActiveTask ADD Fence BIGINT IDENTITY(1,1) NOT NULL;
    PRINT 'Added SEN.ActiveTask.Fence (IDENTITY).';
END
ELSE
    PRINT 'SEN.ActiveTask.Fence already exists - skipped.';
GO

PRINT '=== lock fencing migration complete. Now run: python -m core_engine doctor ===';
GO
