"""Test bảng NGUỒN trend của DE: cách chạy từng nguồn lọc cho một ô (configuration.trend_source_for) và cấu hình mẫu khai đủ nguồn — hermetic, KHÔNG nối Redis.

Từ 2026-11-02 phần đường xử lý một sự kiện, cấu hình Redis và kết nối nằm ở test_lane.py / test_trend_lane.py / test_config_guards.py / test_data_engine.py / test_signal_publisher.py (gói runner/ cũ đã xoá 02/11/2026); file này chỉ còn phần thuộc thế giới chiến lược/trend:
mỗi khối trend cho khung trend nào, có dời tín hiệu không, cần bao nhiêu nến (bảng POLICY ở rules/trend_tf) và de_config.example.yaml khai đủ các nguồn.
"""

from __future__ import annotations

import pytest

from core_engine.src.configuration import (
    OPERATING_SYMBOLS,
    TrendSource,
    get_strategy,
    trend_source_for,
)


def test_config_mau_khai_du_nguon_va_bang_khung_trend_cho_15_symbol_de() -> None:
    from pathlib import Path

    import yaml

    example = yaml.safe_load((Path(__file__).resolve().parents[1] / "de_config.example.yaml").read_text(encoding="utf-8"))
    trend_outputs = example["redis"]["trend_outputs"]
    assert list(trend_outputs) == ["knn", "breakout", "zigzag", "tstat", "cloud"]
    prefixes = [example["redis"]["output"]["key_prefix"], *(item["key_prefix"] for item in trend_outputs.values())]
    channels = [example["redis"]["output"]["channel_prefix"], *(item["channel_prefix"] for item in trend_outputs.values())]
    assert prefixes == ["L_DE", "L_DE_KNN", "L_DE_BREAKOUT", "L_DE_ZIGZAG", "L_DE_TSTAT", "L_DE_CLOUD"]
    assert channels == ["L_CHANNEL", "L_CHANNEL_KNN", "L_CHANNEL_BREAKOUT", "L_CHANNEL_ZIGZAG", "L_CHANNEL_TSTAT", "L_CHANNEL_CLOUD"]
    assert example["redis"]["input"]["symbols"] == ["US30", "US500", "US100", "GER40", "HK50", "JP225", "XAUUSD", "BTCUSD"]
    og8 = example["redis"]["input_sources"]["og8"]  # nguồn nến thêm: Redis DF của OG8 nuôi 7 symbol mà Core AEN không có
    assert og8["symbols"] == ["EURUSD", "GBPUSD", "USDCAD", "USDJPY", "FR40", "SP35", "UK100"] and set(og8) == {"host", "port", "password", "db", "key_prefix", "symbols"}
    assert example["live"]["price_decimals"] == {"EURUSD": 5, "GBPUSD": 5, "USDCAD": 5, "USDJPY": 3}
    assert example["redis"]["input"]["timeframes"] == ["M10", "M20", "M30", "M45", "H1", "H2", "H3", "H4"]
    assert "symbols" not in example["strategies"]["combo"]  # danh sách symbol chỉ khai ở redis.input / redis.input_sources, không khai lại ở chiến lược
    for key in ("combo", "ma_cross", "macd_break"):
        cfg = example["strategies"][key]
        assert "trend_type" not in cfg and "default_trend_tf" not in cfg  # bỏ từ khi có bảng khung trend theo ô
        assert cfg["trend_methods"] == ["knn"]
        assert set(cfg["trend_tf_by_symbol"]) == set(example["redis"]["input"]["symbols"]) | set(og8["symbols"])
    macd_break = example["strategies"]["macd_break"]
    assert macd_break["trend_off_timeframes"] == [] and macd_break["excluded_trend_methods"] == ["breakout"]  # chốt lại 2026-10-28: lọc trend ở mọi khung, trừ breakout
    assert all(set(cells) == {"M10", "M20", "M30", "M45", "H1", "H2", "H3", "H4"} for cells in macd_break["trend_tf_by_symbol"].values())  # đủ ô cả 8 khung
    assert "trend_tf_by_symbol" not in example["strategies"]["ichimoku"] and "trend_methods" not in example["strategies"]["ichimoku"]  # ichimoku không có trend
    assert OPERATING_SYMBOLS == tuple(example["redis"]["input"]["symbols"]) + tuple(og8["symbols"])


