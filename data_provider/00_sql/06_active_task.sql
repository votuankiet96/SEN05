-- =============================================================================
-- data_provider/00_sql/06_active_task.sql
-- Tạo bảng SEN.ActiveTask — dùng cho:
--   1. DB-level distributed lock (ngăn Checker và WS ETL chạy đồng thời)
--   2. Cross-process token relay (WS bot ghi token result, Checker đọc)
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

        -- Payload tuỳ mục đích — hiện dùng để relay token xác nhận
        -- Định dạng: 'confirm:TOKEN' hoặc 'skip:TOKEN'
        -- WS bot ghi vào đây khi nhận /confirm_TOKEN hoặc /skip_TOKEN
        -- Checker đọc từ đây để biết user đã chọn gì (cross-process)
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
