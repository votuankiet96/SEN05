"""Test nguồn nến thêm và bảng chính sách (POLICY) của các khối trend của DE — hermetic, KHÔNG nối Redis.

Mỗi khối trend cho khung trend nào, có dời tín hiệu không, cần bao nhiêu nến: bảng POLICY ở rules/trend_tf (khung trend của khối cần khung tham chiếu riêng do kế hoạch của symbol khai,
xem test_config_guards). Đường xử lý một sự kiện nằm ở test_lane.py (runner, publisher); cấu hình Redis và kết nối ở test_config_guards.py / test_data_engine.py / test_signal_publisher.py;
config mẫu phải khai đủ nguồn nến, nguồn trend và kế hoạch.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from core_engine.src.configuration import OPERATING_SYMBOLS, get_strategy
from core_engine.src.rules import trend_tf

EXAMPLE = yaml.safe_load((Path(__file__).resolve().parents[1] / "de_config.example.yaml").read_text(encoding="utf-8"))


def test_config_mau_khai_du_nguon_nen_nguon_trend_va_bang_khung_trend_cho_15_symbol_de() -> None:
    trend_outputs = EXAMPLE["redis"]["trend_outputs"]
    assert list(trend_outputs) == ["knn", "breakout", "zigzag", "tstat", "cloud"]
    prefixes = [EXAMPLE["redis"]["output"]["key_prefix"], *(item["key_prefix"] for item in trend_outputs.values())]
    channels = [EXAMPLE["redis"]["output"]["channel_prefix"], *(item["channel_prefix"] for item in trend_outputs.values())]
    assert prefixes == ["L_DE", "L_DE_KNN", "L_DE_BREAKOUT", "L_DE_ZIGZAG", "L_DE_TSTAT", "L_DE_CLOUD"]
    assert channels == ["L_CHANNEL", "L_CHANNEL_KNN", "L_CHANNEL_BREAKOUT", "L_CHANNEL_ZIGZAG", "L_CHANNEL_TSTAT", "L_CHANNEL_CLOUD"]
    assert EXAMPLE["redis"]["input"]["symbols"] == ["US30", "US500", "US100", "GER40", "HK50", "JP225", "XAUUSD", "BTCUSD"]
    og8 = EXAMPLE["redis"]["input_sources"]["og8"]  # nguồn nến thêm: Redis DF của OG8 nuôi 7 symbol mà Core AEN không có
    assert og8["symbols"] == ["EURUSD", "GBPUSD", "USDCAD", "USDJPY", "FR40", "SP35", "UK100"] and set(og8) == {"host", "port", "password", "db", "key_prefix", "symbols"}
    assert EXAMPLE["live"]["price_decimals"] == {"EURUSD": 5, "GBPUSD": 5, "USDCAD": 5, "USDJPY": 3}
    assert EXAMPLE["redis"]["input"]["timeframes"] == ["M10", "M20", "M30", "M45", "H1", "H2", "H3", "H4"]
    assert (EXAMPLE["redis"]["input"]["snapshot_bars"], EXAMPLE["redis"]["input"]["warmup_bars"]) == (500, 300)  # ghi 500 hàng, tính thêm 300 nến ấm: 800 <= 1200 nến mỗi LIST nguồn giữ
    assert (EXAMPLE["redis"]["setup"]["window_bars"], EXAMPLE["redis"]["setup"]["retention_max_entries"]) == (500, 500)  # db0/db1: mỗi danh sách chỉ giữ 500 nến, nến mới vào thì nến cũ nhất ra
    assert EXAMPLE["redis"]["output"]["retention_seconds"] == 604800  # db2: mỗi tín hiệu sống TỐI ĐA 7 ngày
    assert "symbols" not in EXAMPLE["strategies"]["combo"]  # danh sách symbol chỉ khai ở redis.input / redis.input_sources, không khai lại ở chiến lược
    assert (EXAMPLE["redis"]["output"]["db"], EXAMPLE["redis"]["setup"]["db"], EXAMPLE["redis"]["signal_inputs"]["db"]) == (2, 0, 1)  # tín hiệu db2, TRIGGER/CONTEXT db0, TREND db1
    assert "plans" not in EXAMPLE and EXAMPLE["context"] == {"macd_fast": 5, "macd_slow": 25, "macd_signal": 5, "ma_period": 20}
    every_symbol = set(EXAMPLE["redis"]["input"]["symbols"]) | set(og8["symbols"])
    for key in ("combo", "ma_cross", "macd_break", "ema_reach"):  # bảng của we: đủ 15 symbol x 8 khung vào lệnh
        table = EXAMPLE["strategies"][key]["trend_tf_by_symbol"]
        assert set(table) == every_symbol and all(set(row) == set(EXAMPLE["redis"]["input"]["timeframes"]) for row in table.values()), key
    for key in ("combo", "ma_cross", "macd_break", "ema_reach"):
        cfg = EXAMPLE["strategies"][key]
        assert "trend_type" not in cfg and "default_trend_tf" not in cfg and cfg["trend_timeframes"] == ["H1", "H2", "H3", "H4"]
    assert EXAMPLE["strategies"]["macd_break"]["excluded_trend_methods"] == ["breakout"]  # chốt lại 2026-10-28: lọc trend ở mọi khung, trừ breakout
    assert not [key for key in EXAMPLE["strategies"]["ichimoku"] if key.startswith("trend")]  # ichimoku không có trend
    assert OPERATING_SYMBOLS == tuple(EXAMPLE["redis"]["input"]["symbols"]) + tuple(og8["symbols"])


def test_chinh_sach_cua_tung_khoi_trend_theo_bang_policy(monkeypatch) -> None:
    # breakout: chính khung vào lệnh, dời tín hiệu, không cần nến thêm
    for entry_tf in ("M30", "H1", "H4"):
        assert trend_tf.entry_based_trend_tf(("breakout",), entry_tf) == entry_tf
    assert trend_tf.uses_entry_timeframe(("breakout",)) and trend_tf.uses_first_aligned_per_run(("breakout",)) and trend_tf.max_trend_warmup_bars(("breakout",)) == 1200  # máy trạng thái giữ hướng tới cú phá kế: dùng cả nến nguồn giữ
    # cloud: chính khung vào lệnh nhưng không nhỏ hơn H1 (M10..M45 -> H1), dời tín hiệu, không cần nến thêm
    for entry_tf, want in (("M10", "H1"), ("M45", "H1"), ("H1", "H1"), ("H4", "H4")):
        assert trend_tf.entry_based_trend_tf(("cloud",), entry_tf) == want
    assert trend_tf.uses_entry_timeframe(("cloud",)) and trend_tf.uses_first_aligned_per_run(("cloud",)) and trend_tf.max_trend_warmup_bars(("cloud",)) == 0
    # knn: khung lớn do kế hoạch khai, không dời tín hiệu, không cần nến thêm
    assert not trend_tf.uses_entry_timeframe(("knn",)) and not trend_tf.uses_first_aligned_per_run(("knn",)) and trend_tf.max_trend_warmup_bars(("knn",)) == 0
    # zigzag: cùng kiểu khung với knn, không dời tín hiệu, khối đòi ~3000 nến khung trend (Redis chỉ giữ 1.200 mỗi danh sách)
    assert not trend_tf.uses_entry_timeframe(("zigzag",)) and not trend_tf.uses_first_aligned_per_run(("zigzag",)) and trend_tf.max_trend_warmup_bars(("zigzag",)) == 3000
    # tstat: khung do kế hoạch khai, CÓ dời tín hiệu
    assert not trend_tf.uses_entry_timeframe(("tstat",)) and trend_tf.uses_first_aligned_per_run(("tstat",))
    # bật lại chính sách dời cho knn (một dòng ở bảng POLICY) thì khối knn cũng dời
    monkeypatch.setitem(trend_tf.POLICY, "knn", trend_tf.MethodPolicy(first_aligned_per_run=True))
    assert trend_tf.uses_first_aligned_per_run(("knn",))


def test_khung_trend_cua_ke_hoach_suy_ra_dung_cho_khoi_cung_khung_va_lay_tu_ke_hoach_cho_khoi_khac() -> None:
    """Mọi (chiến lược dùng trend, khung vào lệnh): breakout và cloud tự suy ra khung trend, khối còn lại dùng đúng ``trend.tf`` của kế hoạch (không tra bảng nào)."""
    for strategy in ("combo", "ma_cross", "macd_break", "ema_reach"):
        spec = get_strategy(strategy)
        for entry_tf in spec.live_timeframes:
            assert spec.normalize_params({"TREND_METHODS": "cloud"}, "EURUSD", entry_tf)["TREND_TF"] == trend_tf.entry_based_trend_tf(("cloud",), entry_tf)
            assert spec.normalize_params({"TREND_METHODS": "knn", "TREND_TF": "H2"}, "EURUSD", entry_tf)["TREND_TF"] == "H2"
            assert spec.normalize_params({"TREND_METHODS": "zigzag", "TREND_TF": "H4"}, "EURUSD", entry_tf)["TREND_TF"] == "H4"
        if strategy != "macd_break":  # macd_break không dùng breakout (excluded_trend_methods)
            assert spec.normalize_params({"TREND_METHODS": "breakout"}, "EURUSD", "M45")["TREND_TF"] == "M45"
    assert get_strategy("ema_reach").excluded_trend_methods == () and get_strategy("macd_break").excluded_trend_methods == ("breakout",)
