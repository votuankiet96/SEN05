"""Chốt chặn CẤU HÌNH (không phải hành vi): config thật và config mẫu phải khớp nhau và khớp bảng chiến lược, mỗi chiến lược chạy đúng nhóm symbol x khung, ô trend theo đúng bảng, đầu vào
và đầu ra Redis không lẫn port/mật khẩu. Chuyển từ test_live_settings.py / test_live_plan.py / test_live_worker.py khi bỏ gói runner/ (2026-11-02); nơi dựng việc nay là
configuration.build_lane_job / build_trend_job.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from engine_support import (
    ALL_STRATEGIES,
    GROUP_SYMBOLS,
    GROUP_TIMEFRAMES,
    lane_config,
    trend_config,
)

from core_engine.src import configuration
from core_engine.src.configuration import (
    CONFIG,
    OPERATING_SYMBOLS,
    STRATEGIES,
    get_strategy,
    trend_enabled_for,
    trend_method_applies,
    trend_source_for,
)
from core_engine.src.rules.trend_tf import method_names

EXAMPLE = yaml.safe_load((Path(__file__).resolve().parents[1] / "de_config.example.yaml").read_text(encoding="utf-8"))
CONFIGS = [("config thật", CONFIG), ("config mẫu", EXAMPLE)]


# =============================================================================
# 1. Config thật và config mẫu.
# =============================================================================


@pytest.mark.parametrize(("label", "config"), CONFIGS)
def test_symbols_chi_khai_mot_noi_va_nhom_khung_bao_moi_chien_luoc(label: str, config: dict) -> None:
    """Danh sách symbol chỉ khai MỘT nơi (redis.input.symbols): chiến lược không có khoá `symbols` riêng. Không có phép kiểm chéo lúc chạy (config quyết định, cấu hình phải khai khớp từ đầu),
    nên test này giữ cấu hình không lệch: nhóm khung bao mọi khung vào lệnh lẫn mọi khung trend mà các chiến lược đang bật cần đọc (nhóm khung KHÔNG được âm thầm bỏ khung nào của
    chiến lược); mỗi chiến lược có bảng khung trend đủ ô cho từng symbol của nhóm."""
    assert all("symbols" not in cfg for cfg in config["strategies"].values()), label
    base = configuration._parse_redis(config)
    assert base["symbols"] == tuple(str(symbol).upper() for symbol in config["redis"]["input"]["symbols"]), label
    declared = set(base["timeframes"])
    for key in config["live"]["enabled_strategies"]:
        spec = get_strategy(str(key))
        assert set(spec.live_timeframes) <= declared, (label, key, spec.live_timeframes)
        for timeframe in spec.live_timeframes:
            if not trend_enabled_for(spec.key, timeframe):
                continue
            for symbol in base["symbols"]:
                for source in base["trend_outputs"]:
                    if trend_method_applies(spec.key, source):
                        trend_tf = trend_source_for(spec.key, symbol, timeframe, source).trend_tf
                        assert trend_tf in declared, (label, key, symbol, timeframe, source, trend_tf)


def test_operating_symbols_la_hop_symbol_cua_moi_nguon_nen_cua_config_dang_nap() -> None:
    names = ["", *configuration._input_source_blocks(CONFIG["redis"])]  # nguồn mặc định (redis.input) rồi từng nguồn nến thêm
    groups = [configuration._parse_redis(CONFIG, name)["symbols"] for name in names]
    assert tuple(symbol for group in groups for symbol in group) == OPERATING_SYMBOLS  # mỗi symbol đúng một nhóm; hợp các nhóm là danh sách mà bảng khung trend của chiến lược phải phủ


@pytest.mark.parametrize(("label", "config"), CONFIGS)
def test_config_dung_duoc_viec_cua_moi_tien_trinh(label: str, config: dict) -> None:
    """Config đang nạp (config thật trên máy chạy; khai ít hay nhiều nguồn trend là quyết định vận hành, kể cả tạm tắt hết chỉ còn raw) và bản mẫu (khai đủ mọi nguồn):
    mọi chiến lược đang bật dựng được việc của tiến trình chiến lược, mọi nguồn trend đã khai dựng được việc của tiến trình trend; raw không đổi tên."""
    declared = list(configuration._parse_redis(config)["trend_outputs"])
    assert set(declared) <= set(method_names()), label
    for key in config["live"]["enabled_strategies"]:
        job = configuration.build_lane_job(config, str(key))
        assert job.outputs[0] == ("raw", "L_DE", "L_CHANNEL"), (label, key)  # nguồn raw luôn đứng đầu và không đổi tên
        used = {cell.method for cells in job.trend_cells.values() for cell in cells}
        assert [name for name, _, _ in job.outputs[1:]] == [method for method in declared if method in used], (label, key)  # kèm đúng các khối có ô áp dụng, theo thứ tự khai
        assert set(job.filtered_sinks) == used and (job.trend_source is not None) == bool(used), (label, key)
    for method in declared:
        assert configuration.build_trend_job(config, method).pairs, (label, method)
    if label == "config mẫu":
        assert set(declared) == set(method_names())  # bản mẫu: raw + đủ năm nguồn trend


def test_cau_hinh_that_va_mau_macd_break_loc_trend_moi_khung_ichimoku_khong_trend() -> None:
    macd_break, ichimoku = get_strategy("macd_break"), get_strategy("ichimoku")
    assert macd_break.trend_off_timeframes == () and macd_break.excluded_trend_methods == ("breakout",)  # chốt lại 2026-10-28
    assert all(set(cells) == set(GROUP_TIMEFRAMES) for cells in configuration.get_strategy("macd_break").trend_tf_by_symbol.values())
    assert set(ichimoku.trend_off_timeframes) == set(ichimoku.supported_timeframes)
    assert "ichimoku" in CONFIG["live"]["enabled_strategies"]


# =============================================================================
# 2. Định tuyến và ô trend theo bảng chiến lược.
# =============================================================================


def test_moi_chien_luoc_chay_du_nhom_khung_va_symbol() -> None:
    """Cả 5 chiến lược chạy MỌI khung (2026-11-02), mỗi khung ở mọi symbol của nhóm: một nến của khung nào cũng có thể kích hoạt mọi chiến lược đang bật."""
    config = lane_config(ALL_STRATEGIES)
    for key in ALL_STRATEGIES:
        job = configuration.build_lane_job(config, key)
        assert set(job.pairs) == {(symbol, timeframe) for symbol in GROUP_SYMBOLS for timeframe in GROUP_TIMEFRAMES}, key
        assert len(job.pairs) == len(GROUP_SYMBOLS) * len(GROUP_TIMEFRAMES)
    assert tuple(job.strategy for job in (configuration.build_lane_job(config, key) for key in ALL_STRATEGIES)) == ALL_STRATEGIES


def test_khung_live_la_khung_khuyen_nghi_neu_co_khong_thi_khung_duoc_ho_tro() -> None:
    for spec in STRATEGIES.values():
        assert spec.live_timeframes == (spec.recommended_timeframes or spec.supported_timeframes) and spec.live_timeframes


def test_cac_nguon_trend_theo_thu_tu_dang_ky_khong_theo_thu_tu_khai_bao() -> None:
    assert tuple(method_names()) == ("knn", "breakout", "zigzag", "tstat", "cloud")  # cùng thứ tự DB1 / DB2 / DB4 / DB3 / DB5 của we_core


def test_cau_hinh_alternating_cua_chien_luoc_cho_log_khoi_dong() -> None:
    assert all(isinstance(configuration.alternating_filter_for(key), bool) for key in ALL_STRATEGIES)


# =============================================================================
# 3. Đầu vào và đầu ra Redis không lẫn port / mật khẩu.
# =============================================================================


def test_dau_vao_dau_ra_raw_va_dau_ra_trend_dung_port_va_mat_khau_rieng() -> None:
    """Nến vào từ Redis nguồn (port gốc, mật khẩu nguồn); tín hiệu raw (db0), kết quả trend (db1) và tín hiệu đã lọc (db2) đều ở Redis đầu ra (port và mật khẩu của `output`);
    tiến trình chiến lược ĐỌC xu hướng ở db1 và GHI tín hiệu lọc ở db2, không bao giờ chạm db nến của Core."""
    config = trend_config(("ma_cross",), ("knn",))
    config["redis"]["password"] = "mat-khau-nguon"
    config["redis"]["output"].update(port=6380, password="mat-khau-dau-ra")
    lane, trend = configuration.build_lane_job(config, "ma_cross"), configuration.build_trend_job(config, "knn")
    assert (lane.source["port"], lane.source["password"], lane.source["db"]) == (6379, "mat-khau-nguon", 0)
    assert (trend.source["port"], trend.source["password"], trend.source["db"]) == (6379, "mat-khau-nguon", 0)
    assert (lane.sink["port"], lane.sink["password"], lane.sink["db"]) == (6380, "mat-khau-dau-ra", 0)  # tín hiệu raw ở db0
    assert (trend.sink["port"], trend.sink["password"], trend.sink["db"]) == (6380, "mat-khau-dau-ra", 1)  # kết quả trend ở db1
    assert trend.sink["key_prefix"] == "L_TREND" and lane.sink["key_prefix"] == "L_DE"
    assert (lane.trend_source["port"], lane.trend_source["password"], lane.trend_source["db"], lane.trend_source["key_prefix"]) == (6380, "mat-khau-dau-ra", 1, "L_TREND")  # đọc đúng nơi trend ghi
    sink = lane.filtered_sinks["knn"]
    assert (sink["port"], sink["password"], sink["db"], sink["key_prefix"], sink["channel_prefix"]) == (6380, "mat-khau-dau-ra", 2, "L_DE_KNN", "L_CHANNEL_KNN")
    assert len({lane.sink["db"], lane.trend_source["db"], sink["db"]}) == 3  # ba db đầu ra khác nhau
    assert lane.source["port"] != sink["port"]  # db lọc nằm ở Redis đầu ra, khác Redis của Core


def test_trend_outputs_ten_nguon_duoc_chuan_hoa_chu_thuong() -> None:
    config = lane_config(("ma_cross",))
    config["redis"]["trend_outputs"] = {" KNN ": {"key_prefix": "L_DE_KNN", "channel_prefix": "L_CHANNEL_KNN"}}
    assert list(configuration._parse_redis(config)["trend_outputs"]) == ["knn"]
