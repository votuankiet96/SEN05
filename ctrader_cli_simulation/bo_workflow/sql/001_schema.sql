-- 001_schema.sql — kho SQL master cho kết quả backtest (database bo_research, SQL Server Express).
-- Thiết kế và lý do: bo_workflow/research_notes/sql-master-database-plan-2026-10-14.md
--
-- Chạy được NHIỀU LẦN (mỗi bảng/chỉ mục chỉ tạo khi chưa có). Các lệnh cách nhau bằng dòng `GO`
-- (master_db.ensure_schema tách theo dòng này). Quy tắc: chỉ thêm, không sửa kết quả đã xong; chỉ lượt HOÀN TẤT
-- mới vào kho. Mọi thời gian UTC, riêng trading_day theo ngày giờ Prague. Cột nào suy ra được từ cột/bảng khác
-- thì KHÔNG lưu (đã dọn 2026-10-14, xem research_notes/sql-master-database-plan-2026-10-14.md).

-- Một bản build bot: file .algo (manifest nằm trong đó) + mã nguồn .cs (.algo KHÔNG chứa mã nguồn nên .cs phải chụp riêng).
IF OBJECT_ID(N'dbo.bot_build', N'U') IS NULL
CREATE TABLE dbo.bot_build (
    bot_build_id           INT IDENTITY(1,1) CONSTRAINT pk_bot_build PRIMARY KEY,
    strategy               NVARCHAR(30)  NOT NULL,                -- combo / macross / macdbreak
    algo_sha256            CHAR(64)      NOT NULL CONSTRAINT uq_bot_build_algo UNIQUE,
    algo_file              VARBINARY(MAX) NOT NULL,               -- chính file .algo, để chạy lại bản cũ
    build_time_utc         DATETIME2(3)  NULL,                    -- Timestamp trong manifest
    source_sha256          CHAR(64)      NULL,                    -- NULL nếu lượt chạy không chụp .cs
    source_code            NVARCHAR(MAX) NULL,
    source_modified_utc    DATETIME2(0)  NULL,                    -- giờ sửa file .cs (mtime), để so với build_time_utc
    first_seen_utc         DATETIME2(0)  NOT NULL CONSTRAINT df_bot_build_seen DEFAULT SYSUTCDATETIME()
);
GO

-- Một bản chụp list tín hiệu Redis (nén). Cùng nội dung thì dùng lại, không thêm.
IF OBJECT_ID(N'dbo.signal_snapshot', N'U') IS NULL
CREATE TABLE dbo.signal_snapshot (
    signal_snapshot_id INT IDENTITY(1,1) CONSTRAINT pk_signal_snapshot PRIMARY KEY,
    channel            NVARCHAR(10)  NOT NULL,                    -- nguồn tín hiệu: original (db0 RAW) / knn (db1) / breakout (db2) / mixed (các kênh của lượt lấy từ nhiều nguồn); nhãn cũ 'trend' đã đổi thành knn/breakout 2026-10-23
    strategy           NVARCHAR(30)  NOT NULL,
    symbol             NVARCHAR(30)  NOT NULL,                    -- tên cTrader, vd US30.cash
    timeframe          NVARCHAR(5)   NOT NULL,
    redis_key          NVARCHAR(100) NULL,                        -- key Redis thật (tên Redis khác tên cTrader: JP225.cash → J225, XAUUSD → GOLD)
    content_sha256     CHAR(64)      NOT NULL,                    -- hash toàn bộ CSV
    last_bar_time      DATETIME2(0)  NULL,
    rows_compressed    VARBINARY(MAX) NOT NULL,                   -- CSV bartime,atr,entry,signal nén gzip (DECOMPRESS() giải nén trong SQL)
    captured_at_utc    DATETIME2(0)  NOT NULL,
    CONSTRAINT uq_signal_snapshot UNIQUE (channel, strategy, symbol, timeframe, content_sha256)
);
GO

