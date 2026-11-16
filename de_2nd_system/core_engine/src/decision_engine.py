"""Phía QUYẾT ĐỊNH của DE: nơi ĐĂNG KÝ ô quyết định (chiến lược nào, symbol nào, khung trigger nào, tham chiếu những yếu tố trend nào ở khung nào), nơi chứa toàn bộ luật "reality epoch" và nơi đưa ra tín hiệu cuối.
Đọc kết quả của chiến lược (khung trigger), kết quả của từng yếu tố trend (mỗi yếu tố một khung) và nến khung trigger, rồi trả các tín hiệu cuối; việc ghi ra Redis là của util.py, việc đọc Redis là của data_engine.py,
file này thuần pandas.

  1. SỔ ĐĂNG KÝ:   ``Registration`` là một ô quyết định (chiến lược, symbol, khung trigger, các yếu tố trend tham chiếu ``TrendRef``); ``register`` dựng sổ cho một chiến lược: mỗi yếu tố tham chiếu phải có khung KHÔNG nhỏ
                    hơn khung trigger (khung nhỏ hơn thì nến trend nằm trọn trong nến trigger, bộ lọc vô hiệu) và một chiến lược phải tham chiếu ít nhất một yếu tố;
  2. REALITY EPOCH: nến trigger đóng lúc Ce chỉ được nhìn nến của một yếu tố trend ĐÃ ĐÓNG có giờ đóng không muộn hơn Ce (bằng nhau vẫn tính, vì lúc nến trigger đóng thì nến trend đóng cùng lúc đã biết kết quả).
                    ``merge_closed_reference`` CHỌN và GHÉP nến theo luật đó cho TỪNG yếu tố; ``reference_ready`` trả lời "đủ chưa": có một nến của yếu tố phải đóng ĐÚNG lúc Ce mà chưa về thì PHẢI CHỜ,
                    không dùng nến cũ thay. Nến trend có thể về trước, cùng lúc hay sau nến trigger; luật không phụ thuộc thứ tự về. Việc chờ không có hàng chờ hay hạn riêng: người gọi đánh giá lại khi một
                    trong các nến cần thiết về, và tín hiệu quá hạn hiệu lực thì tự bị bỏ ở util.SignalWriter.publish;
  3. QUYẾT ĐỊNH:   ``decide``: tín hiệu của chiến lược chỉ phát ở nến mà MỌI yếu tố trend tham chiếu (theo epoch) cùng chiều. Có yếu tố ngược chiều thì tín hiệu được DỜI sang nến kế tiếp, tối đa ``max_shift_bars`` nến
                    kể từ nến có tín hiệu; nến đầu tiên mọi yếu tố cùng chiều thì phát, hết số nến dời mà vẫn lệch thì bỏ. Có tín hiệu NGƯỢC chiều xuất hiện giữa chừng thì tín hiệu đang chờ bị hủy (tín hiệu ngược
                    là tín hiệu mới, tự chạy theo cùng quy tắc). Giá vào lệnh là close của nến phát (lệnh MARKET), hoặc giá chờ ở đỉnh (BUY) / đáy (SELL) của nến phát (lệnh STOP); atr tính từ chính nến phát.

Giới hạn đã biết (chưa sửa): ``reference_ready`` chỉ chặn khi Ce rơi đúng lưới đóng của nến trend suy ra từ chính các nến trend đang có; sau nghỉ phiên lưới suy ra từ nến cuối có thể lệch lưới thật nên chờ nhầm.
File này không giữ tên chiến lược hay yếu tố nào và không gọi Redis; tham số (số nến dời, chu kỳ atr, loại giá vào lệnh, độ dài khung) do người gọi truyền vào.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import NamedTuple

import numpy as np
import pandas as pd

from core_engine.src.indicator import atr

TREND_SUFFIX = "; aligned with trend reference"
# Bảng trend rỗng (chưa có nến trend nào về): ``reference_ready`` trả False nên mọi tín hiệu đều phải chờ.
_EMPTY_TREND = pd.DataFrame(
    {"bartime": pd.Series(dtype="datetime64[us]"), "close_time": pd.Series(dtype="datetime64[us]"), "trend_bias": pd.Series(dtype="float64")}
)


# -- sổ đăng ký ----------------------------------------------------------------------------------------------------------------------------------------


class TrendRef(NamedTuple):
    """Một yếu tố trend mà chiến lược tham chiếu: tên đăng ký (khoá ở ``trends:`` của config, cũng là tên kết quả của nó), khung của nó và độ dài khung (configuration.timeframe_duration)."""

    name: str
    timeframe: str
    length: pd.Timedelta


class Registration(NamedTuple):
    """Một ô quyết định: tín hiệu của chiến lược ``strategy`` ở ``symbol``, khung ``trigger_tf``, được xét với MỌI yếu tố trong ``refs``. Độ dài khung do configuration.timeframe_duration đổi từ mã khung."""

    strategy: str
    symbol: str
    trigger_tf: str
    trigger_length: pd.Timedelta
    refs: tuple[TrendRef, ...]


def register(strategy: str, symbols: Sequence[str], trigger_tf: str, trigger_length: pd.Timedelta, refs: Sequence[TrendRef]) -> tuple[Registration, ...]:
    """Sổ đăng ký của một chiến lược cho một nhóm symbol (mỗi symbol một ô). Không có yếu tố tham chiếu, hoặc có yếu tố khung nhỏ hơn khung trigger thì ValueError nêu đúng chiến lược và yếu tố."""
    if not refs:
        raise ValueError(f"strategy {strategy} references no trend: every strategy needs at least one trend reference")
    for ref in refs:
        if ref.length < trigger_length:
            raise ValueError(f"strategy {strategy}: trend {ref.name} on {ref.timeframe} is smaller than trigger timeframe {trigger_tf}; a trend timeframe must be >= the trigger timeframe")
    return tuple(Registration(strategy, symbol, trigger_tf, trigger_length, tuple(refs)) for symbol in symbols)


# -- reality epoch -------------------------------------------------------------------------------------------------------------------------------------


def merge_closed_reference(
    entry_df: pd.DataFrame, ref_df: pd.DataFrame, columns: Sequence[str], *, ref_close_name: str
) -> pd.DataFrame:
    """
    Gắn vào từng nến trigger các cột ``columns`` của nến THAM CHIẾU (nến trend) ĐÃ ĐÓNG gần nhất, không nhìn trước.

    Luật "reality epoch": nến tham chiếu được dùng khi giờ đóng của nó KHÔNG MUỘN HƠN giờ đóng của nến trigger (``close_time`` của hai bảng); bằng nhau vẫn tính, vì lúc nến
    trigger đóng thì nến tham chiếu đóng cùng lúc đã biết kết quả. Điều kiện merge thực tế: ``ref.close_time <= entry.close_time``. Nến trigger chưa có nến tham chiếu nào
    thì các cột mang sang là NaN. Cột giờ đóng của nến tham chiếu được mang sang với tên ``ref_close_name`` (để kiểm và chẩn đoán).

    ``entry_df`` và ``ref_df`` đều phải có ``close_time`` (cùng kiểu datetime) và ``entry_df`` phải có ``bartime``; kết quả sắp theo ``bartime`` tăng dần.
    """
    if entry_df.empty:
        return entry_df.copy()
    entry = entry_df.sort_values("close_time")
    if ref_df.empty:
        merged = entry.copy()
        merged[ref_close_name] = pd.NaT
        for column in columns:
            merged[column] = np.nan
    else:
        ref = ref_df[["close_time", *columns]].sort_values("close_time").rename(columns={"close_time": ref_close_name})
        merged = pd.merge_asof(entry, ref, left_on="close_time", right_on=ref_close_name, direction="backward")
    return merged.sort_values("bartime").reset_index(drop=True)


def reference_ready(reference: pd.DataFrame, entry_close: pd.Timestamp, reference_length: pd.Timedelta) -> bool:
    """Nến tham chiếu (nến trend) có đủ để quyết định cho nến trigger đóng lúc ``entry_close`` chưa.

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


