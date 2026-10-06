"""Test characterization cho order_gateway — hermetic, KHÔNG đụng Redis.

Mọi giá trị mong đợi trong file này được tính từ CHÍNH pipeline thật trên bộ
nến tổng hợp tất định bên dưới (không phải số bịa). Mục đích: khoá lại hành
vi hiện tại để phát hiện thay đổi logic ngoài ý muốn. Khi sửa logic chiến
lược CÓ CHỦ ĐÍCH, phải chạy lại pipeline để tính giá trị mới rồi cập nhật
test — không tự đoán số.

Cần og_config.yaml tồn tại (hệ thống chỉ chạy trên vm-og, luôn có sẵn file
thật). KHÔNG mở kết nối Redis nào.
"""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd
import pytest

from order_gateway.src.configuration import (
    COMBO_TREND_TIMEFRAMES,
    STRATEGIES,
    get_strategy,
    normalize_combo_params,
    normalize_ma_cross_params,
    run_strategy,
    run_strategy_with_trend_reference,
    trend_reference_for,
)
from order_gateway.src.indicator import atr, rma, sma
from order_gateway.src.redis_io.candle_reader import (
    candle_key,
    normalize_redis_config,
    pair_list_key,
    parse_pair_key,
)
from order_gateway.src.redis_io.live_worker import (
    signal_window,
    strategies_for_timeframe,
    timeframe_duration,
)
from order_gateway.src.redis_io.signal_publisher import (
    build_signal,
    channel_name,
    og_list_key,
    publish_signal,
    signal_message,
    stamp_hash_key,
    strategy_token,
    to_hash_fields,
)

# =============================================================================
# Bộ nến tổng hợp tất định — không random, nên mọi lần chạy cho cùng kết quả.
# =============================================================================


def synthetic_bars(n: int = 400) -> pd.DataFrame:
    """Sin nhiều chu kỳ + trend nhẹ: đủ để sinh CẢ BUY và SELL cho 2 chiến lược."""
    i = np.arange(n, dtype=float)
    base = 1000.0 + 40.0 * np.sin(i / 9.0) + 12.0 * np.sin(i / 2.7) + 0.35 * i
    op = base + 0.8 * np.sin(i / 3.3)
    cl = base - 0.8 * np.sin(i / 3.3)
    hi = np.maximum(op, cl) + 2.0 + np.abs(np.sin(i / 5.0))
    lo = np.minimum(op, cl) - 2.0 - np.abs(np.cos(i / 4.0))
    return pd.DataFrame(
        {
            "bartime": pd.date_range("2026-01-01", periods=n, freq="h").astype("datetime64[us]"),
            "open": op,
            "high": hi,
            "low": lo,
            "close": cl,
            "volume": np.nan,
        }
    )


@pytest.fixture(scope="module")
def bars() -> pd.DataFrame:
    return synthetic_bars()


def _pipeline_digest(df: pd.DataFrame) -> str:
    cols = ["signal", "entry_price", "atr"]
    return hashlib.sha256(df[cols].to_csv(index=False, float_format="%.8f").encode()).hexdigest()


# =============================================================================
# 1. Golden lock: toàn bộ pipeline 1 lượt chạy.
# =============================================================================

# Digest tính từ pipeline thật trên synthetic_bars() ở trên, 3 cột
# signal/entry_price/atr. Tính lại 2026-10-04 khi bỏ SL/TP khỏi OG: digest 3
# cột này từ code TRƯỚC khi bỏ và SAU khi bỏ trùng khớp tuyệt đối, tức việc
# bỏ SL/TP không đổi tín hiệu/entry. Đổi digest = đổi tín hiệu/entry -> phải
# là thay đổi CÓ CHỦ ĐÍCH.
GOLDEN = {
    "combo": {
        "tf": "H1",
        "n_buy": 2,
        "n_sell": 3,
        "first_buy_idx": 219,
        "first_sell_idx": 24,
        "digest": "1fb2c847f6ccae5da7fb0e847900d876742486ee59a29ed657d0134a01e4a489",
    },
    "ma_cross": {
        "tf": "M30",
        "n_buy": 7,
        "n_sell": 6,
        "first_buy_idx": 57,
        "first_sell_idx": 86,
        "digest": "d456518f582604979c92887d6faf7ee321845e9d1c76c4af04c40b7ffcf6d6b8",
    },
}


@pytest.mark.parametrize("strategy", sorted(GOLDEN))
def test_pipeline_golden(bars: pd.DataFrame, strategy: str) -> None:
    want = GOLDEN[strategy]
    df = run_strategy(strategy, symbol="US30", tf=want["tf"], bars=bars)
    sig = df["signal"].astype(int)

    assert int((sig == 1).sum()) == want["n_buy"]
    assert int((sig == -1).sum()) == want["n_sell"]
    assert int(sig[sig == 1].index[0]) == want["first_buy_idx"]
    assert int(sig[sig == -1].index[0]) == want["first_sell_idx"]
    assert _pipeline_digest(df) == want["digest"]


