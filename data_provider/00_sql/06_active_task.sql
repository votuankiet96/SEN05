-- =============================================================================
-- RUNTIME NOTE:
--   `SEN.ActiveTask` is the operational coordination table of the runtime.
--   It is separate from the candle warehouse itself and is used by ws_live,
--   checker and pipeline to avoid write conflicts and to relay optional
--   payload signals across processes.
--   Older Telegram command relay used Payload for /confirm_TOKEN and
--   /skip_TOKEN. The current data_provider notification path uses one-way
--   Discord webhooks, so normal checker confirmations do not rely on an
--   inbound bot listener.
-- =============================================================================
-- data_provider/00_sql/06_active_task.sql
-- Tạo bảng SEN.ActiveTask — dùng cho:
--   1. DB-level distributed lock (ngăn Checker và WS ETL chạy đồng thời)
--   2. Cross-process payload relay (legacy token result / runtime signal)
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

        -- Payload tuỳ mục đích — dùng cho tín hiệu vận hành hoặc legacy token relay
        -- Định dạng legacy: 'confirm:TOKEN' hoặc 'skip:TOKEN'
        -- Discord webhook hiện tại là một chiều nên không có inbound bot ghi lệnh
        -- xác nhận trong luồng vận hành mặc định.
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
