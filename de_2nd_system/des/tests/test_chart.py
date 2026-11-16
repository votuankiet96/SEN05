"""Test của chart (des/src/chart.py): hàm dựng dữ liệu vẽ, đọc Redis chỉ-đọc, khung nhìn của từng chiến lược và các route Flask.

Hermetic: Redis giả nằm trong file này (chỉ các lệnh đọc mà chart dùng), dữ liệu là nến tổng hợp và kết quả module sinh bằng chính ``util.signal_rows`` / ``util.bias_rows`` của engine
nên đúng hình dạng thật ở db10 / db11. Không test nào chạm Redis hay config thật.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import redis
from engine_support import (
    SERVERS,
    bar_row,
    local_config,
    make_bars,
    plan,
    shared_config,
    stamp_of,
    with_break20,
    with_combo,
)

from des.src import chart, configuration
from des.src.indicator import macd_hist, sma
from des.src.util import bias_rows, signal_rows

CLOCK = {"dfs:clock": "2026-01-10 12:00:00"}
STATE = {"run_id": "run1", "status": "running", "tick_seq": "42", "sim_time": "2026-01-10 12:00:00"}


# -- Redis giả: chỉ lệnh đọc mà chart dùng --------------------------------------------------------------------------------------------------------


class _Pipe:
    def __init__(self, core: FakeRedis) -> None:
        self.core = core
        self.queue: list[tuple[str, tuple]] = []

    def __getattr__(self, name: str):
        def queue(*args: object) -> _Pipe:
            self.queue.append((name, args))
            return self

        return queue

    def execute(self) -> list:
        results = [getattr(self.core, name)(*args) for name, args in self.queue]
        self.queue = []
        return results


class FakeRedis:
    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.strings: dict[str, str] = dict(CLOCK)
        self.hashes["dfs:state"] = dict(STATE)
        self.commands: list[str] = []

    def pipeline(self, **_kwargs: object) -> _Pipe:
        return _Pipe(self)

    def get(self, key: str) -> str | None:
        self.commands.append("get")
        return self.strings.get(key)

    def hgetall(self, key: str) -> dict[str, str]:
        self.commands.append("hgetall")
        return dict(self.hashes.get(key, {}))

    def hget(self, key: str, field: str) -> str | None:
        self.commands.append("hget")
        return self.hashes.get(key, {}).get(field)

    def hmget(self, key: str, *fields: str) -> list[str | None]:
        self.commands.append("hmget")
        row = self.hashes.get(key)
        return [row.get(field) for field in fields] if row else [None] * len(fields)

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        self.commands.append("lrange")
        items = self.lists.get(key, [])
        start = max(len(items) + start, 0) if start < 0 else start
        end = len(items) + end if end < 0 else end
        return list(items[start : end + 1])

    def lindex(self, key: str, index: int) -> str | None:
        self.commands.append("lindex")
        items = self.lists.get(key, [])
        return items[index] if -len(items) <= index < len(items) else None

    def llen(self, key: str) -> int:
        self.commands.append("llen")
        return len(self.lists.get(key, []))

    # -- nạp dữ liệu như DFS (nến) và DES (kết quả module, tín hiệu cuối) -------------------------------------------------------------------------
    def put_candles(self, symbol: str, timeframe: str, bars: pd.DataFrame, prefix: str = "L_CANDLE") -> None:
        key = f"{prefix}_{symbol}_{timeframe}"
        self.lists[key] = [stamp_of(t) for t in reversed(list(bars["bartime"]))]  # nến: mới nhất đứng đầu
        for _, bar in bars.iterrows():
            self.hashes[f"{key}:{stamp_of(bar['bartime'])}"] = bar_row(bar)

    def put_rows(self, key: str, rows: list[tuple[str, dict[str, str]]]) -> None:
        self.lists[key] = [stamp for stamp, _ in rows]  # kết quả module và tín hiệu cuối: mới nhất đứng cuối
        for stamp, fields in rows:
            self.hashes[f"{key}:{stamp}"] = dict(fields)


def _final_fields(stamp: str, side: str = "BUY", strategy: str = "ma_cross") -> dict[str, str]:
    return {
        "symbol": "US30", "timeframe": "M10", "strategy": strategy, "bartime": stamp, "tradeSide": side, "orderType": "MARKET",
        "expirationTimestamp": str(int((pd.Timestamp(stamp) + pd.Timedelta(minutes=20) - pd.Timestamp("1970-01-01")).total_seconds())),
        "clientOrderId": f"{strategy}:M10:US30:{stamp}", "comment": "fast SMA crossed above slow SMA; aligned with trend reference", "atr": "3.5", "valid_from": stamp,
    }


def _world(*, combo: bool = False, with_results: bool = True, break20: bool = False) -> tuple[chart.Context, FakeRedis, configuration.Plan]:
    """Một thế giới thu nhỏ: nến US30 M10 (900), M30 (300), M45 và H1 (khung yếu tố của combo), kết quả ma_cross + hai yếu tố trend, hai tín hiệu cuối. ``break20``: combo tham chiếu yếu tố break20 @M45 thay cho macd @H1."""
    config = with_combo(shared_config()) if combo else shared_config()
    if break20:
        with_break20(config)
    the_plan = configuration.parse_plan(config, SERVERS)
    fake = FakeRedis()
    m10, m30, m45, h1 = make_bars(900, "10min"), make_bars(300, "30min"), make_bars(200, "45min"), make_bars(200, "1h")
    for frame, bars in (("M10", m10), ("M30", m30), ("M45", m45), ("H1", h1)):
        fake.put_candles("US30", frame, bars)
    if with_results:
        local = local_config()
        job = configuration.build_module_job(the_plan, local, "ma_cross", "default")
        decided = job.function(m10, **job.params).iloc[300:]
        fake.put_rows("L_MODSTR_MACROSS_US30_M10", signal_rows(decided, pd.Timedelta(minutes=10)))
        for factor in ("macd", "sma20"):
            fjob = configuration.build_module_job(the_plan, local, factor, "default")
            window = m30.assign(trend_bias=fjob.function(m30, **fjob.params))
            fake.put_rows(f"L_MODTRE_{factor.upper()}_US30_M30", bias_rows(window.iloc[100:], pd.Timedelta(minutes=30)))
        raw_stamps = fake.lists["L_MODSTR_MACROSS_US30_M10"]
        fake.put_rows("L_DE_US30_M10", [(stamp, _final_fields(stamp, "BUY" if i % 2 == 0 else "SELL")) for i, stamp in enumerate(raw_stamps[-2:])])
    return chart.Context(1, the_plan, lambda _endpoint, _db: fake), fake, the_plan


def _view(ctx: chart.Context, *, strategy: str = "ma_cross", bars: int = 200, token: str | None = None) -> dict:
    return chart.build_view(ctx, chart.ViewRequest(strategy, "US30", bars), token)


# -- hàm thuần ---------------------------------------------------------------------------------------------------------------------------------


def test_line_points_nan_thanh_diem_trong_va_histogram_to_mau_theo_dau() -> None:
    times = pd.Series(pd.date_range("2026-01-01", periods=4, freq="h"))
    assert chart.line_points(times, [np.nan, 1.0, 2.5, np.inf]) == [{"time": 1767225600}, {"time": 1767229200, "value": 1.0}, {"time": 1767232800, "value": 2.5}, {"time": 1767236400}]
    hist = chart.histogram_points(times, [np.nan, 0.5, -0.5, 0.0])
    assert hist[0] == {"time": 1767225600}
    assert [p["color"] for p in hist[1:]] == [chart.BUY_COLOR, chart.SELL_COLOR, chart.NEUTRAL_COLOR]


def test_unix_times_la_gio_utc_khong_mui_gio() -> None:
    assert chart.unix_times(pd.Series([pd.Timestamp("1970-01-01 00:01:00"), pd.Timestamp("2026-01-01")])) == [60, 1767225600]


def test_strategy_series_ve_dung_cac_duong_tham_so_khai_va_bo_qua_tham_so_la() -> None:
    frame = make_bars(200)
    window = chart._read_window(frame, 50)
    lines, panel = chart.strategy_series({"fast_ma": 5, "slow_ma": 20, "macd_fast": 5, "macd_slow": 25, "macd_signal": 5, "atr_x": 9}, frame, window)
    assert [line["key"] for line in lines] == ["fast_ma", "slow_ma"] and [line["label"] for line in lines] == ["SMA 5 (fast)", "SMA 20 (slow)"]
    expected = sma(frame["close"], 20)[window].tolist()
    assert [p["value"] for p in lines[1]["data"]] == pytest.approx(expected) and len(lines[1]["data"]) == 50
    assert [s["key"] for s in panel] == ["macd_hist"] and panel[0]["label"] == "MACD Histogram 5/25/5"
    assert [p["value"] for p in panel[0]["data"]] == pytest.approx(macd_hist(frame["close"], fast=5, slow=25, signal=5)[window].tolist())
    # thiếu một trong ba tham số MACD thì không có pane MACD; tham số lạ không vẽ gì
    assert chart.strategy_series({"macd_fast": 5, "macd_slow": 25}, frame, window) == ([], [])
    [single] = chart.strategy_series({"ma_period": 20}, frame, window)[0]
    assert single["key"] == "ma_period" and single["label"] == "SMA 20"


def test_factor_series_theo_loai_va_loai_la_khong_co_duong() -> None:
    frame = make_bars(200)
    window = chart._read_window(frame, 80)
    [line] = chart.factor_series("sma20", "sma", {"period": 20}, frame, window)
    assert line["key"] == "sma20.sma" and line["label"] == "SMA 20" and [p["value"] for p in line["data"]] == pytest.approx(sma(frame["close"], 20)[window].tolist())
    assert all("color" not in p for p in line["data"])  # không truyền bias thì đường một màu
    high, low = chart.factor_series("break20", "break", {"lookback": 20}, frame, window)
    top = frame["high"].shift(1).rolling(20).max()[window].tolist()
    bottom = frame["low"].shift(1).rolling(20).min()[window].tolist()
    assert (high["key"], low["key"]) == ("break20.high", "break20.low")
    assert high["label"] == "Breakout 20 high" and low["label"] == "Breakout 20 low"
    assert [p["value"] for p in high["data"]] == pytest.approx(top) and [p["value"] for p in low["data"]] == pytest.approx(bottom)
    [hist] = chart.factor_series("macd", "macd", {"fast": 5, "slow": 25, "signal": 5}, frame, window)
    assert hist["key"] == "macd.hist" and hist["label"] == "MACD Histogram 5/25/5" and hist["kind"] == "histogram" and hist["scale"] == "right"  # histogram có pane riêng, không còn thang "macd" chồng lên nến
    assert chart.factor_series("xyz", "unknown_kind", {}, frame, window) == []


def test_factor_series_to_mau_theo_bias_da_luu_cho_histogram_macd_va_duong_sma() -> None:
    frame = make_bars(60, "30min")
    window = chart._read_window(frame, 6)
    shown = [pd.Timestamp(at) for at in frame.loc[window, "bartime"]]
    stored = {shown[0]: 1, shown[1]: -1, shown[2]: 0}  # ba nến sau chưa có kết quả
    expected = [chart.BUY_COLOR, chart.SELL_COLOR, chart.NEUTRAL_COLOR, chart.NEUTRAL_COLOR, chart.NEUTRAL_COLOR, chart.NEUTRAL_COLOR]
    [hist] = chart.factor_series("macd", "macd", {"fast": 5, "slow": 25, "signal": 5}, frame, window, stored)
    assert [p["color"] for p in hist["data"]] == expected
    [line] = chart.factor_series("sma20", "sma", {"period": 20}, frame, window, stored)
    assert [p["color"] for p in line["data"]] == expected
    # đường đỉnh/đáy của break không tô theo bias (đã có dải bias riêng)
    assert all("color" not in p for s in chart.factor_series("break20", "break", {"lookback": 20}, frame, window, stored) for p in s["data"])


def test_colored_points_mau_theo_bias_da_luu_nan_la_diem_trong() -> None:
    times = pd.Series(pd.date_range("2026-01-01", periods=4, freq="h"))
    stored = {pd.Timestamp("2026-01-01 00:00"): 1, pd.Timestamp("2026-01-01 01:00"): -1}
    assert chart.colored_points(times, [1.0, 2.0, np.nan, 3.0], stored) == [
        {"time": 1767225600, "value": 1.0, "color": chart.BUY_COLOR},
        {"time": 1767229200, "value": 2.0, "color": chart.SELL_COLOR},
        {"time": 1767232800},
        {"time": 1767236400, "value": 3.0, "color": chart.NEUTRAL_COLOR},
    ]


def test_ten_hien_thi_viet_hoa_chu_viet_tat_va_yeu_to_theo_loai_va_tham_so() -> None:
    assert [chart.display_name(n) for n in ("ma_cross", "ema_cross", "combo", "macd_break", "ichimoku")] == ["MA Cross", "EMA Cross", "Combo", "MACD Break", "Ichimoku"]
    assert chart.trend_label("macd", "macd", {"fast": 5, "slow": 25, "signal": 5}) == "MACD Histogram 5/25/5"
    assert chart.trend_label("sma20", "sma", {"period": 20}) == "SMA 20"
    assert chart.trend_label("break20", "break", {"lookback": 20}) == "Breakout 20 (High/Low)"
    # thiếu tham số hoặc loại chưa biết thì dùng tên khai ở config, đã viết chuẩn
    assert chart.trend_label("macd", "macd", {"fast": 5}) == "MACD" and chart.trend_label("zigzag", "zigzag", {}) == "Zigzag"


def test_dong_tham_so_viet_bang_chu_chuan_theo_config_cua_chien_luoc() -> None:
    the_plan = configuration.parse_plan(with_combo(shared_config()), SERVERS)
    assert chart._params_label(the_plan.strategies["ma_cross"]) == "SMA 13/34 · MACD Histogram 5/25/5 · ATR 5 · shift up to 3 bars · valid 10 min · MARKET order"
    assert chart._params_label(the_plan.strategies["combo"]) == "SMA 20 · MACD Histogram 5/25/5 · ATR 5 · no shift · valid 90 min · STOP order"


def _positions(start: str, end: str, freq: str) -> dict[pd.Timestamp, int]:
    """Bảng vị trí các nến trigger liên tiếp (giờ mở nến -> chỉ số), như ``build_view`` dựng từ bảng nến đọc về."""
    return {stamp: index for index, stamp in enumerate(pd.date_range(start, end, freq=freq))}


def test_final_rows_gan_tin_hieu_cuoi_voi_tin_hieu_tho_goc_va_danh_dau_mo_coi() -> None:
    length = pd.Timedelta(minutes=10)
    position = _positions("2026-01-05 06:00", "2026-01-05 14:00", "10min")
    raw = pd.DataFrame({"bartime": pd.to_datetime(["2026-01-05 10:00", "2026-01-05 10:50", "2026-01-05 11:30"]), "signal": [1.0, -1.0, 1.0]})
    first = pd.Timestamp("2026-01-05 08:00")
    signals = [
        (stamp, {"tradeSide": side, "orderType": "MARKET", "atr": "2.0", "comment": "x"})
        for stamp, side in (("2026-01-05 07:00:00", "BUY"), ("2026-01-05 09:00:00", "BUY"), ("2026-01-05 10:20:00", "BUY"), ("2026-01-05 10:50:00", "SELL"), ("2026-01-05 12:30:00", "BUY"))
    ]
    rows = chart.final_rows(signals, first, length, raw, 3, position)
    assert [(r["bar"], r["origin"], r["shift"], r["orphan"]) for r in rows] == [
        ("2026-01-05 09:00:00", None, None, False),  # cũ hơn mọi tín hiệu thô còn giữ: không biết gốc, KHÔNG phải mồ côi
        ("2026-01-05 10:20:00", "2026-01-05 10:00:00", 2, False),  # bị dời 2 nến
        ("2026-01-05 10:50:00", "2026-01-05 10:50:00", 0, False),  # phát ngay ở nến tín hiệu
        ("2026-01-05 12:30:00", None, None, True),  # nằm trong vùng có kết quả thô mà không có tín hiệu thô cùng chiều trong 3 nến trước: mồ côi
    ]
    # chưa có kết quả thô nào thì không thể nói ai mồ côi
    nothing = pd.DataFrame({"bartime": pd.Series(dtype="datetime64[us]"), "close_time": pd.Series(dtype="datetime64[us]")})
    assert [r["orphan"] for r in chart.final_rows(signals, first, length, nothing, 3, position)] == [False, False, False, False]


def test_so_nen_doi_dem_theo_vi_tri_nen_nen_nghi_phien_khong_lam_sai() -> None:
    length = pd.Timedelta(minutes=10)
    grid = [*pd.date_range("2026-01-02 23:30", "2026-01-02 23:50", freq="10min"), *pd.date_range("2026-01-05 00:00", "2026-01-05 00:30", freq="10min")]  # thứ Sáu rồi nghỉ cuối tuần tới thứ Hai
    position = {stamp: index for index, stamp in enumerate(grid)}
    raw = pd.DataFrame({"bartime": pd.to_datetime(["2026-01-02 23:50"]), "signal": [1.0]})
    signals = [("2026-01-05 00:10:00", {"tradeSide": "BUY", "orderType": "MARKET"})]  # nến thứ hai sau nến gốc (23:50 -> 00:00 -> 00:10), cách hơn hai ngày theo giờ
    [row] = chart.final_rows(signals, pd.Timestamp("2026-01-02 00:00"), length, raw, 3, position)
    assert (row["origin"], row["shift"], row["orphan"]) == ("2026-01-02 23:50:00", 2, False)


def test_raw_rows_ghi_nen_phat_cua_tin_hieu_cuoi_lay_no_lam_goc_va_cho_trong_khoang_dich() -> None:
    length = pd.Timedelta(minutes=10)
    position = _positions("2026-01-05 06:00", "2026-01-05 14:00", "10min")
    raw = pd.DataFrame({"bartime": pd.to_datetime(["2026-01-05 10:00", "2026-01-05 10:50", "2026-01-05 11:30"]), "signal": [1.0, -1.0, 1.0], "reason": ["a", "b", "c"]})
    finals = [{"origin": "2026-01-05 10:00:00", "bar": "2026-01-05 10:20:00"}]
    rows = chart.raw_rows(raw, pd.Timestamp("2026-01-05 08:00"), length, finals, pd.Timestamp("2026-01-05 11:40"), 3, position)
    assert [(r["bar"], r["side"], r["decidedAt"], r["pending"]) for r in rows] == [
        ("2026-01-05 10:00:00", "BUY", "2026-01-05 10:20:00", False),  # đã được quyết định ở nến sau 2 nến
        ("2026-01-05 10:50:00", "SELL", None, False),  # quá khoảng dời và không có tín hiệu cuối: không được phát
        ("2026-01-05 11:30:00", "BUY", None, True),  # còn trong khoảng dời kể từ nến mới nhất: DES có thể còn đang chờ trend
    ]


def test_chien_luoc_khong_doi_chi_noi_tin_hieu_cuoi_voi_cham_o_cung_nen_va_cho_chi_o_nen_moi_nhat() -> None:
    length = pd.Timedelta(minutes=30)
    position = _positions("2026-01-05 08:00", "2026-01-05 13:00", "30min")
    raw = pd.DataFrame({"bartime": pd.to_datetime(["2026-01-05 10:00", "2026-01-05 12:00"]), "signal": [1.0, -1.0], "reason": ["a", "b"]})
    signals = [(stamp, {"tradeSide": side, "orderType": "STOP", "atr": "2.0", "comment": "x"}) for stamp, side in (("2026-01-05 10:00:00", "BUY"), ("2026-01-05 11:00:00", "BUY"), ("2026-01-05 12:00:00", "SELL"))]
    rows = chart.final_rows(signals, pd.Timestamp("2026-01-05 08:00"), length, raw, 0, position)
    # Combo không dời: tín hiệu cuối chỉ có gốc ở ĐÚNG nến của nó; cái phát 2 nến sau chấm gốc không có gốc nào (do bản luật cũ có dời)
    assert [(r["bar"], r["shift"], r["orphan"]) for r in rows] == [("2026-01-05 10:00:00", 0, False), ("2026-01-05 11:00:00", None, True), ("2026-01-05 12:00:00", 0, False)]
    finals = [{"origin": "2026-01-05 10:00:00", "bar": "2026-01-05 10:00:00"}, {"origin": "2026-01-05 12:00:00", "bar": "2026-01-05 12:00:00"}]
    pending = chart.raw_rows(raw, pd.Timestamp("2026-01-05 08:00"), length, finals, pd.Timestamp("2026-01-05 12:00"), 0, position)
    assert [(r["bar"], r["decidedAt"], r["pending"]) for r in pending] == [("2026-01-05 10:00:00", "2026-01-05 10:00:00", False), ("2026-01-05 12:00:00", "2026-01-05 12:00:00", True)]
    undecided = chart.raw_rows(raw, pd.Timestamp("2026-01-05 08:00"), length, [], pd.Timestamp("2026-01-05 12:30"), 0, position)
    assert [r["pending"] for r in undecided] == [False, False]  # đã qua nến phát của nó mà chưa có tín hiệu cuối: không còn chờ nữa


def _trend_results(bars: list[tuple[str, int]], length: pd.Timedelta) -> pd.DataFrame:
    """Kết quả đã lưu của một yếu tố trend như ``read_module`` trả về: ``bartime``, ``close_time`` (giờ mở cộng độ dài khung) và ``trend_bias``."""
    opened = pd.to_datetime([stamp for stamp, _ in bars])
    return pd.DataFrame({"bartime": opened, "close_time": opened + length, "trend_bias": [float(bias) for _, bias in bars]})


def test_reference_at_chon_nen_trend_dung_luat_reality_epoch_nhu_engine() -> None:
    from des.src.decision_engine import merge_closed_reference

    half_hour = pd.Timedelta(minutes=30)
    trend = _trend_results([("2026-01-05 09:30", -1), ("2026-01-05 10:00", 1), ("2026-01-05 10:30", 0), ("2026-01-05 11:00", 1)], half_hour)
    entries = pd.DataFrame({"bartime": pd.date_range("2026-01-05 09:00", "2026-01-05 11:20", freq="10min")})
    entries["close_time"] = entries["bartime"] + pd.Timedelta(minutes=10)
    merged = merge_closed_reference(entries, trend.rename(columns={"trend_bias": "bias"}), ["bias"], ref_close_name="ref_close")
    for row in merged.itertuples():
        found = chart.reference_at(trend, row.close_time)
        if pd.isna(row.ref_close):
            assert found is None  # chưa có nến trend nào đóng xong: engine cũng không có gì để tham chiếu
        else:
            assert found is not None and found[1] == row.ref_close and found[2] == int(row.bias)
    # ví dụ cụ thể: nến M10 mở 10:20 (đóng 10:30) dùng nến M30 mở 10:00 (đóng cùng lúc 10:30); nến M10 mở 10:10 (đóng 10:20) vẫn dùng nến M30 mở 09:30 vì nến 10:00 chưa đóng
    assert chart.reference_at(trend, pd.Timestamp("2026-01-05 10:30")) == (pd.Timestamp("2026-01-05 10:00"), pd.Timestamp("2026-01-05 10:30"), 1)
    assert chart.reference_at(trend, pd.Timestamp("2026-01-05 10:20")) == (pd.Timestamp("2026-01-05 09:30"), pd.Timestamp("2026-01-05 10:00"), -1)
    assert chart.reference_at(trend.iloc[:0], pd.Timestamp("2026-01-05 10:30")) is None


def test_chuoi_kiem_trend_cua_tin_hieu_bi_doi_liet_ke_tung_nen_da_xet_va_nen_trend_da_dung() -> None:
    length = pd.Timedelta(minutes=10)
    bar_times = list(pd.date_range("2026-01-05 09:00", "2026-01-05 12:00", freq="10min"))
    position = {stamp: index for index, stamp in enumerate(bar_times)}
    half_hour = pd.Timedelta(minutes=30)
    macd = _trend_results([("2026-01-05 09:30", -1), ("2026-01-05 10:00", 1), ("2026-01-05 10:30", 1), ("2026-01-05 11:00", -1), ("2026-01-05 11:30", -1)], half_hour)
    sma = _trend_results([("2026-01-05 09:30", 1), ("2026-01-05 10:00", 1), ("2026-01-05 10:30", 1), ("2026-01-05 11:00", 1), ("2026-01-05 11:30", 1)], half_hour)
    references = [("macd", macd), ("sma20", sma)]
    raw = pd.DataFrame({"bartime": pd.to_datetime(["2026-01-05 10:00", "2026-01-05 11:00", "2026-01-05 11:20"]), "signal": [1.0, -1.0, 1.0], "reason": ["a", "b", "c"]})
    signals = [("2026-01-05 10:20:00", {"tradeSide": "BUY", "orderType": "MARKET"})]
    first = pd.Timestamp("2026-01-05 09:00")
    finals = chart.final_rows(signals, first, length, raw, 3, position)
    raws = chart.raw_rows(raw, first, length, finals, bar_times[-1], 3, position)
    chains = chart.attach_chains(finals, raws, chart._raw_events(raw, position), references, bar_times, position, length, 3)
    # BUY mở 10:00 bị dời 2 nến: 10:00 và 10:10 còn dùng nến M30 mở 09:30 (hướng -1, ngược), tới 10:20 mới có nến M30 mở 10:00 (đóng 10:30, +1) và SMA cùng chiều
    fired = chains[finals[0]["chain"]]
    assert (fired["raw"], fired["fired"], fired["side"]) == ("2026-01-05 10:00:00", "2026-01-05 10:20:00", "BUY")
    assert [(e["bar"], e["ok"]) for e in fired["evals"]] == [("2026-01-05 10:00:00", False), ("2026-01-05 10:10:00", False), ("2026-01-05 10:20:00", True)]
    first_eval, last_eval = fired["evals"][0], fired["evals"][-1]
    assert [(r["name"], r["bar"], r["bias"], r["agree"]) for r in first_eval["refs"]] == [("macd", "2026-01-05 09:30:00", -1, False), ("sma20", "2026-01-05 09:30:00", 1, True)]
    assert [(r["name"], r["bar"], r["close"], r["bias"], r["agree"]) for r in last_eval["refs"]] == [
        ("macd", "2026-01-05 10:00:00", "2026-01-05 10:30:00", 1, True), ("sma20", "2026-01-05 10:00:00", "2026-01-05 10:30:00", 1, True)
    ]
    assert raws[0]["chain"] == finals[0]["chain"] and raws[0]["decidedAt"] == "2026-01-05 10:20:00"  # chấm gốc và mũi tên dùng chung một chuỗi
    # SELL mở 11:00 không được phát: bị hủy bởi BUY thô mở 11:20 nên chỉ xét 11:00 và 11:10 (không xét 11:20 trở đi dù còn trong 3 nến)
    cancelled = chains[raws[1]["chain"]]
    assert (cancelled["raw"], cancelled["fired"]) == ("2026-01-05 11:00:00", None) and [e["bar"] for e in cancelled["evals"]] == ["2026-01-05 11:00:00", "2026-01-05 11:10:00"]
    # BUY thô mở 11:20 chưa có tín hiệu cuối: xét từ 11:20 tối đa 3 nến sau (11:50), dừng ở đó dù nến mới nhất là 12:00
    waiting = chains[raws[2]["chain"]]
    assert [e["bar"] for e in waiting["evals"]] == ["2026-01-05 11:20:00", "2026-01-05 11:30:00", "2026-01-05 11:40:00", "2026-01-05 11:50:00"]
    assert not any(e["ok"] for e in waiting["evals"])  # MACD M30 lúc này là -1, ngược BUY


def test_bias_points_khop_ket_qua_da_luu_va_bo_nen_chua_co_ket_qua() -> None:
    frame = make_bars(6, "30min")
    results = pd.DataFrame({"bartime": frame["bartime"].iloc[2:5], "trend_bias": [1.0, -1.0, 0.0]})
    points = chart.bias_points(frame, results)
    assert [p["value"] for p in points] == [1, -1, 0] and [p["time"] for p in points] == chart.unix_times(frame["bartime"].iloc[2:5])
    assert chart.bias_points(frame, pd.DataFrame({"bartime": [], "close_time": []})) == []


def test_parse_request_chi_nhan_chien_luoc_va_symbol_cua_config_va_dua_so_nen_ve_khoang_cho_phep() -> None:
    the_plan = configuration.parse_plan(shared_config(), SERVERS)
    assert chart.parse_request({"strategy": "MA_Cross", "symbol": "us30", "bars": "99999"}, the_plan) == chart.ViewRequest("ma_cross", "US30", chart.BARS_MAX)
    assert chart.parse_request({"strategy": "ma_cross", "symbol": "US30"}, the_plan).bars == chart.DEFAULT_BARS
    assert chart.parse_request({"strategy": "ma_cross", "symbol": "US30", "bars": "1"}, the_plan).bars == chart.BARS_MIN
    for args, message in (({"strategy": "x", "symbol": "US30"}, "strategy 'x'"), ({"strategy": "ma_cross", "symbol": "ZZZ"}, "symbol 'ZZZ'"), ({"strategy": "ma_cross", "symbol": "US30", "bars": "abc"}, "bars must be")):
        with pytest.raises(ValueError, match=message):
            chart.parse_request(args, the_plan)


def test_catalog_lay_het_tu_config_chien_luoc_khung_symbol_va_yeu_to() -> None:
    ctx, _, _ = _world(combo=True)
    catalog = chart.build_catalog(ctx)
    assert catalog["configVersion"] == 1 and set(catalog["strategies"]) == {"ma_cross", "combo"}
    assert catalog["strategies"]["ma_cross"]["trigger"] == "M10" and catalog["strategies"]["ma_cross"]["symbols"] == ["US30", "BTCUSD"]
    assert [(t["name"], t["frame"], t["type"]) for t in catalog["strategies"]["ma_cross"]["trends"]] == [("macd", "M30", "macd"), ("sma20", "M30", "sma")]
    assert catalog["strategies"]["combo"]["orderType"] == "STOP" and catalog["strategies"]["combo"]["trends"][0]["params"] == {"fast": 5, "slow": 25, "signal": 5}
    # tên hiển thị: người xem thấy "MA Cross"/"Combo" và tên chuẩn của từng yếu tố, không thấy khoá thô của config
    assert (catalog["strategies"]["ma_cross"]["label"], catalog["strategies"]["combo"]["label"]) == ("MA Cross", "Combo")
    assert [t["label"] for t in catalog["strategies"]["ma_cross"]["trends"]] == ["MACD Histogram 5/25/5", "SMA 20"]
    assert [(t["name"], t["frame"], t["label"]) for t in catalog["strategies"]["combo"]["trends"]] == [("macd", "H1", "MACD Histogram 5/25/5")]


# -- khung nhìn ---------------------------------------------------------------------------------------------------------------------------------


def test_view_ma_cross_co_pane_gia_macd_va_mot_pane_cho_moi_yeu_to_chi_ve_dung_thu_yeu_to_do_dung() -> None:
    ctx, _fake, _ = _world()
    view = _view(ctx, bars=200)
    assert view["changed"] is True and [pane["id"] for pane in view["panes"]] == ["price", "macd", "trend.macd", "trend.sma20"]
    price = view["panes"][0]
    assert len(price["candles"]) == 200 and [s["key"] for s in price["series"]] == ["fast_ma", "slow_ma"]
    assert all(len(s["data"]) == 200 for s in price["series"])
    assert (price["title"], price["group"], price["hasCandles"], price["biasStrip"]) == ("MA Cross · US30 · M10", "Price", True, False)
    assert view["meta"]["triggerSeconds"] == 600 and price["frameSeconds"] == 600 and view["panes"][1]["frameSeconds"] == 600  # pane giá và pane MACD của chiến lược cùng khung trigger M10
    assert [view["panes"][2]["frameSeconds"], view["panes"][3]["frameSeconds"]] == [1800, 1800]  # hai pane trend ở M30
    finals = [m for m in price["markers"] if m["kind"] == "final"]
    raws = [m for m in price["markers"] if m["kind"] == "raw"]
    assert [m["side"] for m in finals] == ["BUY", "SELL"] and len(raws) == len([r for r in view["signals"] if r["kind"] == "raw"]) > 0
    assert price["markers"] == sorted(price["markers"], key=lambda m: (m["time"], m["kind"] == "final"))
    assert all("shift" in m and "orphan" not in m for m in price["markers"])  # không có loại dấu "mồ côi" nào để vẽ
    assert all(isinstance(row["chain"], int) for row in view["signals"]) and len(view["chains"]) > 0
    assert all([r["name"] for r in ev["refs"]] == ["macd", "sma20"] for chain in view["chains"] for ev in chain["evals"])  # mỗi nến xét có đủ mọi yếu tố trend của chiến lược
    macd_pane, trend_macd, trend_sma = view["panes"][1], view["panes"][2], view["panes"][3]
    assert macd_pane["candles"] == [] and len(macd_pane["series"][0]["data"]) == 200
    assert (macd_pane["title"], macd_pane["group"], macd_pane["hasCandles"]) == ("MACD Histogram 5/25/5 · M10", "Strategy · M10", False)
    # yếu tố MACD chỉ có histogram (không nến, không dải bias), mỗi cột tô theo bias đã lưu ở db11
    assert (trend_macd["title"], trend_macd["group"], trend_macd["factorLabel"], trend_macd["frame"]) == ("Trend reference · MACD Histogram 5/25/5 · M30", "Trend · M30", "MACD Histogram 5/25/5", "M30")
    assert trend_macd["hasCandles"] is False and trend_macd["candles"] == [] and trend_macd["biasStrip"] is False
    assert [s["key"] for s in trend_macd["series"]] == ["macd.hist"] and len(trend_macd["bias"]) > 0
    colors = {1: chart.BUY_COLOR, -1: chart.SELL_COLOR, 0: chart.NEUTRAL_COLOR}
    stored = {b["time"]: b["value"] for b in trend_macd["bias"]}
    assert all(p["color"] == colors[stored[p["time"]]] for p in trend_macd["series"][0]["data"] if "value" in p and p["time"] in stored)
    # yếu tố SMA có nến và đường SMA tô theo bias đã lưu, không dải bias
    assert (trend_sma["title"], trend_sma["factorLabel"], trend_sma["hasCandles"], trend_sma["biasStrip"]) == ("Trend reference · SMA 20 · M30", "SMA 20", True, False)
    assert trend_sma["series"][0]["key"] == "sma20.sma" and {b["value"] for b in trend_sma["bias"]} <= {-1, 0, 1} and len(trend_sma["candles"]) > 0
    assert {p["color"] for p in trend_sma["series"][0]["data"] if "value" in p} <= set(colors.values())
    # cửa sổ của pane yếu tố bắt đầu ở nến chứa nến trigger đầu tiên của cửa sổ hiển thị
    first_time = price["candles"][0]["time"]
    assert first_time - 2 * 1800 <= trend_sma["candles"][0]["time"] <= first_time  # lùi hai khung M30: nến trend engine tham chiếu cho nến trigger đầu có thể mở sớm gần hai khung trước nó
    histogram = trend_macd["series"][0]["data"]
    assert first_time - 2 * 1800 <= histogram[0]["time"] <= first_time
    meta = view["meta"]
    assert meta["warnings"] == [] and meta["lastCandle"] == stamp_of(make_bars(900, "10min")["bartime"].iloc[-1]) and meta["finalCount"] == 2 and meta["configVersion"] == 1
    assert meta["strategyLabel"] == "MA Cross" and meta["paramsLabel"].startswith("SMA 13/34 · MACD Histogram 5/25/5 · ATR 5") and meta["maxShiftBars"] == 3
    assert meta["clock"] == {"simTime": "2026-01-10 12:00:00", "tick": 42, "status": "running", "runId": "run1"}
    finals_rows = [r for r in view["signals"] if r["kind"] == "final"]
    assert finals_rows[0]["orderType"] == "MARKET" and finals_rows[0]["atr"] == 3.5 and finals_rows[0]["until"] is not None
    assert [r["time"] for r in view["signals"]] == sorted((r["time"] for r in view["signals"]), reverse=True)


def test_view_combo_tham_chieu_macd_o_khung_h1_chi_ve_histogram_to_mau_theo_bias() -> None:
    ctx, _fake, _ = _world(combo=True)
    view = _view(ctx, strategy="combo", bars=100)
    assert [pane["id"] for pane in view["panes"]] == ["price", "macd", "trend.macd"]
    pane = view["panes"][2]
    assert (pane["title"], pane["group"], pane["factorLabel"], pane["frame"]) == ("Trend reference · MACD Histogram 5/25/5 · H1", "Trend · H1", "MACD Histogram 5/25/5", "H1")
    assert (pane["hasCandles"], pane["biasStrip"], pane["candles"]) == (False, False, []) and [s["key"] for s in pane["series"]] == ["macd.hist"]
    assert (view["meta"]["triggerSeconds"], view["panes"][0]["frameSeconds"], pane["frameSeconds"]) == (1800, 1800, 3600)  # Combo: trigger M30, trend MACD ở H1
    assert view["panes"][0]["series"][0]["key"] == "ma_period" and view["panes"][0]["title"] == "Combo · US30 · M30"
    assert view["meta"]["strategyLabel"] == "Combo" and view["meta"]["paramsLabel"].startswith("SMA 20 · MACD Histogram 5/25/5")
    assert view["meta"]["maxShiftBars"] == 0 and "no shift" in view["meta"]["paramsLabel"]  # Combo không dời tín hiệu
    # ma_cross chưa có kết quả combo ở Redis giả: không có dấu nhưng cũng không lỗi
    assert view["meta"]["finalCount"] == 0 and view["meta"]["rawCount"] == 0


def test_view_yeu_to_break_khai_trong_config_co_nen_hai_duong_dinh_day_va_dai_bias() -> None:
    ctx, _fake, _ = _world(combo=True, break20=True)
    view = _view(ctx, strategy="combo", bars=100)
    assert [pane["id"] for pane in view["panes"]] == ["price", "macd", "trend.break20"]
    pane = view["panes"][2]
    assert pane["frame"] == "M45" and [s["key"] for s in pane["series"]] == ["break20.high", "break20.low"]
    assert (pane["title"], pane["group"], pane["factorLabel"], pane["hasCandles"], pane["biasStrip"]) == ("Trend reference · Breakout 20 (High/Low) · M45", "Trend · M45", "Breakout 20 (High/Low)", True, True)


def test_dau_van_tay_khong_doi_thi_chi_tra_gio_ao_va_doi_khi_co_tin_hieu_hoac_nen_moi() -> None:
    ctx, fake, _ = _world()
    first = _view(ctx)
    again = _view(ctx, token=first["token"])
    assert again == {"changed": False, "meta": again["meta"]} and again["meta"]["clock"]["runId"] == "run1"
    stamp = "2026-01-07 10:00:00"
    fake.lists["L_DE_US30_M10"].append(stamp)
    fake.hashes[f"L_DE_US30_M10:{stamp}"] = _final_fields(stamp)
    assert _view(ctx, token=first["token"])["changed"] is True  # thêm một tín hiệu cuối
    assert _view(ctx, bars=100, token=first["token"])["changed"] is True  # đổi số nến hiển thị
    fake.lists["L_CANDLE_US30_M30"].insert(0, "2026-01-11 00:00:00")
    assert chart.fingerprint(ctx, chart.ViewRequest("ma_cross", "US30", 200)) != first["token"]  # có nến khung yếu tố mới


def test_thieu_nen_trigger_thi_khung_nhin_rong_co_canh_bao_khong_loi() -> None:
    ctx, fake, _ = _world()
    del fake.lists["L_CANDLE_US30_M10"]
    view = _view(ctx)
    assert view["panes"] == [] and view["signals"] == [] and "No M10 candles for US30" in view["meta"]["warnings"][0]


def test_thieu_ket_qua_yeu_to_thi_pane_van_co_duong_nhung_khong_co_bias_va_cot_mau_xam() -> None:
    ctx, fake, _ = _world()
    del fake.lists["L_MODTRE_MACD_US30_M30"]
    del fake.lists["L_MODTRE_SMA20_US30_M30"]
    view = _view(ctx)
    macd_pane = next(p for p in view["panes"] if p["id"] == "trend.macd")
    sma_pane = next(p for p in view["panes"] if p["id"] == "trend.sma20")
    assert macd_pane["candles"] == [] and macd_pane["series"] and macd_pane["bias"] == []
    assert {p["color"] for p in macd_pane["series"][0]["data"] if "value" in p} == {chart.NEUTRAL_COLOR}  # chưa có bias đã lưu thì xám, không đoán màu
    assert sma_pane["candles"] and sma_pane["series"] and sma_pane["bias"] == []
    assert any("No stored results for MACD Histogram 5/25/5 on US30 M30" in w for w in view["meta"]["warnings"])
    assert any("No stored results for SMA 20 on US30 M30" in w for w in view["meta"]["warnings"])


def test_tin_hieu_cuoi_bi_dich_hoac_khong_co_goc_duoc_bao_ma_khong_ve_thanh_dau_rieng() -> None:
    ctx, fake, _ = _world()
    bars = make_bars(900, "10min")["bartime"]
    at = lambda index: stamp_of(bars.iloc[index])
    length = pd.Timedelta(minutes=10)
    raw = [(at(-40), "1"), (at(-20), "-1")]
    fake.put_rows("L_MODSTR_MACROSS_US30_M10", [(stamp, {"close_time": stamp_of(pd.Timestamp(stamp) + length), "signal": signal, "reason": "r"}) for stamp, signal in raw])
    fake.put_rows("L_DE_US30_M10", [(at(-38), _final_fields(at(-38), "BUY")), (at(-20), _final_fields(at(-20), "SELL")), (at(-10), _final_fields(at(-10), "BUY"))])
    view = _view(ctx, bars=200)
    price = view["panes"][0]
    assert [(m["kind"], m["side"], m["shift"]) for m in price["markers"] if m["kind"] == "final"] == [("final", "BUY", 2), ("final", "SELL", 0), ("final", "BUY", None)]
    meta = view["meta"]
    assert (meta["rawCount"], meta["finalCount"], meta["shiftedCount"], meta["orphanCount"]) == (2, 3, 1, 1)
    assert any("1 decided signal(s) in view have no matching raw signal" in w for w in meta["warnings"])
    raw_rows_by_bar = {r["bar"]: r for r in view["signals"] if r["kind"] == "raw"}
    assert raw_rows_by_bar[at(-40)]["decidedAt"] == at(-38) and raw_rows_by_bar[at(-20)]["decidedAt"] == at(-20)
    assert not any(r["pending"] for r in raw_rows_by_bar.values())  # đã quá khoảng dời so với nến mới nhất
    # chuỗi kiểm: tín hiệu bị dời 2 nến có 3 nến được xét, tín hiệu cuối không có gốc chỉ có nến phát của nó
    by_bar = {(r["kind"], r["bar"]): r for r in view["signals"]}
    assert len(view["chains"][by_bar[("final", at(-38))]["chain"]]["evals"]) == 3
    assert [e["bar"] for e in view["chains"][by_bar[("final", at(-10))]["chain"]]["evals"]] == [at(-10)]


def test_thieu_nen_khung_yeu_to_thi_pane_rong_co_canh_bao() -> None:
    ctx, fake, _ = _world()
    del fake.lists["L_CANDLE_US30_M30"]
    view = _view(ctx)
    assert [p["candles"] for p in view["panes"] if p["id"].startswith("trend.")] == [[], []]
    assert sum("No M30 candles for US30" in w for w in view["meta"]["warnings"]) == 2


def test_so_nen_hon_cua_so_ket_qua_cua_des_thi_canh_bao() -> None:
    ctx, _, the_plan = _world()
    view = _view(ctx, bars=the_plan.snapshot_bars + 100)
    assert any(f"last {the_plan.snapshot_bars} bars" in w for w in view["meta"]["warnings"])
    assert not _view(ctx, bars=100)["meta"]["warnings"]


def test_tin_hieu_cuoi_cua_chien_luoc_khac_cung_khoa_khong_hien_vao_chart() -> None:
    ctx, fake, _ = _world()
    stamp = fake.lists["L_DE_US30_M10"][-1]
    fake.hashes[f"L_DE_US30_M10:{stamp}"]["strategy"] = "combo"
    assert _view(ctx)["meta"]["finalCount"] == 1


def test_chart_chi_dung_lenh_doc() -> None:
    ctx, fake, _ = _world()
    _view(ctx)
    assert set(fake.commands) <= {"get", "hgetall", "hget", "hmget", "lrange", "lindex", "llen"}


# -- Provider + route Flask ---------------------------------------------------------------------------------------------------------------------


def test_provider_nap_lai_config_sau_khoang_thoi_gian_va_giu_ban_cu_khi_nap_loi() -> None:
    now = [0.0]
    versions = iter([(1, plan()), (2, plan()), "loi"])
    calls: list[int] = []

    def loader():
        calls.append(1)
        item = next(versions)
        if item == "loi":
            raise redis.ConnectionError("mat ket noi")
        return item

    provider = chart.Provider(local_config(), loader=loader, connect=lambda _e, _d: FakeRedis(), clock=lambda: now[0])
    assert provider.get().version == 1 and provider.get().version == 1 and len(calls) == 1  # trong hạn: không nạp lại
    now[0] = chart.CONFIG_REFRESH_SECONDS
    assert provider.get().version == 2 and len(calls) == 2
    now[0] = 2 * chart.CONFIG_REFRESH_SECONDS
    assert provider.get().version == 2 and len(calls) == 3  # nạp lỗi: giữ bản cũ


def test_provider_chua_co_config_nao_thi_loi_noi_len() -> None:
    def loader():
        raise RuntimeError("Redis chưa có config nào")

    with pytest.raises(RuntimeError, match="chưa có config"):
        chart.Provider(local_config(), loader=loader).get()


def _client(combo: bool = False):
    ctx, fake, _ = _world(combo=combo)
    provider = chart.Provider(local_config(), loader=lambda: (ctx.version, ctx.plan), connect=ctx.connect)
    return chart.create_app(provider).test_client(), fake


def test_route_trang_nhung_san_thu_vien_va_khong_con_cho_giu_cho() -> None:
    client, _ = _client()
    response = client.get("/")
    html = response.get_data(as_text=True)
    assert response.status_code == 200 and "LightweightCharts" in html and "__VENDOR_JS__" not in html and "/api/view" in html
    assert 'id="refs"' in html and "Raw signals" in html and "Decided signals" in html  # dòng yếu tố trend tham chiếu + hai lớp dấu tách bạch
    assert "never delays a signal" in html  # chiến lược không dời (Combo) có lời tóm tắt riêng
    assert "chart-legend" in html and "setCrosshairPosition" in html  # chú thích góc phải pane giá + crosshair đồng bộ giữa các pane khi rê hoặc bấm ở khung trigger
    assert "Trend reference check" not in html  # khung chi tiết không còn khối chữ kiểm trend: chỉ còn dấu "ref" trên pane trend
    assert client.get("/health").get_json() == {"status": "ok"}
    assert client.get("/favicon.ico").status_code == 204  # trình duyệt luôn hỏi: không để 404 làm nhiễu console


def test_route_catalog_va_view_tra_json_va_dau_van_tay_dung_cho_lan_sau() -> None:
    client, _ = _client()
    catalog = client.get("/api/catalog").get_json()["strategies"]["ma_cross"]
    assert catalog["trigger"] == "M10" and catalog["label"] == "MA Cross"
    response = client.get("/api/view?strategy=ma_cross&symbol=US30&bars=150")
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    view = response.get_json()
    assert len(view["panes"][0]["candles"]) == 150
    assert client.get(f"/api/view?strategy=ma_cross&symbol=US30&bars=150&token={view['token']}").get_json()["changed"] is False


@pytest.mark.parametrize(
    "query", ["strategy=x&symbol=US30", "strategy=ma_cross&symbol=ZZZ", "strategy=ma_cross&symbol=US30&bars=abc", ""],
)
def test_route_view_tham_so_sai_thi_400_voi_thong_bao(query: str) -> None:
    client, _ = _client()
    response = client.get(f"/api/view?{query}")
    assert response.status_code == 400 and response.get_json()["error"]


def test_route_redis_loi_thi_503_va_chua_co_config_thi_503() -> None:
    client, fake = _client()

    def boom(*_args: object) -> None:
        raise redis.ConnectionError("down")

    fake.get = boom  # type: ignore[method-assign]
    response = client.get("/api/view?strategy=ma_cross&symbol=US30")
    assert response.status_code == 503 and "Redis is not responding" in response.get_json()["error"]

    def loader():
        raise RuntimeError("Redis chưa có config nào")

    app = chart.create_app(chart.Provider(local_config(), loader=loader))
    assert app.test_client().get("/api/catalog").status_code == 503