def test_pipeline_khong_sua_input_tai_cho(bars: pd.DataFrame) -> None:
    before = bars.copy()
    run_strategy("combo", symbol="US30", tf="H1", bars=bars)
    pd.testing.assert_frame_equal(bars, before)


# =============================================================================
# 2. Trend filter — mặc định TẮT, bật lên thì phải tham chiếu trend thật.
# =============================================================================


def test_mac_dinh_trend_filter_phai_tat() -> None:
    """Mặc định vận hành: cả 2 chiến lược đều TẮT trend filter, nên luồng live
    chạy 1 timeframe y như trước. Nếu ai commit og_config.yaml với true, test
    này đỏ — vì đó là thay đổi hành vi production, phải cố ý.
    """
    for key in ("combo", "ma_cross"):
        enabled, trend_tf = trend_reference_for(key)
        assert enabled is False, f"{key}: trend filter đang BẬT trong og_config.yaml"
        assert trend_tf in COMBO_TREND_TIMEFRAMES  # H1..H4


def test_trend_tat_thi_signal_bang_raw_signal(bars: pd.DataFrame) -> None:
    for strategy, tf in (("combo", "H1"), ("ma_cross", "M30")):
        df = run_strategy(strategy, symbol="US30", tf=tf, bars=bars)
        pd.testing.assert_series_equal(
            df["signal"].astype(int), df["raw_signal"].astype(int), check_names=False
        )
        assert set(df["trend_filter_status"]) == {"disabled"}
        # risk_reward đã bỏ hẳn: không có công thức nào tính nó.
        assert "risk_reward" not in df.columns


# (strategy, entry_tf, trend_tf, bước lấy mẫu trend, raw, còn lại sau khi lọc)
# Số "còn lại" tính từ pipeline thật — khoá lại để đổi logic lọc là test đỏ.
TREND_CASES = [
    ("ma_cross", "M30", "H2", 2, 13, 12),
    ("ma_cross", "M30", "H3", 3, 13, 3),
    ("ma_cross", "M30", "H4", 4, 13, 0),
    ("combo", "H1", "H4", 4, 5, 0),
]


@pytest.mark.parametrize("strategy,entry_tf,trend_tf,step,n_raw,n_kept", TREND_CASES)
def test_trend_bat_thi_chi_giu_tin_hieu_cung_chieu(
    bars: pd.DataFrame,
    strategy: str,
    entry_tf: str,
    trend_tf: str,
    step: int,
    n_raw: int,
    n_kept: int,
) -> None:
    """Chạy đường 2 timeframe thật (entry + trend dựng từ cùng bộ nến).

    Khoá 4 tính chất quan trọng nhất của bộ lọc:
      1. Mọi tín hiệu còn lại phải CÙNG CHIỀU trend_bias.
      2. Không lookahead: trend bar dùng để lọc phải ĐÃ ĐÓNG trước entry bar.
      3. Lọc chỉ làm GIẢM số tín hiệu, không bao giờ tạo thêm.
      4. Tín hiệu bị lọc phải được ghi nhãn rõ, không âm thầm biến mất.
    """
    trend_bars = bars.iloc[::step].reset_index(drop=True)  # giả lập khung lớn hơn
    off = run_strategy(strategy, symbol="US30", tf=entry_tf, bars=bars)
    on = run_strategy_with_trend_reference(
        strategy,
        symbol="US30",
        entry_tf=entry_tf,
        entry_bars=bars,
        trend_tf=trend_tf,
        trend_bars=trend_bars,
    )

    assert {"trend_bias", "trend_ai_knn", "trend_ai_avg", "trend_close_time"} <= set(on.columns)

    # Bộ lọc không được đụng tới raw_signal — chỉ quyết định giữ hay bỏ.
    assert int((on["raw_signal"].astype(int) != 0).sum()) == n_raw
    assert int((off["signal"].astype(int) != 0).sum()) == n_raw

    kept = on[on["signal"].astype(int) != 0]
    assert len(kept) == n_kept
    assert len(kept) <= n_raw
    assert (kept["signal"].astype(int) == kept["trend_bias"].astype(int)).all()
    assert (kept["trend_close_time"] <= kept["bartime"]).all()
    assert set(on.loc[on["raw_signal"].astype(int) != 0, "trend_filter_status"]) <= {
        "aligned",
        "filtered",
        "neutral_trend",
    }
    if n_kept:
        assert set(kept["signal_reason"].str.endswith("; aligned with trend reference")) == {True}


def test_trend_bat_it_nhat_mot_cap_con_giu_tin_hieu(bars: pd.DataFrame) -> None:
    """Chốt rằng bộ lọc KHÔNG phải là "chặn hết" — có cặp thật giữ lại tín
    hiệu. Nếu một thay đổi nào đó làm mọi tín hiệu bị lọc sạch, test này đỏ.
    """
    assert any(n_kept > 0 for *_, n_kept in TREND_CASES)


