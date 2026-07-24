-- =============================================================================
-- RUNTIME NOTE:
--   `SEN.ActiveTask` is the operational coordination table of the runtime.
--   It is separate from the candle warehouse itself and is used by ws_live,
--   checker and pipeline to avoid write conflicts and to relay optional
--   runtime payload signals across processes.
-- =============================================================================
-- data_provider/sql/06_active_task.sql
-- Tạo bảng SEN.ActiveTask — dùng cho:
--   1. DB-level distributed lock (ngăn Checker và WS ETL chạy đồng thời)
--   2. Cross-process runtime payload signal
--
-- Chạy một lần duy nhất khi cài đặt hệ thống.
-- Idempotent: an toàn khi chạy lại (IF OBJECT_ID check).
-- =============================================================================

USE SEN05_AutoTrading;
GO

IF OBJECT_ID('SEN.ActiveTask', 'U') IS NULL
BEGIN
    CREATE TABLE SEN.ActiveTask (
        -- Tên task — dùng làm khóa lock
        -- Ví dụ: 'checker_repair'
        TaskName  NVARCHAR(50)  NOT NULL,

        -- Thời điểm bắt đầu (UTC, tự động ghi)
        StartedAt DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),

        -- Thời điểm hết hạn lock (UTC)
        -- Dead-man switch: nếu process bị kill, lock tự expire sau ExpiresAt
        -- WS tự resume ETL bình thường sau khi ExpiresAt qua
        ExpiresAt DATETIME2     NOT NULL,

        -- Payload tuỳ mục đích — dùng cho tín hiệu vận hành nhỏ giữa các process,
        -- ví dụ heartbeat metadata hoặc shutdown_requested=1 cho ws_live.
        Payload   NVARCHAR(500) NULL,

        CONSTRAINT PK_ActiveTask PRIMARY KEY CLUSTERED (TaskName)
        -- PRIMARY KEY trên TaskName = natural advisory lock:
        -- INSERT thất bại (PK violation) nếu task đã tồn tại → lock đang bị giữ
    );

    PRINT 'Table SEN.ActiveTask created successfully.';
END
ELSE
BEGIN
    PRINT 'Table SEN.ActiveTask already exists — skipped.';
END
GO
