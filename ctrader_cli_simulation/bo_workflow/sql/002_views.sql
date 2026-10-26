-- 002_views.sql — các view phân tích trên kho SQL master (không lưu số suy ra, tính lúc truy vấn).
-- Thiết kế: bo_workflow/research_notes/sql-master-database-plan-2026-10-14.md. CREATE OR ALTER nên chạy lại được.
-- Quy ước: kho chỉ chứa lượt HOÀN TẤT nên không có điều kiện trạng thái; thời gian UTC.

-- Một dòng mỗi lượt chạy kèm phiên bản bot/tín hiệu đã dùng (đọc nhanh, không phải JOIN tay).
CREATE OR ALTER VIEW dbo.v_run_overview AS
SELECT r.backtest_run_id, r.batch_name, r.window_name, r.zone, r.strategy, r.symbol, r.timeframe,
       r.start_date, r.end_date_requested, r.last_data_time,
       r.ksl_atr_multiple, r.ktp_atr_multiple, r.risk_percent, r.leg_count, r.margin_rejections,
       r.net_profit, r.profit_factor, r.total_trades, r.win_rate, r.max_equity_drawdown_percent,
       s.channel AS signal_channel, s.last_bar_time AS signal_last_bar_time,
       b.build_time_utc AS bot_build_time_utc, LEFT(b.algo_sha256, 12) AS bot_algo_sha_short,
       CASE WHEN b.source_sha256 IS NULL THEN 0 ELSE 1 END AS bot_source_captured,
       r.cli_version, LEFT(r.symbol_spec_sha256, 12) AS symbol_spec_sha_short, r.engine_profile, r.started_utc
FROM dbo.backtest_run r
JOIN dbo.bot_build b ON b.bot_build_id = r.bot_build_id
JOIN dbo.signal_snapshot s ON s.signal_snapshot_id = r.signal_snapshot_id;
GO

-- Lãi ròng và số lệnh theo nửa năm (theo NGÀY ĐÓNG LỆNH, UTC) của từng lượt chạy.
CREATE OR ALTER VIEW dbo.v_cell_half_year_profit AS
SELECT r.backtest_run_id, r.batch_name, r.strategy, r.symbol, r.timeframe,
       YEAR(t.close_time_utc) AS close_year,
       CASE WHEN MONTH(t.close_time_utc) <= 6 THEN 1 ELSE 2 END AS half,
       COUNT(*) AS trade_count,
       SUM(t.net_profit) AS net_profit
FROM dbo.trade t
JOIN dbo.backtest_run r ON r.backtest_run_id = t.backtest_run_id
WHERE t.close_time_utc IS NOT NULL
GROUP BY r.backtest_run_id, r.batch_name, r.strategy, r.symbol, r.timeframe,
         YEAR(t.close_time_utc), CASE WHEN MONTH(t.close_time_utc) <= 6 THEN 1 ELSE 2 END;
GO

-- Ghép cặp lượt chạy CÙNG Ô (cùng chiến lược, symbol, khung, kỳ, tham số, vốn) và chỉ ra phiên bản nào khác nhau.
-- Lọc `versions_differing = 1` để thấy các cặp khác ĐÚNG MỘT thứ → chênh lệch kết quả quy được về một nguyên nhân.
CREATE OR ALTER VIEW dbo.v_compare_same_cell AS
SELECT a.backtest_run_id AS run_a, b.backtest_run_id AS run_b,
       a.strategy, a.symbol, a.timeframe, a.start_date, a.end_date_requested,
       a.ksl_atr_multiple, a.ktp_atr_multiple, a.risk_percent, a.leg_count,
       CASE WHEN a.bot_build_id <> b.bot_build_id THEN 1 ELSE 0 END AS bot_differs,
       CASE WHEN a.signal_window_sha256 <> b.signal_window_sha256 THEN 1 ELSE 0 END AS signal_differs,
       CASE WHEN sa.channel <> sb.channel THEN 1 ELSE 0 END AS signal_channel_differs,
       CASE WHEN ISNULL(a.symbol_spec_sha256, '') <> ISNULL(b.symbol_spec_sha256, '') THEN 1 ELSE 0 END AS symbol_spec_differs,
       CASE WHEN ISNULL(a.cli_version, '') <> ISNULL(b.cli_version, '') THEN 1 ELSE 0 END AS cli_differs,
       (CASE WHEN a.bot_build_id <> b.bot_build_id THEN 1 ELSE 0 END
        + CASE WHEN a.signal_window_sha256 <> b.signal_window_sha256 THEN 1 ELSE 0 END
        + CASE WHEN sa.channel <> sb.channel THEN 1 ELSE 0 END
        + CASE WHEN ISNULL(a.symbol_spec_sha256, '') <> ISNULL(b.symbol_spec_sha256, '') THEN 1 ELSE 0 END
        + CASE WHEN ISNULL(a.cli_version, '') <> ISNULL(b.cli_version, '') THEN 1 ELSE 0 END) AS versions_differing,
       a.net_profit AS net_profit_a, b.net_profit AS net_profit_b, b.net_profit - a.net_profit AS net_profit_change,
       a.total_trades AS trades_a, b.total_trades AS trades_b,
       a.profit_factor AS profit_factor_a, b.profit_factor AS profit_factor_b,
       a.started_utc AS started_utc_a, b.started_utc AS started_utc_b