def test_trend_bat_ma_thieu_cot_trend_thi_raise(bars: pd.DataFrame) -> None:
    """run_strategy() (1 timeframe) không bao giờ sinh trend_bias. Bật trend
    filter trên đường đó phải RAISE rõ ràng chứ không âm thầm bỏ qua bộ lọc —
    live_worker dựa vào điều này để không publish tín hiệu chưa lọc.
    """
    with pytest.raises(ValueError, match="trend_bias"):
        run_strategy(
            "combo", symbol="US30", tf="H1", bars=bars, overrides={"TREND_FILTER_ENABLED": True}
        )


def test_trend_type_chi_chap_nhan_knn() -> None:
    with pytest.raises(ValueError, match="TREND_TYPE must be 'knn'"):
        normalize_combo_params({"TREND_TYPE": "ema"}, "US30", "H1")


def test_trend_tf_ngoai_danh_sach_bi_chan() -> None:
    with pytest.raises(ValueError, match="TREND_TF must be one of"):
        normalize_combo_params({"TREND_TF": "M5"}, "US30", "H1")


def test_strategies_chi_con_combo_va_ma_cross() -> None:
    assert sorted(STRATEGIES) == ["combo", "ma_cross"]


# =============================================================================
# 3. signal_reason -> field `comment` của DB1, OF đọc nguyên văn.
# =============================================================================

REASONS = {
    ("combo", 1): "bullish candle crossing above MA, MACD histogram > 0",
    ("combo", -1): "bearish candle crossing below MA, MACD histogram < 0",
    ("ma_cross", 1): "fast SMA crossed above slow SMA; MACD histogram > 0",
    ("ma_cross", -1): "fast SMA crossed below slow SMA; MACD histogram < 0",
}


@pytest.mark.parametrize("strategy,tf", [("combo", "H1"), ("ma_cross", "M30")])
def test_signal_reason_dung_nguyen_van(bars: pd.DataFrame, strategy: str, tf: str) -> None:
    df = run_strategy(strategy, symbol="US30", tf=tf, bars=bars)
    sig = df["signal"].astype(int)
    for side in (1, -1):
        rows = df[sig == side]
        assert not rows.empty, f"{strategy} không sinh được tín hiệu {side}"
        assert set(rows["signal_reason"]) == {REASONS[(strategy, side)]}
    # Dòng không có tín hiệu thì reason phải rỗng.
    assert set(df[sig == 0]["signal_reason"]) <= {""}


# =============================================================================
# 4. Entry. OG không còn SL/TP (OF tự tính từ KSL/KTP của họ, 2026-10-04).
# =============================================================================


@pytest.mark.parametrize("strategy,tf", [("combo", "H1"), ("ma_cross", "M30")])
def test_og_khong_con_cot_sl_tp(bars: pd.DataFrame, strategy: str, tf: str) -> None:
    df = run_strategy(strategy, symbol="US30", tf=tf, bars=bars)
    assert not {"sl_price", "tp_price", "ksl", "ktp"} & set(df.columns)


def test_combo_entry_la_breakout_ngoai_bar(bars: pd.DataFrame) -> None:
    """Combo là lệnh STOP: entry BUY phải nằm TRÊN high, SELL DƯỚI low."""
    df = run_strategy("combo", symbol="US30", tf="H1", bars=bars)
    sig = df["signal"].astype(int)
    x = normalize_combo_params(None, "US30", "H1")["X"]
    buy, sell = df[sig == 1], df[sig == -1]
    np.testing.assert_allclose(buy["entry_price"], buy["high"] + x, rtol=1e-12)
    np.testing.assert_allclose(sell["entry_price"], sell["low"] - x, rtol=1e-12)


def test_ma_cross_entry_la_close(bars: pd.DataFrame) -> None:
    """MA Cross là lệnh MARKET: entry = close của bar tín hiệu."""
    df = run_strategy("ma_cross", symbol="US30", tf="M30", bars=bars)
    has = df[df["signal"].astype(int) != 0]
    np.testing.assert_allclose(has["entry_price"], has["close"], rtol=1e-12)


# =============================================================================
# 5. ATR/RMA phải seed bằng SMA(period) — khớp ta.atr/ta.rma của TradingView.
#    KHÔNG được thay bằng ewm(adjust=False) thuần (lệch 2.5-4% ở bar đầu).
# =============================================================================


