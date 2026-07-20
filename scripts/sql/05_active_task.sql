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

        CONSTRAINT PK_ActiveTask PRIMARY KEY CLUSTERED (TaskName)
    );

    PRINT 'Table SEN.ActiveTask created successfully.';
END
ELSE
BEGIN
    PRINT 'Table SEN.ActiveTask already exists - skipped.';
END
GO