FROM dbo.backtest_run a
JOIN dbo.backtest_run b
  ON a.backtest_run_id < b.backtest_run_id
 AND a.strategy = b.strategy AND a.symbol = b.symbol AND a.timeframe = b.timeframe
 AND a.start_date = b.start_date AND a.end_date_requested = b.end_date_requested
 AND a.initial_balance = b.initial_balance AND a.engine_profile = b.engine_profile
 AND a.parameters_json = b.parameters_json
JOIN dbo.signal_snapshot sa ON sa.signal_snapshot_id = a.signal_snapshot_id
JOIN dbo.signal_snapshot sb ON sb.signal_snapshot_id = b.signal_snapshot_id;
GO

-- "Bền": lãi trong MỌI năm dương lịch có lệnh, lãi ≥ (số nửa năm − 1) nửa năm, không bị margin từ chối,
-- PF ≥ 1,10, DD equity ≤ 20% (ngưỡng ở CASE bên dưới; sửa ở đây nếu muốn đổi). Dùng cho lượt chạy LIỀN MẠCH nhiều năm:
-- chỉ lấy zone = 'single'; các cửa sổ ngắn của walk-forward (zone train / oos) không vào (đo 2026-10-14: 65 lượt walk-forward
-- 1–3 tháng bị gắn "bền" vô nghĩa).
CREATE OR ALTER VIEW dbo.v_durable_cells AS
WITH half AS (
    SELECT backtest_run_id, close_year, half, net_profit FROM dbo.v_cell_half_year_profit
), half_total AS (
    SELECT backtest_run_id, COUNT(*) AS half_years_total,
           SUM(CASE WHEN net_profit > 0 THEN 1 ELSE 0 END) AS half_years_profitable
    FROM half GROUP BY backtest_run_id
), year_profit AS (
    SELECT backtest_run_id, close_year, SUM(net_profit) AS year_profit FROM half GROUP BY backtest_run_id, close_year
), year_total AS (
    SELECT backtest_run_id, COUNT(*) AS years_total,
           SUM(CASE WHEN year_profit > 0 THEN 1 ELSE 0 END) AS years_profitable
    FROM year_profit GROUP BY backtest_run_id
)
SELECT r.backtest_run_id, r.batch_name, r.strategy, r.symbol, r.timeframe, r.start_date, r.end_date_requested,
       r.net_profit, r.profit_factor, r.max_equity_drawdown_percent, r.margin_rejections,
       h.half_years_total, h.half_years_profitable, y.years_total, y.years_profitable,
       CASE WHEN y.years_profitable = y.years_total
             AND h.half_years_profitable >= h.half_years_total - 1
             AND r.margin_rejections = 0
             AND r.profit_factor >= 1.10
             AND r.max_equity_drawdown_percent <= 20
            THEN 1 ELSE 0 END AS is_durable
FROM dbo.backtest_run r
JOIN half_total h ON h.backtest_run_id = r.backtest_run_id
JOIN year_total y ON y.backtest_run_id = r.backtest_run_id
WHERE r.zone = 'single';
GO