-- Một lần backtest.
IF OBJECT_ID(N'dbo.backtest_run', N'U') IS NULL
CREATE TABLE dbo.backtest_run (
    backtest_run_id              BIGINT IDENTITY(1,1) CONSTRAINT pk_backtest_run PRIMARY KEY,
    run_key                      CHAR(64)      NOT NULL CONSTRAINT uq_backtest_run_key UNIQUE,   -- = param_hash của pipeline
    batch_name                   NVARCHAR(100) NULL,                                              -- experiment đã tạo ra lượt này
    method                       NVARCHAR(20)  NULL,                                              -- loại thí nghiệm: grid / walkforward
    window_name                  NVARCHAR(30)  NULL,                                              -- vd wf01_is
    zone                         NVARCHAR(10)  NULL,                                              -- train / oos / single
    selection_rule               NVARCHAR(10)  NULL,                                              -- chỉ lượt KIỂM walk-forward: luật chọn ra tham số này (plateau / best); `rule` là từ khoá SQL Server
    selection_objective          NVARCHAR(20)  NULL,                                              -- chỉ lượt kiểm: tiêu chí chọn (vd net_profit)
    strategy                     NVARCHAR(30)  NOT NULL,
    symbol                       NVARCHAR(30)  NOT NULL,
    timeframe                    NVARCHAR(5)   NOT NULL,
    start_date                   DATE          NOT NULL,
    end_date_requested           DATE          NOT NULL,       -- giá trị đã truyền cho --end (cTrader chạy TRỌN ngày này)
    last_data_time               DATETIME2(3)  NULL,           -- điểm equity cuối cùng thật sự
    initial_balance              DECIMAL(18,2) NOT NULL,
    ksl_atr_multiple             DECIMAL(9,4)  NULL,           -- hệ số thật ×ATR, không phải số thứ tự enum
    ktp_atr_multiple             DECIMAL(9,4)  NULL,
    risk_percent                 DECIMAL(9,4)  NULL,
    leg_count                    TINYINT       NULL,
    parameters_json              NVARCHAR(MAX) NOT NULL CONSTRAINT ck_backtest_run_params CHECK (ISJSON(parameters_json) = 1),
    bot_build_id                 INT           NOT NULL CONSTRAINT fk_backtest_run_bot REFERENCES dbo.bot_build (bot_build_id),
    signal_snapshot_id           INT           NOT NULL CONSTRAINT fk_backtest_run_signal REFERENCES dbo.signal_snapshot (signal_snapshot_id),
    signal_window_sha256         CHAR(64)      NOT NULL,       -- hash tín hiệu tới HẾT ngày end (phần trước-khi-chạy của run_key)
    engine_profile               NVARCHAR(80)  NOT NULL,
    engine_json                  NVARCHAR(MAX) NULL,
    cli_version                  NVARCHAR(30)  NULL,
    symbol_spec_json             NVARCHAR(MAX) NULL,           -- thông số symbol broker lúc chạy (swap đổi theo ngày)
    symbol_spec_sha256           CHAR(64)      NULL,
    margin_rejections            INT           NULL,
    net_profit                   DECIMAL(18,2) NULL,           -- số chính thức của cTrader
    profit_factor                DECIMAL(18,4) NULL,
    total_trades                 INT           NULL,
    win_rate                     DECIMAL(9,4)  NULL,
    max_equity_drawdown_percent  DECIMAL(9,4)  NULL,
    bot_counters_json            NVARCHAR(MAX) NULL,
    started_utc                  DATETIME2(3)  NULL,           -- lượt optimize: giờ của CẢ HỘP (9 pass cùng giá trị)
    ended_utc                    DATETIME2(3)  NULL,
    train_run_id                 BIGINT        NULL CONSTRAINT fk_backtest_run_train REFERENCES dbo.backtest_run (backtest_run_id),   -- chỉ lượt kiểm: lượt HỌC đã được chọn để đem kiểm
    saved_at_utc                 DATETIME2(0)  NOT NULL CONSTRAINT df_backtest_run_saved DEFAULT SYSUTCDATETIME()
);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_backtest_run_cell' AND object_id = OBJECT_ID(N'dbo.backtest_run'))
CREATE INDEX ix_backtest_run_cell ON dbo.backtest_run (strategy, symbol, timeframe, start_date, end_date_requested);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_backtest_run_batch' AND object_id = OBJECT_ID(N'dbo.backtest_run'))
CREATE INDEX ix_backtest_run_batch ON dbo.backtest_run (batch_name, window_name, zone);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_backtest_run_train' AND object_id = OBJECT_ID(N'dbo.backtest_run'))
CREATE INDEX ix_backtest_run_train ON dbo.backtest_run (train_run_id) WHERE train_run_id IS NOT NULL;
GO