def test_atr_seed_bang_sma_khong_phai_ewm(bars: pd.DataFrame) -> None:
    period = 14
    tr = pd.concat(
        [
            bars["high"] - bars["low"],
            (bars["high"] - bars["close"].shift(1)).abs(),
            (bars["low"] - bars["close"].shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)

    got = atr(bars, period)
    first = got.first_valid_index()
    # Giá trị đầu tiên xuất hiện ở bar thứ `period` (index period-1), và bằng
    # đúng SMA(period) của True Range — KHÔNG phải ewm seed bằng 1 điểm.
    assert first == period - 1
    assert got.loc[first] == pytest.approx(tr.iloc[:period].mean(), rel=1e-12)

    # Nếu ai đổi sang ewm(adjust=False) thuần, bar đầu sẽ lệch hẳn (đã đo:
    # period=14 lệch ~4.2%). Test này là cái chặn.
    naive = tr.ewm(alpha=1 / period, adjust=False).mean()
    assert got.loc[first] != pytest.approx(naive.loc[first], rel=1e-3)
    assert abs(got.loc[first] - naive.loc[first]) / got.loc[first] > 0.01


def test_rma_seed_bang_sma(bars: pd.DataFrame) -> None:
    period = 50
    got = rma(bars["close"], period)
    first = got.first_valid_index()
    assert got.loc[first] == pytest.approx(sma(bars["close"], period).loc[first], rel=1e-12)


# =============================================================================
# 7. Hợp đồng key Redis DB0/DB1.
# =============================================================================


def test_db0_key_symbol_truoc_timeframe() -> None:
    assert pair_list_key("L_CANDLE", "us30", "m5") == "L_CANDLE_US30_M5"
    assert candle_key("L_CANDLE_US30_M5", "2026-09-14 03:00:00") == (
        "L_CANDLE_US30_M5:2026-09-14 03:00:00"
    )


def test_parse_pair_key_loai_key_hash() -> None:
    assert parse_pair_key("L_CANDLE_US30_M5", "L_CANDLE") == ("US30", "M5")
    # key HASH có dấu ':' -> không phải pair (mốc giờ cũng chứa ':')
    assert parse_pair_key("L_CANDLE_US30_M5:2026-09-14 03:00:00", "L_CANDLE") is None
    assert parse_pair_key("OTHER_US30_M5", "L_CANDLE") is None
    assert parse_pair_key("L_CANDLE_US30", "L_CANDLE") is None


def test_db1_key_og_va_channel_theo_strategy_symbol_tf() -> None:
    """3 chiều, STRATEGY đứng đầu (chốt 2026-10-04): key DB1
    L_OG_{STRATEGY}_{SYMBOL}_{TF} và tên kênh Pub/Sub
    L_CHANNEL_{STRATEGY}_{SYMBOL}_{TF} (kênh chỉ là đường truyền, không lưu key).
    """
    key = og_list_key("L_OG", "combo", "us30", "h1")
    assert key == "L_OG_COMBO_US30_H1"
    assert stamp_hash_key(key, "2026-09-22 13:00:00") == "L_OG_COMBO_US30_H1:2026-09-22 13:00:00"
    assert channel_name("L_CHANNEL", "combo", "us30", "h1") == "L_CHANNEL_COMBO_US30_H1"
    # ma_cross -> MACROSS (bỏ '_' để token nào cũng không chứa ký tự phân cách).
    assert og_list_key("L_OG", "ma_cross", "US30", "M30") == "L_OG_MACROSS_US30_M30"
    assert channel_name("L_CHANNEL", "ma_cross", "US30", "M30") == "L_CHANNEL_MACROSS_US30_M30"
    assert strategy_token("MA_CROSS") == "MACROSS"
    assert strategy_token("combo") == "COMBO"


def test_redis_config_thieu_key_thi_raise() -> None:
    """7 key vận hành bắt buộc — thiếu phải raise rõ ràng, không fallback.

    input.event_channel KHÔNG còn trong danh sách này (gỡ 2026-09-23): OG
    không còn trigger qua dp:events:candles, trigger qua keyspace
    notification của chính Redis trên DB0 (xem event_listener.py) -- chỉ
    cần db/key_prefix/snapshot_bars/process_on_startup ở input.
    """
    full = {
        "host": "127.0.0.1",
        "password": "x",
        "input": {
            "db": 0,
            "key_prefix": "L_CANDLE",
            "snapshot_bars": 500,
            "process_on_startup": True,
        },
        "output": {"db": 1, "key_prefix": "L_OG", "channel_prefix": "L_CHANNEL"},
    }
    assert normalize_redis_config(full)["input"]["reconcile_interval_seconds"] == 1800
    assert normalize_redis_config(full)["output"]["retention_seconds"] == 604800
    # input.event_channel thật sự không còn tồn tại trong config đã chuẩn hoá.
    assert "event_channel" not in normalize_redis_config(full)["input"]

    for section, key in (
        ("input", "db"),
        ("input", "key_prefix"),
        ("input", "snapshot_bars"),
        ("input", "process_on_startup"),
        ("output", "db"),
        ("output", "key_prefix"),
        ("output", "channel_prefix"),
    ):
        broken = {**full, section: {k: v for k, v in full[section].items() if k != key}}
        with pytest.raises(KeyError, match=f"redis.{section}.{key}"):
            normalize_redis_config(broken)


# =============================================================================
# 8. Payload DB1 gửi cho OF.
# =============================================================================


def _one_signal_row(strategy: str, tf: str, bars: pd.DataFrame) -> pd.Series:
    df = run_strategy(strategy, symbol="US30", tf=tf, bars=bars)
    return df[df["signal"].astype(int) != 0].iloc[-1]


def test_hash_fields_combo_la_lenh_stop(bars: pd.DataFrame) -> None:
    row = _one_signal_row("combo", "H1", bars)
    vf, vu = signal_window("combo", "H1", row["bartime"], {"mode": "next_bar", "valid_bars": 3})
    signal_id, stamp, payload = build_signal(
        strategy="combo", timeframe="H1", symbol="US30", row=row, valid_from=vf, valid_until=vu
    )
    fields = to_hash_fields("combo", payload)

    assert fields["orderType"] == "STOP"
    assert "stopPrice" in fields  # STOP phải có giá kích hoạt
    assert fields["tradeSide"] in {"BUY", "SELL"}
    assert fields["symbol"] == "US30"
    assert fields["timeframe"] == "H1"
    assert fields["strategy"] == "combo"
    assert fields["clientOrderId"] == signal_id
    assert stamp == pd.Timestamp(row["bartime"]).strftime("%Y-%m-%d %H:%M:%S")
    # expirationTimestamp là epoch GIÂY (cTrader cần int64), không phải ISO.
    assert fields["expirationTimestamp"].isdigit()
    assert int(fields["expirationTimestamp"]) == int(vu.timestamp())
    # SL/TP không còn do OG publish -- OF tự tính.
    assert not {"stopLoss", "takeProfit", "ksl", "ktp"} & set(fields)
    # OG chỉ có 1 nguồn text -> chỉ 'comment', không có 'label'.
    assert "label" not in fields
    assert "risk_reward" not in fields


def test_hash_fields_ma_cross_la_lenh_market(bars: pd.DataFrame) -> None:
    row = _one_signal_row("ma_cross", "M30", bars)
    vf, vu = signal_window(
        "ma_cross", "M30", row["bartime"], {"mode": "seconds_after_bar_close", "seconds": 180}
    )
    _, _, payload = build_signal(
        strategy="ma_cross", timeframe="M30", symbol="US30", row=row, valid_from=vf, valid_until=vu
    )
    fields = to_hash_fields("ma_cross", payload)

    assert fields["orderType"] == "MARKET"
    assert "stopPrice" not in fields  # MARKET không có giá kích hoạt
    assert "atr" in fields  # OF tự tính volume từ atr
    assert "valid_from" in fields


def test_build_signal_tu_choi_dong_khong_co_tin_hieu(bars: pd.DataFrame) -> None:
    df = run_strategy("combo", symbol="US30", tf="H1", bars=bars)
    row = df[df["signal"].astype(int) == 0].iloc[-1]
    now = pd.Timestamp("2026-01-01", tz="UTC")
    with pytest.raises(ValueError, match="BUY/SELL"):
        build_signal(
            strategy="combo",
            timeframe="H1",
            symbol="US30",
            row=row,
            valid_from=now,
            valid_until=now,
        )


class _RecordingRedis:
    """Ghi lại đúng lời gọi EVAL, không nối Redis thật."""

    def __init__(self, created: int = 1) -> None:
        self.created = created
        self.calls: list[tuple] = []

    def eval(self, script, numkeys, *args):
        self.calls.append((script, numkeys, args))
        return self.created


def test_publish_ghi_og_roi_publish_day_du(bars: pd.DataFrame) -> None:
    """1 EVAL: 2 key (OG hash/list), PUBLISH lên kênh L_CHANNEL_..., message là
    JSON đầy đủ đúng bằng các field của Hash. Kênh không lưu key nào.
    """
    import json

    row = _one_signal_row("combo", "H1", bars)
    vf, vu = signal_window("combo", "H1", row["bartime"], {"mode": "next_bar", "valid_bars": 3})
    _, stamp, payload = build_signal(
        strategy="combo", timeframe="H1", symbol="US30", row=row, valid_from=vf, valid_until=vu
    )
    fields = to_hash_fields("combo", payload)
    client = _RecordingRedis()

    assert publish_signal(
        client, key_prefix="L_OG", channel_prefix="L_CHANNEL", symbol="US30",
        timeframe="H1", strategy="combo", stamp=stamp, hash_fields=fields,
        retention_seconds=604800, max_list_entries=500,
    ) is True

    (script, numkeys, args), = client.calls
    assert numkeys == 2  # chỉ OG hash + OG list, không có key nào cho kênh
    assert args[:2] == (
        f"L_OG_COMBO_US30_H1:{stamp}",
        "L_OG_COMBO_US30_H1",
    )
    assert "KEYS[3]" not in script
    argv = args[2:]
    assert argv[0] == stamp
    assert argv[3] == "L_CHANNEL_COMBO_US30_H1"  # kênh Pub/Sub
    assert json.loads(argv[4]) == fields  # message = đầy đủ thông tin
    assert dict(zip(argv[5::2], argv[6::2])) == fields  # Hash = cùng nội dung
    # Trùng lặp chỉ chặn ở OG hash; PUBLISH nằm sau các lệnh ghi.
    assert script.index("EXISTS") < script.index("PUBLISH")
    assert signal_message(fields) == argv[4]


def test_publish_ma_cross_dung_token_macross_nhung_giu_field_ma_cross(bars: pd.DataFrame) -> None:
    """Chỉ TÊN key/kênh đổi sang MACROSS; field strategy và clientOrderId trong
    message/Hash vẫn là tên thật 'ma_cross' (chốt 2026-10-05).
    """
    import json

    row = _one_signal_row("ma_cross", "M30", bars)
    vf, vu = signal_window(
        "ma_cross", "M30", row["bartime"], {"mode": "seconds_after_bar_close", "seconds": 180}
    )
    _, stamp, payload = build_signal(
        strategy="ma_cross", timeframe="M30", symbol="US30", row=row, valid_from=vf, valid_until=vu
    )
    fields = to_hash_fields("ma_cross", payload)
    client = _RecordingRedis()
    publish_signal(
        client, key_prefix="L_OG", channel_prefix="L_CHANNEL", symbol="US30",
        timeframe="M30", strategy="ma_cross", stamp=stamp, hash_fields=fields,
        retention_seconds=604800, max_list_entries=500,
    )
    (_, _, args), = client.calls
    assert args[:2] == (
        f"L_OG_MACROSS_US30_M30:{stamp}",
        "L_OG_MACROSS_US30_M30",
    )
    assert args[2 + 3] == "L_CHANNEL_MACROSS_US30_M30"  # kênh Pub/Sub
    message = json.loads(args[2 + 4])
    assert message["strategy"] == "ma_cross"
    assert message["clientOrderId"].startswith("ma_cross:M30:US30:")


def test_publish_trung_thi_bao_false() -> None:
    client = _RecordingRedis(created=0)
    assert publish_signal(
        client, key_prefix="L_OG", channel_prefix="L_CHANNEL", symbol="US30",
        timeframe="H1", strategy="combo", stamp="2026-09-22 13:00:00",
        hash_fields={"symbol": "US30"}, retention_seconds=1, max_list_entries=1,
    ) is False


# =============================================================================
# 9. Cửa sổ hiệu lực + định tuyến timeframe.
# =============================================================================


def test_timeframe_duration() -> None:
    assert timeframe_duration("M30") == pd.Timedelta(minutes=30)
    assert timeframe_duration("H4") == pd.Timedelta(hours=4)
    for bad in ("X5", "M0", "M-1", ""):
        with pytest.raises(ValueError):
            timeframe_duration(bad)


def test_signal_window_hai_che_do() -> None:
    bartime = pd.Timestamp("2026-09-22 13:00:00")

    vf, vu = signal_window("combo", "H4", bartime, {"mode": "next_bar", "valid_bars": 3})
    # valid_from = lúc bar ĐÓNG, không phải lúc bar mở.
    assert vf == pd.Timestamp("2026-09-22 17:00:00", tz="UTC")
    assert vu == pd.Timestamp("2026-09-23 05:00:00", tz="UTC")

    vf, vu = signal_window(
        "ma_cross", "M30", bartime, {"mode": "seconds_after_bar_close", "seconds": 180}
    )
    assert vf == pd.Timestamp("2026-09-22 13:30:00", tz="UTC")
    assert vu == pd.Timestamp("2026-09-22 13:33:00", tz="UTC")

    with pytest.raises(ValueError, match="Unsupported validity mode"):
        signal_window("combo", "H1", bartime, {"mode": "khong_ton_tai"})


def test_dinh_tuyen_timeframe() -> None:
    enabled = ["combo", "ma_cross"]
    assert strategies_for_timeframe(enabled, "H4") == ["combo"]
    assert strategies_for_timeframe(enabled, "M30") == ["ma_cross"]
    assert strategies_for_timeframe(enabled, "M5") == []  # không chiến lược nào


def test_ma_cross_tu_choi_timeframe_ngoai_danh_sach(bars: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="supports only these timeframes"):
        run_strategy("ma_cross", symbol="US30", tf="H1", bars=bars)


# =============================================================================
# 9b. live_worker chọn đúng đường chạy theo công tắc trend — hermetic, client giả.
# =============================================================================


class _FakeInput:
    """Trả đúng bộ nến đã dựng sẵn cho từng timeframe, ghi lại TF nào bị đọc."""

    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self.frames = frames
        self.doc: list[str] = []

    def read(self, *, symbol: str, timeframe: str, key_prefix: str, snapshot_bars: int):
        self.doc.append(timeframe)
        return self.frames.get(timeframe, pd.DataFrame())


class _FakeOutput:
    """Không nối Redis. eval() luôn báo 'đã tồn tại' -> không bao giờ publish."""

    def __init__(self) -> None:
        self.calls = 0

    def eval(self, *args, **kwargs):
        self.calls += 1
        return 0


def _worker_config() -> dict:
    return {
        "redis": {
            "input": {"key_prefix": "L_CANDLE", "snapshot_bars": 500},
            "output": {
                "key_prefix": "L_OG",
                "channel_prefix": "L_CHANNEL",
                "retention_seconds": 604800,
                "retention_max_entries": 500,
            },
        },
        "live": {
            "enabled_strategies": ["combo"],
            "discord": {"enabled": False},
            "signal_validity": {"combo": {"mode": "next_bar", "valid_bars": 3}},
        },
    }


def test_worker_trend_tat_chi_doc_mot_timeframe(
    bars: pd.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    from order_gateway.src.redis_io import live_worker as lw

    fake_in = _FakeInput({"H1": bars})
    monkeypatch.setattr(lw, "read_candles_from_redis", lambda client, **kw: fake_in.read(**kw))
    monkeypatch.setattr(lw, "normalize_app_config", lambda cfg: cfg)
    monkeypatch.setattr(lw, "trend_reference_for", lambda key: (False, "H4"))

    lw.process_candle_update(
        fake_in,
        _FakeOutput(),
        timeframe="H1",
        symbol="US30",
        config=_worker_config(),
        now=pd.Timestamp("2026-01-17 16:00:00", tz="UTC"),
    )
    assert fake_in.doc == ["H1"]


def test_worker_trend_bat_doc_them_khung_lon(
    bars: pd.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bật trend filter thì worker PHẢI đọc thêm nến trend timeframe, và phải
    chạy qua đường 2 timeframe (run_strategy_with_trend_reference).
    """
    from order_gateway.src.redis_io import live_worker as lw

    fake_in = _FakeInput({"H1": bars, "H4": bars.iloc[::4].reset_index(drop=True)})
    dung_duong_trend: list[str] = []

    def spy_trend(key, **kwargs):
        dung_duong_trend.append(kwargs["trend_tf"])
        return run_strategy_with_trend_reference(key, **kwargs)

    monkeypatch.setattr(lw, "read_candles_from_redis", lambda client, **kw: fake_in.read(**kw))
    monkeypatch.setattr(lw, "normalize_app_config", lambda cfg: cfg)
    monkeypatch.setattr(lw, "trend_reference_for", lambda key: (True, "H4"))

    lw.process_candle_update(
        fake_in,
        _FakeOutput(),
        timeframe="H1",
        symbol="US30",
        config=_worker_config(),
        now=pd.Timestamp("2026-01-17 16:00:00", tz="UTC"),
        trend_runner=spy_trend,
    )
    assert fake_in.doc == ["H1", "H4"]
    assert dung_duong_trend == ["H4"]


def test_worker_thieu_nen_trend_thi_khong_publish(
    bars: pd.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DB0 không có nến khung lớn cho symbol này -> BỎ QUA, tuyệt đối không
    publish tín hiệu chưa lọc (im lặng bỏ qua bộ lọc là kịch bản tệ nhất: OF
    sẽ đặt lệnh mà operator tin là đã được lọc theo trend).
    """
    from order_gateway.src.redis_io import live_worker as lw

    fake_in = _FakeInput({"H1": bars})  # không có H4
    fake_out = _FakeOutput()
    monkeypatch.setattr(lw, "read_candles_from_redis", lambda client, **kw: fake_in.read(**kw))
    monkeypatch.setattr(lw, "normalize_app_config", lambda cfg: cfg)
    monkeypatch.setattr(lw, "trend_reference_for", lambda key: (True, "H4"))

    published = lw.process_candle_update(
        fake_in,
        fake_out,
        timeframe="H1",
        symbol="US30",
        config=_worker_config(),
        now=pd.Timestamp("2026-01-17 16:00:00", tz="UTC"),
    )
    assert fake_in.doc == ["H1", "H4"]
    assert published == 0
    assert fake_out.calls == 0  # không hề gọi tới Redis DB1


def test_get_strategy_khong_phan_biet_hoa_thuong() -> None:
    assert get_strategy("COMBO").key == "combo"
    assert get_strategy(" ma_cross ").key == "ma_cross"
    # ema_cross đã gỡ khỏi order_gateway có chủ đích.
    with pytest.raises(KeyError, match="Unknown strategy"):
        get_strategy("ema_cross")


# =============================================================================
# 10. Validate tham số.
# =============================================================================


def test_ma_cross_chan_fast_lon_hon_slow() -> None:
    with pytest.raises(ValueError, match="FAST_MA must be smaller"):
        normalize_ma_cross_params({"FAST_MA": 50, "SLOW_MA": 20}, "US30", "M30")
    with pytest.raises(ValueError, match="MACD_FAST must be smaller"):
        normalize_ma_cross_params({"MACD_FAST": 30, "MACD_SLOW": 10}, "US30", "M30")


def test_tham_so_bi_kep_vao_khoang_an_toan() -> None:
    p = normalize_combo_params({"MA_PERIOD": 99999, "ATR_PERIOD": -5}, "US30", "H1")
    assert p["MA_PERIOD"] == 500  # max
    assert p["ATR_PERIOD"] == 2  # min


def test_session_hours_ngoai_pham_vi_bi_chan() -> None:
    with pytest.raises(ValueError, match="0..23"):
        normalize_combo_params({"SESSION_HOURS_UTC": "9,99"}, "US30", "H1")


def test_symbol_khong_co_trong_bang_x_thi_x_bang_0() -> None:
    assert normalize_combo_params(None, "ZZZ999", "H1")["X"] == 0.0
    assert normalize_combo_params(None, "US30", "H1")["X"] == 10.0


# =============================================================================
# 11. listen_for_candle_hash_events -- trigger dựa keyspace notification (thay
#     dp:events:candles, chốt 2026-09-23). Hermetic bằng pubsub/client giả.
# =============================================================================


class _StopTest(Exception):
    """Thoát vòng lặp while True vô hạn của listener sau khi đã quan sát đủ."""


class _FakePubSub:
    def __init__(self, messages: list[dict]) -> None:
        self._messages = list(messages)
        self.psubscribed: list[str] = []
        self.closed = False

    def psubscribe(self, pattern: str) -> None:
        self.psubscribed.append(pattern)

    def get_message(self, **_kwargs: object) -> dict | None:
        return self._messages.pop(0) if self._messages else None

    def close(self) -> None:
        self.closed = True


class _FakeRedisClient:
    def __init__(self, messages: list[dict]) -> None:
        self._pubsub = _FakePubSub(messages)

    def pubsub(self):
        return self._pubsub


def test_listen_for_candle_hash_events_chi_dispatch_hset_hop_le() -> None:
    """Khoá đúng 4 quy tắc lọc của listener mới:
      1. Chỉ payload=='hset' mới dispatch -- 'del' (LPOP evict) bị bỏ qua.
      2. Channel sai db-prefix (không khớp __keyspace@{db}__:) bị bỏ qua.
      3. Channel parse ra list-key không hợp lệ (parse_pair_key trả None)
         bị bỏ qua.
      4. Channel hợp lệ -> on_update(timeframe, symbol) đúng thứ tự tham số.
    """
    from order_gateway.src.redis_io.event_listener import listen_for_candle_hash_events

    messages = [
        {"type": "psubscribe"},
        # 1. Sai lệnh (del = LPOP evict, không phải nến đổi giá) -> bỏ qua.
        {
            "type": "pmessage",
            "channel": "__keyspace@0__:L_CANDLE_US30_M5:2026-01-01 00:00:00",
            "data": "del",
        },
        # 2. Sai db trong channel prefix (đang nghe db=0) -> bỏ qua.
        {
            "type": "pmessage",
            "channel": "__keyspace@1__:L_CANDLE_US30_M5:2026-01-01 00:00:00",
            "data": "hset",
        },
        # 3. List-key không parse được (thiếu timeframe sau prefix) -> bỏ qua.
        {
            "type": "pmessage",
            "channel": "__keyspace@0__:L_CANDLE_:2026-01-01 00:00:00",
            "data": "hset",
        },
        # 4a. Hợp lệ -> dispatch.
        {
            "type": "pmessage",
            "channel": "__keyspace@0__:L_CANDLE_US30_M5:2026-01-01 00:00:00",
            "data": "hset",
        },
        # 4b. Hợp lệ, symbol/timeframe khác -> dispatch, dừng test ở đây.
        {
            "type": "pmessage",
            "channel": "__keyspace@0__:L_CANDLE_BTCUSD_H4:2026-01-02 00:00:00",
            "data": "hset",
        },
    ]
    client = _FakeRedisClient(messages)
    dispatched: list[tuple[str, str]] = []

    def on_update(timeframe: str, symbol: str) -> None:
        dispatched.append((timeframe, symbol))
        if len(dispatched) == 2:
            raise _StopTest

    started = []
    with pytest.raises(_StopTest):
        listen_for_candle_hash_events(
            client,
            db=0,
            key_prefix="L_CANDLE",
            on_update=on_update,
            on_started=lambda: started.append(True),
        )

    assert started == [True]
    assert dispatched == [("M5", "US30"), ("H4", "BTCUSD")]
    assert client._pubsub.psubscribed == ["__keyspace@0__:L_CANDLE_*"]
    assert client._pubsub.closed  # finally: pubsub.close() luôn chạy


def test_listen_for_candle_hash_events_goi_reconcile_dung_dieu_kien(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """on_started không bắt buộc (None vẫn chạy được), và tới hạn reconcile
    thì on_reconcile phải được gọi -- không phụ thuộc có message nào tới hay
    không. Giả lập đồng hồ để không phải chờ thật 1 giây (get_message giả
    không block, vòng lặp sẽ busy-spin tới khi time.monotonic() vượt hạn).
    """
    import itertools

    import order_gateway.src.redis_io.event_listener as event_listener_module

    # Lần gọi đầu (tính next_reconcile) trả 0.0; mọi lần sau trả 999.0 --
    # vượt hạn ngay từ vòng lặp đầu tiên, không cần chờ thật 1 giây.
    ticks = itertools.chain([0.0], itertools.repeat(999.0))
    monkeypatch.setattr(event_listener_module.time, "monotonic", lambda: next(ticks))

    messages = [{"type": "psubscribe"}, {"type": "pmessage", "channel": "x", "data": "hset"}]
    client = _FakeRedisClient(messages)

    def on_update(timeframe: str, symbol: str) -> None:
        raise _StopTest  # sẽ không tới đây vì channel "x" không khớp prefix

    def on_reconcile() -> None:
        raise _StopTest

    with pytest.raises(_StopTest):
        event_listener_module.listen_for_candle_hash_events(
            client,
            db=0,
            key_prefix="L_CANDLE",
            on_update=on_update,
            on_started=None,
            reconcile_interval_seconds=1,
            on_reconcile=on_reconcile,
        )