def test_trend_source_for_theo_khoi_va_bang_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    from core_engine.src.rules import trend_tf

    # breakout: chính khung vào lệnh, dời tín hiệu, không cần nến thêm
    assert trend_source_for("combo", "US30", "H1", "breakout") == TrendSource("H1", True, 0)
    assert trend_source_for("ma_cross", "US30", "M30", "breakout") == TrendSource("M30", True, 0)
    # cloud: chính khung vào lệnh nhưng không nhỏ hơn H1 (M10..M45 -> H1), dời tín hiệu, không tra bảng, không cần nến thêm
    assert trend_source_for("combo", "US30", "H1", "cloud") == TrendSource("H1", True, 0)
    assert trend_source_for("combo", "US30", "H4", "cloud") == TrendSource("H4", True, 0)
    assert trend_source_for("ma_cross", "US30", "M30", "cloud") == TrendSource("H1", True, 0)
    assert trend_source_for("macd_break", "US30", "M10", "cloud") == TrendSource("H1", True, 0)
    # knn: khung lớn tra bảng trend_tf_by_symbol, không dời tín hiệu
    knn = trend_source_for("macd_break", "US30", "M30", "knn")
    assert knn.shifts_signals is False and knn.warmup_bars == 0 and knn.trend_tf in get_strategy("combo").trend_timeframes
    # zigzag: cùng bảng khung với knn, không dời tín hiệu, khối đòi ~3000 nến khung trend (Redis chỉ giữ 1.200 mỗi danh sách)
    zigzag = trend_source_for("combo", "US30", "H4", "zigzag")
    assert zigzag == TrendSource(zigzag.trend_tf, False, 3000) and zigzag.trend_tf in get_strategy("combo").trend_timeframes
    assert zigzag.trend_tf == trend_source_for("combo", "US30", "H4", "knn").trend_tf
    # bật lại chính sách dời cho knn (một dòng ở bảng POLICY) thì nguồn knn cũng dời
    monkeypatch.setitem(trend_tf.POLICY, "knn", trend_tf.MethodPolicy(first_aligned_per_run=True))
    assert trend_source_for("macd_break", "US30", "M30", "knn").shifts_signals is True
    # ngoài bảng: khối cần khung tham chiếu riêng chưa có khung trend; breakout và cloud vẫn suy ra từ khung vào lệnh
    assert trend_source_for("combo", "AUDUSD", "H1", "knn").trend_tf == ""
    assert trend_source_for("combo", "AUDUSD", "H1", "breakout").trend_tf == "H1"
    assert trend_source_for("combo", "AUDUSD", "H1", "cloud").trend_tf == "H1"
    # khung tắt trend (mọi khung của ichimoku): không có nguồn trend nào, hỏi là lỗi (build_lane_job không bao giờ hỏi: xem trend_enabled_for)
    for method in ("knn", "breakout", "zigzag", "tstat", "cloud"):
        with pytest.raises(ValueError, match="does not use a trend filter on timeframe H1"):
            trend_source_for("ichimoku", "US30", "H1", method)
    # macd_break dùng lọc trend ở H1 (2026-10-28) với khung trend theo bảng; riêng breakout bị loại (excluded_trend_methods): hỏi là lỗi
    assert trend_source_for("macd_break", "US30", "H1", "knn").trend_tf == "H1" and trend_source_for("macd_break", "US30", "H2", "knn").trend_tf == "H3"
    with pytest.raises(ValueError, match="cannot be used with this strategy"):
        trend_source_for("macd_break", "US30", "H1", "breakout")


def test_trend_source_for_ema_reach_theo_bang_va_khoi_cung_khung() -> None:
    for symbol in OPERATING_SYMBOLS:
        for entry_tf in ("M10", "M20", "M30", "M45"):
            knn = trend_source_for("ema_reach", symbol, entry_tf, "knn")
            assert knn.trend_tf == ("H4" if entry_tf == "M10" else "H1") and knn.shifts_signals is False
            assert trend_source_for("ema_reach", symbol, entry_tf, "zigzag").trend_tf == knn.trend_tf
            assert trend_source_for("ema_reach", symbol, entry_tf, "tstat").shifts_signals is True
            assert trend_source_for("ema_reach", symbol, entry_tf, "breakout").trend_tf == entry_tf  # cùng khung vào lệnh
            assert trend_source_for("ema_reach", symbol, entry_tf, "cloud").trend_tf == "H1"  # cùng khung nhưng sàn H1
