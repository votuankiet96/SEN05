/* ============================================================
   05_active_task.sql
   Project   : SEN05 Data Provider
   Database  : SEN05_AutoTrading

   PURPOSE:
     Create SEN.ActiveTask, the runtime coordination table.

   USED BY:
     - Live fetching runtime lock and graceful shutdown signal.
     - Historical pulling runtime lock.
     - Short-lived maintenance/write coordination.

   SAFE TO RE-RUN:
     The table is created only if it does not already exist.
   ============================================================ */

USE SEN05_AutoTrading;
GO

IF OBJECT_ID('SEN.ActiveTask', 'U') IS NULL
BEGIN
    CREATE TABLE SEN.ActiveTask (
        -- Natural advisory lock key.
        -- Examples: 'ws_live_runtime', 'tv_historical_job'.
        TaskName  NVARCHAR(50)  NOT NULL,

        -- UTC time when the lock/task row was created.
        StartedAt DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),

        -- UTC expiry. If a process is killed, the lock can expire naturally.
        ExpiresAt DATETIME2     NOT NULL,

        -- Optional runtime metadata or signal payload.
        -- Examples: heartbeat metadata or shutdown_requested=1.
        Payload   NVARCHAR(500) NULL,

        -- Set once per acquire() from the acquiring LockCoordinator's own
        -- generated identifier. renew()/release() require a matching
        -- OwnerId (not just TaskName), so a process that lost and regained
        -- its DB connection after another process legitimately took over
        -- an expired lock cannot renew or delete that other process's
        -- lock - see scripts/sql/09_migration_lock_fencing.sql for the
        -- split-brain scenario this closes.
        OwnerId   UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),

        -- Strictly-increasing generation counter (survives delete +
        -- reacquire for the same TaskName, since IDENTITY does not reset).
        Fence     BIGINT IDENTITY(1,1) NOT NULL,

        CONSTRAINT PK_ActiveTask PRIMARY KEY CLUSTERED (TaskName)
    );

    PRINT 'Table SEN.ActiveTask created successfully.';
END
ELSE
BEGIN
    PRINT 'Table SEN.ActiveTask already exists - skipped.';
END
GO
