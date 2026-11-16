"""Luật CHỜ của epoch: nến tham chiếu (context hoặc trend) đã về đủ để quyết định cho nến vào lệnh chưa.

Nến vào lệnh đóng lúc ``Ce`` chỉ được nhìn nến tham chiếu ĐÃ ĐÓNG có giờ đóng không muộn hơn ``Ce`` (luật "reality epoch"; bằng nhau vẫn tính). Việc CHỌN nến nào và GHÉP nó vào bảng của ô là của
``signal_publisher.merge_closed_reference`` (và ``assemble_cell_table``, ``merge_trend_reference``); file này chỉ trả lời câu hỏi "đủ chưa": có một nến tham chiếu phải đóng ĐÚNG lúc ``Ce`` mà chưa về thì
PHẢI CHỜ, không dùng nến cũ thay, còn không có nến nào đến hạn lúc đó thì dùng nến đã đóng gần nhất. Hai hàm đều thuần (chỉ đọc bảng, không I/O): người gọi (``configuration.process_cell``) chạy lại ô khi
cấu phần về, nên không có hàng chờ hay hạn riêng; tín hiệu quá hạn hiệu lực thì tự bị bỏ ở ``signal_publisher.RawSignalWriter.publish_latest``.

  * ``entry_complete``: khung context bằng khung trigger -- CONTEXT đã có dòng của nến trigger mới nhất chưa.
  * ``reference_ready``: khung tham chiếu lớn hơn (context hoặc trend) -- nến tham chiếu đến hạn lúc ``Ce`` đã về chưa.

Giới hạn đã biết (chưa sửa): ``reference_ready`` chỉ chặn khi ``Ce`` rơi đúng lưới đóng của nến tham chiếu. Nếu làn tham chiếu đứng quá một nến vào lệnh thì nến vào lệnh kế tiếp (không nằm trên lưới)
được quyết định bằng nến tham chiếu cũ; ngoài ra sau nghỉ phiên lưới suy ra từ nến cuối có thể lệch lưới thật nên chờ nhầm (xem trang DE Workflow, mục 4.4).
"""

from __future__ import annotations

import pandas as pd


def entry_complete(setup: pd.DataFrame, context: pd.DataFrame) -> bool:
    """CONTEXT của khung vào lệnh đã có mốc của nến vào lệnh MỚI NHẤT (dòng cuối SETUP) chưa. strategy_runner ghi CONTEXT trước SETUP nên bình thường là có; chưa có thì chờ."""
    if setup.empty or context.empty:
        return False
    return bool((context["bartime"] == setup["bartime"].iloc[-1]).any())


def reference_ready(reference: pd.DataFrame, entry_close: pd.Timestamp, reference_length: pd.Timedelta) -> bool:
    """Nến tham chiếu (khung chỉ báo hoặc khung trend) có đủ để quyết định cho nến vào lệnh đóng lúc ``entry_close`` chưa.

    CHƯA (phải chờ) khi: bảng tham chiếu rỗng; hoặc nến tham chiếu mới nhất đóng TRƯỚC ``entry_close`` và ``entry_close`` rơi đúng lưới đóng của khung tham chiếu (cách nến đó một số nguyên lần
    ``reference_length``) tức là có một nến tham chiếu đóng ĐÚNG lúc này mà chưa về. ĐỦ ở mọi trường hợp khác: đã có nến đóng đúng ``entry_close``; hoặc ``entry_close`` không trùng lưới nên
    nến đã đóng gần nhất (không muộn hơn) chính là nến phải dùng (kể cả khi nó cũ vì nghỉ phiên). Lưới suy ra từ chính các nến tham chiếu đang có nên không giả định giờ neo nào."""
    if reference.empty:
        return False
    closes = reference["close_time"]
    if (closes == entry_close).any():
        return True
    latest = closes.max()
    return not (latest < entry_close and (entry_close - latest) % reference_length == pd.Timedelta(0))