-- Luật lỗ FTMO theo từng ngày của từng lượt (I = vốn ban đầu; ngày theo giờ Prague; so với EQUITY thấp nhất trong ngày):
--   2-Step: sàn ngày = balance 00:00 − 5%·I; sàn tổng TĨNH = 90%·I.
--   1-Step: sàn ngày = balance 00:00 − 3%·I; sàn tổng TRAILING cuối ngày = max(I, balance 00:00 cao nhất tới ngày này) − 10%·I.
-- (Tài khoản <FTMO-ACCOUNT-ID> chưa rõ 1-Step hay 2-Step nên cả hai cùng được tính.)
-- balance 00:00 của một ngày = balance chốt của ngày giao dịch trước (ngày đầu = vốn ban đầu), tính bằng LAG.
CREATE OR ALTER VIEW dbo.v_ftmo_daily_loss AS
WITH day_open AS (
    SELECT d.backtest_run_id, r.batch_name, r.strategy, r.symbol, r.timeframe, d.trading_day, r.initial_balance,
           ISNULL(LAG(d.balance_at_close) OVER (PARTITION BY d.backtest_run_id ORDER BY d.trading_day), r.initial_balance) AS balance_at_midnight,
           d.lowest_equity
    FROM dbo.daily_equity d
    JOIN dbo.backtest_run r ON r.backtest_run_id = d.backtest_run_id
), tracked AS (
    SELECT *, MAX(balance_at_midnight) OVER (PARTITION BY backtest_run_id ORDER BY trading_day
                                             ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS highest_midnight_so_far
    FROM day_open
)
SELECT x.backtest_run_id, x.batch_name, x.strategy, x.symbol, x.timeframe, x.trading_day,
       x.initial_balance, x.balance_at_midnight, x.lowest_equity,
       x.balance_at_midnight - x.lowest_equity AS day_drop_amount,
       100.0 * (x.balance_at_midnight - x.lowest_equity) / x.initial_balance AS day_drop_percent_of_initial,
       CASE WHEN x.lowest_equity < x.balance_at_midnight - 0.05 * x.initial_balance THEN 1 ELSE 0 END AS breaches_2step_daily_limit,
       CASE WHEN x.lowest_equity < 0.90 * x.initial_balance THEN 1 ELSE 0 END AS breaches_2step_total_limit,
       CASE WHEN x.lowest_equity < x.balance_at_midnight - 0.03 * x.initial_balance THEN 1 ELSE 0 END AS breaches_1step_daily_limit,
       CASE WHEN x.lowest_equity < (CASE WHEN x.highest_midnight_so_far > x.initial_balance THEN x.highest_midnight_so_far ELSE x.initial_balance END)
                                   - 0.10 * x.initial_balance THEN 1 ELSE 0 END AS breaches_1step_trailing_limit
FROM tracked x;
GO

-- Walk-forward KHÔNG có bảng riêng: một lượt KIỂM ngoài mẫu là một dòng backtest_run bình thường mang thêm nhãn
-- (zone = 'oos', selection_rule, selection_objective, train_run_id trỏ về lượt HỌC đã được chọn). Hai view dưới đây tính
-- mọi thứ từ các nhãn đó. Số cửa sổ lấy từ tên cửa sổ (wf01_oos → 1).

-- Mỗi (cửa sổ, luật) một dòng: tham số được chọn, kết quả lúc HỌC cạnh kết quả lúc KIỂM, số ứng viên đã quét ở cửa sổ học.
CREATE OR ALTER VIEW dbo.v_walkforward_selected AS
SELECT te.batch_name AS walkforward_name, te.symbol, te.timeframe,
       CAST(SUBSTRING(te.window_name, 3, 2) AS INT) AS window_number,
       te.selection_rule, te.selection_objective,
       tr.start_date AS train_start_date, tr.end_date_requested AS train_end_date,
       te.start_date AS test_start_date, te.end_date_requested AS test_end_date,
       tr.ksl_atr_multiple, tr.ktp_atr_multiple,
       (SELECT COUNT(*) FROM dbo.backtest_run c
         WHERE c.batch_name = tr.batch_name AND c.symbol = tr.symbol AND c.timeframe = tr.timeframe
           AND c.window_name = tr.window_name AND c.zone = 'train') AS candidate_count,
       tr.backtest_run_id AS train_run_id, tr.net_profit AS train_net_profit, tr.profit_factor AS train_profit_factor, tr.total_trades AS train_trades,
       te.backtest_run_id AS test_run_id, te.net_profit AS test_net_profit, te.profit_factor AS test_profit_factor, te.total_trades AS test_trades
FROM dbo.backtest_run te
JOIN dbo.backtest_run tr ON tr.backtest_run_id = te.train_run_id
WHERE te.zone = 'oos' AND te.selection_rule IS NOT NULL;
GO

-- Các lệnh của lượt KIỂM ngoài mẫu walk-forward, nối theo thời gian cho từng luật chọn, kèm lãi lũy kế.
-- Lãi lũy kế cộng thẳng tiền (mỗi cửa sổ kiểm bắt đầu từ cùng vốn); chuỗi lãi theo % ngày dùng daily_equity.
CREATE OR ALTER VIEW dbo.v_walkforward_oos_chain AS
SELECT r.batch_name AS walkforward_name, r.symbol, r.timeframe, r.selection_rule,
       CAST(SUBSTRING(r.window_name, 3, 2) AS INT) AS window_number,
       t.backtest_run_id, t.trade_number, t.close_time_utc, t.net_profit,
       SUM(t.net_profit) OVER (PARTITION BY r.batch_name, r.symbol, r.timeframe, r.selection_rule
                               ORDER BY t.close_time_utc, t.backtest_run_id, t.trade_number
                               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cumulative_net_profit
FROM dbo.backtest_run r
JOIN dbo.trade t ON t.backtest_run_id = r.backtest_run_id
WHERE r.zone = 'oos' AND r.selection_rule IS NOT NULL;
GO