-- Một lệnh đã đóng.
IF OBJECT_ID(N'dbo.trade', N'U') IS NULL
CREATE TABLE dbo.trade (
    backtest_run_id BIGINT        NOT NULL CONSTRAINT fk_trade_run REFERENCES dbo.backtest_run (backtest_run_id),
    trade_number    INT           NOT NULL,                       -- id trong report cTrader
    direction       NVARCHAR(4)   NOT NULL,                       -- buy / sell
    leg_label       NVARCHAR(40)  NULL,                           -- vd ComboCsvPending_L1
    entry_time_utc  DATETIME2(3)  NULL,
    close_time_utc  DATETIME2(3)  NULL,
    entry_price     DECIMAL(18,5) NULL,
    close_price     DECIMAL(18,5) NULL,
    volume          DECIMAL(18,4) NULL,
    gross_profit    DECIMAL(18,2) NULL,
    commission      DECIMAL(18,2) NULL,
    swap            DECIMAL(18,2) NULL,
    net_profit      DECIMAL(18,2) NULL,
    close_reason    NVARCHAR(20)  NULL,                           -- stop_loss / take_profit / closed_by_bot (từ events.json)
    CONSTRAINT pk_trade PRIMARY KEY (backtest_run_id, trade_number)
);
GO

-- Một ngày giao dịch của một lần chạy (ngày theo giờ Prague, dùng cho luật lỗ ngày FTMO).
IF OBJECT_ID(N'dbo.daily_equity', N'U') IS NULL
CREATE TABLE dbo.daily_equity (
    backtest_run_id     BIGINT        NOT NULL CONSTRAINT fk_daily_equity_run REFERENCES dbo.backtest_run (backtest_run_id),
    trading_day         DATE          NOT NULL,
    balance_at_close    DECIMAL(18,2) NOT NULL,                   -- balance lúc 00:00 = balance_at_close ngày trước (ngày đầu = vốn ban đầu)
    lowest_equity       DECIMAL(18,2) NOT NULL,
    CONSTRAINT pk_daily_equity PRIMARY KEY (backtest_run_id, trading_day)
);
GO

-- Thành phần của một DANH MỤC: lượt NHIỀU KÊNH chạy chung 1 tài khoản (spec.channels có từ 2 kênh trở lên). Mỗi kênh một dòng, ghi MỘT lần
-- cho mỗi batch (lượt khác của cùng batch không ghi thêm). Rủi ro và nguồn lấy từ chính cấu hình lượt chạy, không suy ra từ tín hiệu.
IF OBJECT_ID(N'dbo.portfolio_channel', N'U') IS NULL
CREATE TABLE dbo.portfolio_channel (
    batch_name    NVARCHAR(100) NOT NULL,                         -- = backtest_run.batch_name
    strategy      NVARCHAR(30)  NOT NULL,                         -- combo / ma_cross / macd_break
    symbol        NVARCHAR(30)  NOT NULL,                         -- tên cTrader, vd HK50.cash
    timeframe     NVARCHAR(5)   NOT NULL,                         -- chữ thường: h3, m30
    risk_percent  FLOAT         NOT NULL,                         -- rủi ro % của RIÊNG kênh này (tổng các kênh = rủi ro cả danh mục)
    source        NVARCHAR(40)  NOT NULL,                         -- nguồn tín hiệu của kênh: original / knn / breakout / zigzag / tstat. Dòng nạp từ trước 2026-10-26 là suy ra từ tín hiệu: 'a|b' = không phân biệt được a hay b
    ksl           FLOAT         NULL,                             -- SL = ksl × ATR (chung cả danh mục)
    tp1           FLOAT         NULL,                             -- TP chân 1 / 2 / 3 (× ATR); NULL nếu danh mục không có chân đó
    tp2           FLOAT         NULL,
    tp3           FLOAT         NULL,
    legs          TINYINT       NULL,                             -- số chân mỗi tín hiệu
    CONSTRAINT pk_portfolio_channel PRIMARY KEY (batch_name, strategy, symbol, timeframe)
);
GO
