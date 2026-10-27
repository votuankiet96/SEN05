-- 003_analysis.sql — dữ liệu DẪN XUẤT, nằm trong lược đồ riêng `analysis`: tính SAU từ dbo (cộng cache tick trên đĩa),
-- KHÔNG do luồng backtest ghi sau mỗi lượt. Dựng/làm mới bằng lệnh riêng của output_util/trade_excursion.py
-- (`run` tính các lượt còn thiếu, `status` cho biết còn bao nhiêu, `check` đối chiếu với kết cục thật).
-- Quy ước kho: dbo = ghi nhận lượt chạy (luồng backtest ghi); analysis = kết quả tính thêm, xoá đi dựng lại được.
-- Chạy được NHIỀU LẦN (mỗi bảng chỉ tạo khi chưa có); các lệnh cách nhau bằng dòng `GO` như 001/002.

IF SCHEMA_ID(N'analysis') IS NULL EXEC(N'CREATE SCHEMA analysis');
GO

-- Một NHÓM lệnh (1 tín hiệu = N chân cùng giá vào) của lượt lẻ bán niên, đo bằng tick thật như thể KHÔNG có TP: từ lúc vào lệnh đi theo
-- giá tới điểm SỚM NHẤT trong 3 (chạm SL, tín hiệu ngược hướng kế tiếp, hết dữ liệu của kỳ). Mọi mức TP suy ra từ đây:
-- xác suất chạm = P(mfe_atr >= TP), kết quả kỳ vọng = chạm: +TP; không chạm: end_exc_atr (xem truy vấn "TP Curve" của Power BI).
IF OBJECT_ID(N'analysis.group_excursion', N'U') IS NULL
CREATE TABLE analysis.group_excursion (
    backtest_run_id  BIGINT       NOT NULL,
    direction        VARCHAR(4)   NOT NULL,                       -- buy / sell
    entry_time_utc   DATETIME2(3) NOT NULL,                       -- giờ vào lệnh của nhóm
    signal_bar_time  DATETIME2(0) NULL,                           -- giờ nến của tín hiệu đã ghép với nhóm
    atr              FLOAT        NOT NULL,                       -- ATR của tín hiệu (đơn vị của mọi cột *_atr)
    ref_price        FLOAT        NOT NULL,                       -- giá tham chiếu: entry của tín hiệu (lệnh STOP) hoặc giá khớp (MARKET)
    fill_price       FLOAT        NOT NULL,
    entry_slip_atr   FLOAT        NOT NULL,                       -- d × (giá khớp - giá tham chiếu) / ATR; d = +1 mua, -1 bán (như mọi cột *_atr dưới đây)
    mfe_atr          FLOAT        NOT NULL,                       -- mức thuận lợi tối đa so với giá tham chiếu, chia ATR (Mua: Bid cao nhất; Bán: Ask thấp nhất)
    end_kind         TINYINT      NOT NULL,                       -- 1 = chạm SL, 2 = tín hiệu ngược hướng, 3 = hết dữ liệu của kỳ
    end_exc_atr      FLOAT        NOT NULL,                       -- vị thế so với giá tham chiếu (chia ATR) tại điểm kết thúc
    end_time_utc     DATETIME2(3) NOT NULL,
    CONSTRAINT pk_group_excursion PRIMARY KEY (backtest_run_id, direction, entry_time_utc)
);
GO

-- Lượt nào đã được đo (để lần sau chỉ tính phần còn thiếu) và độ khớp giữa "chạm TPk tính từ tick" với kết cục THẬT của backtest.
IF OBJECT_ID(N'analysis.excursion_run', N'U') IS NULL
CREATE TABLE analysis.excursion_run (
    backtest_run_id  BIGINT       NOT NULL CONSTRAINT pk_excursion_run PRIMARY KEY,
    groups           INT          NOT NULL,                       -- số nhóm lệnh của lượt
    matched          INT          NOT NULL,                       -- số nhóm ghép được với tín hiệu (có dòng trong group_excursion)
    comparable       INT          NOT NULL,                       -- số nhóm 3 chân có đủ kết cục để đối chiếu
    agree_tp1        INT          NOT NULL,                       -- số nhóm mà cờ "chạm TPk" tính từ tick trùng kết cục thật
    agree_tp2        INT          NOT NULL,
    agree_tp3        INT          NOT NULL,
    computed_utc     DATETIME2(0) NOT NULL
);
GO
