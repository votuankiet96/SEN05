"""Typed result contracts shared across symbol/portfolio workflows.

=======================================================================
FILE NÀY LÀM GÌ?
=======================================================================

File này định nghĩa "khuôn chứa kết quả" cho mọi lần chạy backtest.

Hãy tưởng tượng bạn đặt bánh pizza và nhận về một hộp có ngăn cố định:
ngăn pizza, ngăn tương ớt, tờ hóa đơn. Bạn biết chắc mở hộp ra sẽ
thấy gì, không cần đoán. File này làm đúng điều đó — quy định hộp
chứa kết quả backtest trông như thế nào.

=======================================================================
TẠI SAO CẦN FILE NÀY?
=======================================================================

Nếu KHÔNG có file này:
  - Mỗi hàm backtest trả về một dict tự do: {"kq": ..., "trades": ...}
  - Notebook, dashboard, báo cáo phải đoán key nào tồn tại
  - Ai đó đổi tên key "equity" → "eq_curve" → toàn bộ notebook im lặng
    trả về lỗi hoặc kết quả sai mà không báo

Khi CÓ file này:
  - Mọi backtest đều trả về cùng một "khuôn" đã định sẵn
  - IDE tự gợi ý field khi gõ `result.` → ít sai sót hơn
  - Đổi tên field → code báo lỗi ngay lập tức, không sai lặng

=======================================================================
AI TẠO RA OBJECT NÀY, AI DÙNG NÓ?
=======================================================================

  Tạo ra bởi  →  shared.execution + các strategy runner (combo, ai_trend...)
  Dùng bởi    →  notebooks (.ipynb), dashboard (Streamlit), báo cáo,
                 walk-forward optimizer

=======================================================================
⚠️  LƯU Ý QUAN TRỌNG KHI SỬA FILE NÀY
=======================================================================

Đổi tên bất kỳ field nào ở đây (ví dụ: đổi "metrics" → "kpis") sẽ
làm hỏng MỌI notebook và dashboard đang dùng field đó. Phải tìm và
cập nhật toàn bộ consumer trước khi đổi tên.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass(slots=True)
class SymbolBacktestResult:
    """Hộp chứa toàn bộ kết quả sau khi backtest MỘT symbol.

    =======================================================================
    MỤC ĐÍCH
    =======================================================================

    Sau khi chạy backtest cho một symbol (ví dụ: EURUSD), có rất nhiều thứ
    cần lưu lại: danh sách lệnh, đường vốn, chỉ số hiệu suất, dữ liệu gốc...

    Thay vì để mỗi hàm trả về dict khác nhau, object này gom tất cả vào
    một chỗ có tên rõ ràng. Notebook chỉ cần nhận object này là có đủ
    mọi thứ để phân tích và vẽ biểu đồ.

    =======================================================================
    Ý NGHĨA TỪNG FIELD
    =======================================================================

    symbol
        Tên symbol, ví dụ "US30", "EURUSD", "GOLD".
        Dùng để ghi nhãn biểu đồ và tổng hợp portfolio.

    raw_data
        Dữ liệu nến (OHLCV) gốc tải từ database, CHƯA tính indicator.
        Giữ lại để so sánh trước/sau khi thêm indicator, hoặc debug.

    signal_data
        Dữ liệu nến ĐÃ tính thêm indicator (MA, MACD, ATR...) và cột
        signal (1 = mua, -1 = bán, 0 = không có tín hiệu).
        Dùng để vẽ biểu đồ tín hiệu và kiểm tra xem indicator tính đúng chưa.

        Tại sao giữ cả raw_data lẫn signal_data?
        → raw_data không bao giờ bị thay đổi, luôn là dữ liệu gốc sạch.
          signal_data có thể thay đổi khi thử indicator khác nhau.

    trades
        Danh sách tất cả lệnh đã thực hiện trong backtest.
        Mỗi lệnh là một dict gồm: entry_time, exit_time, entry (giá vào),
        exit (giá ra), pnl (lãi/lỗ), exit_reason (SL/TP/REVERSED...).

    equity
        Đường vốn theo thời gian — vốn thay đổi như thế nào qua từng lệnh.
        Dùng để vẽ đồ thị vốn và tính drawdown.

    metrics
        Bộ chỉ số hiệu suất tổng hợp: Sharpe ratio, Sortino ratio,
        max drawdown, win rate (tỷ lệ thắng), profit factor, v.v.
        Đây là "bảng điểm" tóm tắt kết quả backtest.

    symbol_config
        Cấu hình thực tế đã dùng khi chạy backtest cho symbol này.
        Bao gồm: contract_value, spread, swap, ktp, trailing_activation...
        Lưu lại để biết chính xác backtest chạy với thông số nào,
        tránh nhầm lẫn khi so sánh nhiều lần chạy.

    strategy_settings
        Cấu hình quản lý vốn và risk đã dùng: risk_per_trade (rủi ro mỗi
        lệnh), daily_loss_limit (giới hạn lỗ ngày), account_mode (FTMO
        hay standard)...
        Cần lưu lại vì cùng một symbol chạy chế độ FTMO vs standard
        cho kết quả rất khác nhau.

    account_mode
        Nhãn chế độ tài khoản: "standard" hoặc "ftmo".
        Dùng để ghi nhãn báo cáo và lọc kết quả theo chế độ.

    =======================================================================
    GHI CHÚ KỸ THUẬT
    =======================================================================

    slots=True có nghĩa là gì?
        Python cấp phát bộ nhớ cố định cho object, không cho phép thêm
        field mới sau khi tạo. Lợi ích: tiết kiệm ~30% bộ nhớ khi chạy
        portfolio 37 symbol đồng thời.
    """

    symbol: str
    raw_data: pd.DataFrame
    signal_data: pd.DataFrame
    trades: list[dict[str, Any]]
    equity: pd.Series
    metrics: dict[str, Any]
    symbol_config: dict[str, Any]
    strategy_settings: dict[str, Any]
    account_mode: str


@dataclass(slots=True)
class PortfolioBacktestResult:
    """Hộp chứa toàn bộ kết quả sau khi backtest TOÀN BỘ PORTFOLIO (nhiều symbol).

    =======================================================================
    MỤC ĐÍCH
    =======================================================================

    Khi chạy backtest cho 37 symbol cùng lúc, cần một chỗ để gom kết quả
    lại — vừa giữ nguyên kết quả từng symbol (để phân tích chi tiết),
    vừa có kết quả tổng hợp cả portfolio (để đánh giá tổng thể).

    Hãy hình dung như một báo cáo tổng kết tháng của cả công ty:
    - Có bảng tổng (combined_equity, metrics tổng) → xem nhanh hiệu quả chung
    - Có báo cáo chi tiết từng phòng ban (symbol_results) → drill-down khi cần

    =======================================================================
    Ý NGHĨA TỪNG FIELD
    =======================================================================

    symbol_results
        Dict chứa kết quả của từng symbol riêng lẻ.
        Key là tên symbol ("US30", "EURUSD"...), value là SymbolBacktestResult.
        Tại sao giữ lại toàn bộ?
        → Để phân tích sâu sau này (symbol nào kéo lãi, symbol nào gây drawdown)
          mà không cần chạy lại backtest tốn thời gian.

    trades
        Danh sách TẤT CẢ lệnh của cả portfolio (gộp lệnh của mọi symbol).
        Dùng để phân tích phân phối lãi/lỗ toàn danh mục, hoặc chạy
        Monte Carlo trên toàn bộ trade history.

    equity_frame
        Bảng vốn chi tiết: mỗi cột là một symbol, mỗi hàng là một mốc thời gian.
        Các cột được căn chỉnh cùng trục thời gian để có thể so sánh trực tiếp.
        Ví dụ: ngày 2024-01-15, US30 đang lãi 1200$, EURUSD đang lỗ 300$...
        Dùng để vẽ biểu đồ so sánh hiệu suất từng symbol theo thời gian.

    combined_equity
        Đường vốn TỔNG HỢP của cả portfolio (1 đường duy nhất).
        Tính bằng cách cộng equity tất cả symbol lại theo từng mốc thời gian.
        Đây là đường quan trọng nhất để đánh giá portfolio tổng thể.

    metrics
        Bộ chỉ số hiệu suất CỦA CẢ PORTFOLIO: Sharpe tổng, max drawdown
        lớn nhất của portfolio, tổng số lệnh, win rate chung, v.v.
        Khác với metrics trong SymbolBacktestResult — đây là metrics tính
        trên combined_equity, không phải từng symbol riêng lẻ.

    account_mode
        Chế độ tài khoản áp dụng cho toàn portfolio khi chạy backtest.

    symbol_keys
        Danh sách tên các symbol đã tham gia backtest, theo thứ tự.
        Mặc định là list rỗng. Dùng để hiển thị nhãn, lọc, báo cáo.
    """

    symbol_results: dict[str, SymbolBacktestResult]
    trades: list[dict[str, Any]]
    equity_frame: pd.DataFrame
    combined_equity: pd.Series
    metrics: dict[str, Any]
    account_mode: str
    symbol_keys: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PortfolioWindowResult:
    """Kết quả của MỘT CỬA SỔ OOS trong walk-forward test.

    =======================================================================
    BỐI CẢNH — WALK-FORWARD LÀ GÌ?
    =======================================================================

    Walk-forward test là cách kiểm tra xem chiến lược có thực sự hoạt động
    trong tương lai không, hay chỉ "học vẹt" dữ liệu quá khứ.

    Cách hoạt động:
      1. Chia toàn bộ lịch sử thành nhiều "cửa sổ" liên tiếp
      2. Mỗi cửa sổ gồm 2 phần:
           - IS (in-sample)  : dùng để tìm tham số tốt nhất
           - OOS (out-of-sample): chạy với tham số đó trên dữ liệu MỚI
             (dữ liệu này KHÔNG được dùng khi tìm tham số)
      3. Kết quả OOS mới là kết quả đáng tin — vì đây là "tương lai giả lập"

    Ví dụ thực tế với 3 cửa sổ:
      Cửa sổ 1: IS = 2020→2022, OOS = 2022→2023
      Cửa sổ 2: IS = 2021→2023, OOS = 2023→2024
      Cửa sổ 3: IS = 2022→2024, OOS = 2024→2025

    Object này lưu kết quả phần OOS của MỖI cửa sổ.

    =======================================================================
    TẠI SAO CẦN CLASS RIÊNG THAY VÌ DÙNG DICT?
    =======================================================================

    Walk-forward trả về một danh sách nhiều cửa sổ. Nếu dùng dict tự do,
    khi gộp lại để vẽ biểu đồ OOS toàn bộ sẽ không biết key nào chắc chắn
    có. Class này đảm bảo mọi cửa sổ đều có đúng các field cần thiết.

    =======================================================================
    Ý NGHĨA TỪNG FIELD
    =======================================================================

    window
        Số thứ tự của cửa sổ này (cửa sổ 1, cửa sổ 2...).
        Dùng để sắp xếp và ghi nhãn trên biểu đồ.

    start
        Ngày bắt đầu của khoảng OOS — thời điểm "tương lai giả lập" bắt đầu.

    end
        Ngày kết thúc của khoảng OOS.

    metrics
        Bộ chỉ số hiệu suất TRONG KHOẢNG OOS NÀY: Sharpe, drawdown,
        win rate, số lệnh... Dùng để so sánh chiến lược hoạt động tốt
        hay xấu dần theo từng cửa sổ thời gian.

    combined_equity
        Đường vốn portfolio trong khoảng OOS của cửa sổ này.
        Ghép nhiều combined_equity của các cửa sổ lại → thấy được
        toàn bộ hiệu suất OOS liên tục từ đầu đến cuối.
    """

    window: int
    start: pd.Timestamp
    end: pd.Timestamp
    metrics: dict[str, Any]
    combined_equity: pd.Series