# -- quyết định ----------------------------------------------------------------------------------------------------------------------------------------


def decide(
    signals: pd.DataFrame | None,
    candles: pd.DataFrame,
    trends: Mapping[str, pd.DataFrame | None],
    registration: Registration,
    *,
    max_shift_bars: int,
    atr_period: int,
    stop_entry: bool,
) -> list[pd.Series]:
    """Các tín hiệu cuối đang đến lúc phát của một ô.

    ``signals``: kết quả của chiến lược (bartime, close_time, signal, reason), ``candles``: nến khung trigger tăng dần (OHLCV), ``trends``: kết quả của từng yếu tố trend theo tên (bartime, close_time, trend_bias);
    thiếu tên, None hoặc rỗng là chưa có. Với mỗi tín hiệu còn trong cửa sổ dời (nến tín hiệu cách nến mới nhất không quá ``max_shift_bars`` nến), xét lần lượt nến tín hiệu rồi tối đa ``max_shift_bars`` nến kế tiếp,
    dừng khi:
      * nến chưa về, hoặc nến đó đã là nến của một tín hiệu NGƯỢC chiều (tín hiệu đang xét bị hủy): chưa/không phát;
      * nến của MỘT yếu tố phải chờ chưa về (``reference_ready`` sai): chưa phát, người gọi đánh giá lại khi nó về;
      * MỌI yếu tố (theo epoch) cùng chiều tín hiệu: PHÁT ở nến này; nến không có atr thì không phát.
    Giá vào lệnh (``entry_price``) là close của nến phát; ``stop_entry`` đúng thì là giá chờ: High của nến phát với BUY, Low của nến phát với SELL. atr tính từ chính nến phát.
    Hàm thuần và xác định: gọi lại với cùng dữ liệu cho cùng kết quả, nên người gọi gọi lại mỗi khi có nến mới về; chống ghi trùng là việc của util.SignalWriter. Trả về các dòng (bartime, signal, entry_price, atr,
    signal_reason) theo hợp đồng util.build_signal; ``signal_reason`` là lý do của chiến lược cộng ``TREND_SUFFIX``.
    """
    if signals is None or signals.empty or candles.empty:
        return []
    bars = candles.reset_index(drop=True)
    last = len(bars) - 1
    position = {stamp: index for index, stamp in enumerate(bars["bartime"])}
    references = {ref.name: _EMPTY_TREND if trends.get(ref.name) is None else trends[ref.name] for ref in registration.refs}
    table = bars.assign(close_time=bars["bartime"] + registration.trigger_length)
    for ref in registration.refs:
        column = f"bias_{ref.name}"
        table = merge_closed_reference(table, references[ref.name].rename(columns={"trend_bias": column}), [column], ref_close_name=f"close_time_{ref.name}")
    atr_values = atr(bars, atr_period)
    ordered = signals.sort_values("bartime").reset_index(drop=True)

    fires: list[pd.Series] = []
    for number, signal in ordered.iterrows():
        start = position.get(signal["bartime"])
        if start is None or start < last - max_shift_bars:
            continue  # nến tín hiệu không còn trong bảng nến, hoặc đã quá cửa sổ dời: không còn gì để quyết định
        direction = int(signal["signal"])
        stop = last + 1
        for later in ordered.iloc[number + 1 :].itertuples():
            if int(later.signal) != direction:
                stop = position.get(later.bartime, last + 1)  # từ nến của tín hiệu ngược trở đi, tín hiệu này đã bị hủy
                break
        for index in range(start, min(start + max_shift_bars, last, stop - 1) + 1):
            close_time = table.at[index, "close_time"]
            if not all(reference_ready(references[ref.name], close_time, ref.length) for ref in registration.refs):
                break
            if all(table.at[index, f"bias_{ref.name}"] == direction for ref in registration.refs):
                value = atr_values.iloc[index]
                if pd.notna(value):
                    reason = signal.get("reason")
                    entry = (bars.at[index, "high"] if direction > 0 else bars.at[index, "low"]) if stop_entry else bars.at[index, "close"]
                    fires.append(
                        pd.Series(
                            {
                                "bartime": bars.at[index, "bartime"],
                                "signal": direction,
                                "entry_price": entry,
                                "atr": value,
                                "signal_reason": ("" if pd.isna(reason) else str(reason)) + TREND_SUFFIX,
                            }
                        )
                    )
                break
    return fires
