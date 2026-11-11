"""Chốt chặn CẤU HÌNH (không phải hành vi): config thật và config mẫu phải khớp nhau và khớp bảng chiến lược, lưới ô (symbol x chiến lược x khung) dựng đúng theo bảng khung trend của we, việc của runner /
publisher dựng đúng, đầu vào và đầu ra Redis không lẫn port / mật khẩu / db. Nơi dựng lưới ô là configuration.build_cells; nơi dựng việc là build_runner_job / build_publisher_job.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from engine_support import (
    ALL_STRATEGIES,
    GROUP_SYMBOLS,
    GROUP_TIMEFRAMES,
    TREND_SOURCES,
    cell_config,
)

from core_engine.src import configuration
from core_engine.src.configuration import (
    CONFIG,
    OPERATING_SYMBOLS,
    STRATEGIES,
    Cell,
    build_cells,
)
from core_engine.src.data_engine import Pair
from core_engine.src.rules.trend_tf import method_names

EXAMPLE = yaml.safe_load((Path(__file__).resolve().parents[1] / "de_config.example.yaml").read_text(encoding="utf-8"))
CONFIGS = [("config thật", CONFIG), ("config mẫu", EXAMPLE)]


# =============================================================================
# 1. Config thật và config mẫu.
# =============================================================================


@pytest.mark.parametrize(("label", "config"), CONFIGS)
def test_symbols_chi_khai_mot_noi_va_khong_con_ke_hoach_theo_symbol(label: str, config: dict) -> None:
    """Danh sách symbol chỉ khai MỘT nơi (redis.input.symbols và các nguồn nến thêm): chiến lược không có khoá `symbols` riêng, và không còn mục `plans` (DE chạy mọi ô theo bảng của we)."""
    assert all("symbols" not in cfg for cfg in config["strategies"].values()), label
    assert "plans" not in config, label
    base = configuration._parse_redis(config)
    assert base["symbols"] == tuple(str(symbol).upper() for symbol in config["redis"]["input"]["symbols"]), label


def test_operating_symbols_la_hop_symbol_cua_moi_nguon_nen_cua_config_dang_nap() -> None:
    names = ["", *configuration._input_source_blocks(CONFIG["redis"])]  # nguồn mặc định (redis.input) rồi từng nguồn nến thêm
    groups = [configuration._parse_redis(CONFIG, name)["symbols"] for name in names]
    assert tuple(symbol for group in groups for symbol in group) == OPERATING_SYMBOLS  # mỗi symbol đúng một nhóm; hợp các nhóm là danh sách symbol DE vận hành


@pytest.mark.parametrize(("label", "config"), CONFIGS)
def test_luoi_o_day_du_theo_bang_we_15_symbol_x_5_chien_luoc_x_8_khung(label: str, config: dict) -> None:
    cells = build_cells(config)
    assert len(cells) == len(OPERATING_SYMBOLS) * len(ALL_STRATEGIES) * len(GROUP_TIMEFRAMES) == 600, label
    legs = [leg for plan in cells.values() for leg in plan.legs]
    assert len(legs) == 2280, label  # 480 ô trend x 4 khối + 120 ô macd_break... : combo/ma_cross/ema_reach 5 khối, macd_break 4 (không breakout), ichimoku không trend
    for strategy in ALL_STRATEGIES:
        mine = {cell: plan for cell, plan in cells.items() if cell.strategy == strategy}
        assert len(mine) == 120, (label, strategy)
        names = {leg.method for plan in mine.values() for leg in plan.legs}
        assert names == {"ichimoku": set(), "macd_break": set(TREND_SOURCES) - {"breakout"}}.get(strategy, set(TREND_SOURCES)), (label, strategy)
    assert all(plan.context_tf == cell.entry_tf for cell, plan in cells.items()), label  # chưa ô nào đổi khung context: mặc định = khung trigger


@pytest.mark.parametrize(("label", "config"), CONFIGS)
def test_o_cu_the_lay_dung_khung_trend_cua_bang_we_va_khoi_cung_khung_tu_suy(label: str, config: dict) -> None:
    cells = build_cells(config)

    def legs(symbol: str, strategy: str, tf: str) -> dict[str, str]:
        return {leg.method: leg.trend_tf for leg in cells[Cell(symbol, strategy, tf)].legs}

    assert legs("US30", "combo", "H1") == {"knn": "H4", "breakout": "H1", "zigzag": "H4", "tstat": "H4", "cloud": "H1"}  # bảng: combo H1 -> H4; breakout/cloud suy từ khung vào lệnh
    assert legs("US30", "ma_cross", "M10") == {"knn": "H4", "breakout": "M10", "zigzag": "H4", "tstat": "H4", "cloud": "H1"}  # cloud không nhỏ hơn H1
    assert legs("US30", "ma_cross", "M30")["knn"] == "H1"
    assert "breakout" not in legs("US30", "macd_break", "H1") and legs("US30", "ichimoku", "H1") == {}
    assert all(leg.trend_tf in ("H1", "H2", "H3", "H4") for cell, plan in cells.items() for leg in plan.legs if leg.method in ("knn", "zigzag", "tstat")), label


def test_ten_symbol_trong_bang_la_ten_theo_nguon_nen_khong_phai_ten_cua_we() -> None:
    table = CONFIG["strategies"]["combo"]["trend_tf_by_symbol"]
    assert set(table) == set(OPERATING_SYMBOLS) and not {"DE40", "J225", "GOLD"} & set(table)  # we gọi GER40/JP225/XAUUSD là DE40/J225/GOLD


# =============================================================================
# 2. Bảng khung trend và khung context sai bị từ chối ngay lúc dựng.
# =============================================================================


def _edit(strategy: str, edit) -> dict:
    config = cell_config()
    edit(config["strategies"][strategy])
    return config


@pytest.mark.parametrize(
    ("edit", "error", "message"),
    [
        (lambda cfg: cfg["trend_tf_by_symbol"].pop("HK50"), KeyError, r"thiếu 'strategies\.combo\.trend_tf_by_symbol\.HK50'"),
        (lambda cfg: cfg["trend_tf_by_symbol"]["HK50"].pop("H1"), KeyError, r"thiếu 'strategies\.combo\.trend_tf_by_symbol\.HK50' cho khung H1"),
        (lambda cfg: cfg.pop("trend_tf_by_symbol"), KeyError, r"thiếu 'strategies\.combo\.trend_tf_by_symbol'"),
        (lambda cfg: cfg["trend_tf_by_symbol"].update({"DE40": {}}), ValueError, "DE40 is not an operating symbol"),
        (lambda cfg: cfg["trend_tf_by_symbol"]["HK50"].update({"D1": "H4"}), ValueError, "entry timeframes the strategy does not run"),
        (lambda cfg: cfg["trend_tf_by_symbol"]["HK50"].update({"H1": "M30"}), ValueError, "trend timeframe M30 is not in strategies.combo.trend_timeframes"),
        (lambda cfg: cfg["trend_tf_by_symbol"]["HK50"].update({"H4": "H1"}), ValueError, "must not be smaller than the entry timeframe H4"),
        (lambda cfg: cfg.update(trend_tf_by_symbol=["HK50"]), TypeError, "must map symbol"),
    ],
)
def test_bang_khung_trend_sai_bi_tu_choi(edit, error: type[Exception], message: str) -> None:
    with pytest.raises(error, match=message):
        build_cells(_edit("combo", edit))


def test_khung_context_mac_dinh_la_khung_trigger_va_doi_duoc_theo_o_trong_luat_trigger_context_trend() -> None:
    config = _edit("macd_break", lambda cfg: cfg["context_tf_by_symbol"]["HK50"].update({"M10": "H1"}))
    cells = build_cells(config)
    assert cells[Cell("HK50", "macd_break", "M10")].context_tf == "H1"  # trend nhỏ nhất của ô là cloud H1 nên context H1 vẫn hợp lệ
    assert cells[Cell("HK50", "macd_break", "M20")].context_tf == "M20" and cells[Cell("US30", "macd_break", "M10")].context_tf == "M10"  # các ô còn lại đúng bằng khung trigger
    smaller = _edit("combo", lambda cfg: cfg["context_tf_by_symbol"]["HK50"].update({"H2": "H1"}))
    with pytest.raises(ValueError, match="context timeframe H1 must not be smaller than the trigger timeframe H2"):
        build_cells(smaller)
    larger_than_trend = _edit("macd_break", lambda cfg: cfg["context_tf_by_symbol"]["HK50"].update({"M10": "H2"}))
    with pytest.raises(ValueError, match="trend timeframe H1 must not be smaller than the context timeframe H2"):
        build_cells(larger_than_trend)  # cloud của ô M10 tính ở H1, nhỏ hơn context H2
    same_timeframe_methods = _edit("combo", lambda cfg: cfg["context_tf_by_symbol"]["HK50"].update({"M10": "H1"}))
    with pytest.raises(ValueError, match="trend timeframe M10 must not be smaller than the context timeframe H1"):
        build_cells(same_timeframe_methods)  # breakout tính ở chính khung trigger M10 nên context không thể lớn hơn nó
    outside = _edit("combo", lambda cfg: cfg["context_tf_by_symbol"]["HK50"].update({"H1": "D1"}))
    with pytest.raises(ValueError, match="D1"):
        build_cells(outside)


def test_khung_cua_chien_luoc_phai_nam_trong_nhom_khung_da_khai() -> None:
    config = cell_config()
    config["redis"]["input"]["timeframes"] = ["H1", "H2", "H3", "H4"]
    with pytest.raises(ValueError, match="not in redis.input.timeframes"):
        build_cells(config)


# =============================================================================
# 3. Việc của runner: đủ lưới, số nến, nguồn nến, thứ tự quét.
# =============================================================================


def test_runner_job_tinh_du_luoi_cua_nguon_khong_phu_thuoc_cau_hinh_o() -> None:
    job = configuration.build_runner_job(cell_config())
    assert {pair.symbol for pair in job.pairs} == set(GROUP_SYMBOLS) and len(job.pairs) == len(GROUP_SYMBOLS) * len(GROUP_TIMEFRAMES)
    assert job.strategies == tuple(STRATEGIES) and job.methods == ("knn", "breakout", "zigzag", "tstat", "cloud")
    assert [pair.timeframe for pair in job.pairs[: len(GROUP_SYMBOLS)]] == ["H4"] * len(GROUP_SYMBOLS) and job.pairs[-1].timeframe == "M10"  # khung lớn trước
    assert job.context == {"macd_fast": 5, "macd_slow": 25, "macd_signal": 5, "ma_period": 20}
    assert (job.sink["db"], job.inputs_sink["db"], job.write_bars, job.warmup_bars, job.process_on_startup) == (0, 1, 500, 0, True)
    assert set(job.source["snapshot_bars"].values()) == {1200} and job.source["db"] == 0  # zigzag đòi 3000 nhưng nguồn chỉ giữ 1200


def test_runner_job_doc_them_warmup_truoc_cua_so_ghi_va_chi_zigzag_doi_hon() -> None:
    job = configuration.build_runner_job(cell_config(warmup_bars=300))
    assert job.warmup_bars == 300 and job.write_bars == 500 and set(job.source["snapshot_bars"].values()) == {1200}
    config = cell_config(warmup_bars=300)
    config["redis"]["trend_outputs"] = {"knn": {"key_prefix": "L_DE_KNN", "channel_prefix": "L_CHANNEL_KNN"}}
    assert set(configuration.build_runner_job(config).source["snapshot_bars"].values()) == {800}  # không có zigzag: chỉ snapshot_bars + warmup_bars


def test_runner_job_cua_so_tinh_chung_la_ghi_cong_warmup_va_rieng_zigzag_1200() -> None:
    """Mỗi bảng chỉ được TÍNH trên snapshot_bars + warmup_bars nến cuối (ở đây 100 + 300 = 400; config thật 500 + 300); riêng zigzag (đòi 3000) và breakout (giữ hướng dài) dùng cả 1200 nến nguồn giữ. Số nến ĐỌC mỗi cặp vẫn là cửa sổ lớn nhất."""
    job = configuration.build_runner_job(cell_config(rows=100, warmup_bars=300))
    assert job.window_bars == 400 and job.write_bars == 100 and job.warmup_bars == 300
    assert job.trend_window_bars == {"knn": 400, "breakout": 1200, "zigzag": 1200, "tstat": 400, "cloud": 400}  # zigzag và breakout giữ trạng thái dài: cả nến nguồn giữ
    assert set(job.source["snapshot_bars"].values()) == {1200}  # đọc đủ cho zigzag
    config = cell_config(rows=100, warmup_bars=300)
    config["redis"]["trend_outputs"] = {"knn": {"key_prefix": "L_DE_KNN", "channel_prefix": "L_CHANNEL_KNN"}}
    only_knn = configuration.build_runner_job(config)
    assert only_knn.trend_window_bars == {"knn": 400} and set(only_knn.source["snapshot_bars"].values()) == {400}


def test_warmup_bars_bat_buoc_khong_am_va_cong_snapshot_khong_vuot_nen_nguon_giu() -> None:
    missing = cell_config()
    del missing["redis"]["input"]["warmup_bars"]
    for build in (configuration.build_runner_job, configuration.build_publisher_job):
        with pytest.raises(KeyError, match=r"redis\.input\.warmup_bars"):
            build(missing)
        with pytest.raises(ValueError, match="warmup_bars must not be negative"):
            build(cell_config(warmup_bars=-1))
        with pytest.raises(ValueError, match=r"snapshot_bars \+ warmup_bars \(1201\) exceeds redis\.input\.source_max_bars \(1200\)"):
            build(cell_config(warmup_bars=701))
        build(cell_config(warmup_bars=700))  # đúng 1200 nến: vừa đủ


@pytest.mark.parametrize(("label", "config"), CONFIGS)
def test_warmup_bars_cua_config_thuc_va_mau_hop_le_va_phu_hop_tat_ca_nguon(label: str, config: dict) -> None:
    for name in ["", *configuration._input_source_blocks(config["redis"])]:
        base = configuration._parse_redis(config, name)
        assert base["warmup_bars"] == config["redis"]["input"]["warmup_bars"] >= 0, (label, name)  # nguồn nến thêm kế thừa redis.input
        assert base["snapshot_bars"] + base["warmup_bars"] <= base["source_max_bars"], (label, name)


def test_runner_job_theo_nguon_nen_va_gioi_han_symbol() -> None:
    config = cell_config(og8=True)
    default, og8 = configuration.build_runner_job(config), configuration.build_runner_job(config, "og8")
    assert {pair.symbol for pair in default.pairs} == set(GROUP_SYMBOLS) and {pair.symbol for pair in og8.pairs} == {"EURUSD", "FR40"}
    assert (og8.source["host"], og8.source["port"], og8.source["password"], og8.source["key_prefix"]) == ("10.11.12.8", 6380, "mat-khau-og8", "L_CANDLE")
    assert og8.sink == default.sink and og8.inputs_sink == default.inputs_sink and og8.input_name == "og8"  # đầu ra luôn là Redis của DE, bất kể nguồn nến
    only = configuration.build_runner_job(config, symbols=["hk50"])
    assert {pair.symbol for pair in only.pairs} == {"HK50"} and len(only.pairs) == len(GROUP_TIMEFRAMES)
    with pytest.raises(ValueError, match="not served by this candle source"):
        configuration.build_runner_job(config, symbols=["EURUSD"])  # EURUSD thuộc nguồn og8


def test_runner_job_theo_lan_chia_vai_tro_chien_luoc_va_trend() -> None:
    """Làn chiến lược: đúng một chiến lược, không khối trend, không CONTEXT, nên chỉ đọc ``window_bars`` nến; làn trend: mọi khối + CONTEXT, không chiến lược, đọc đủ cho zigzag/breakout; không làn: trọn mọi vai trò."""
    config = cell_config(rows=500, warmup_bars=300)
    whole = configuration.build_runner_job(config)
    trend = configuration.build_runner_job(config, lane=configuration.TREND_LANE)
    assert (whole.strategies, whole.methods, whole.with_context) == (tuple(STRATEGIES), ("knn", "breakout", "zigzag", "tstat", "cloud"), True)
    assert (trend.strategies, trend.methods, trend.with_context) == ((), whole.methods, True) and trend.pairs == whole.pairs
    assert set(trend.source["snapshot_bars"].values()) == {1200}  # zigzag, breakout đòi cửa sổ dài
    for key in STRATEGIES:
        lane = configuration.build_runner_job(config, lane=key)
        assert (lane.strategies, lane.methods, lane.with_context) == ((key,), (), False), key
        assert lane.pairs == whole.pairs and set(lane.source["snapshot_bars"].values()) == {800}, key  # 500 + 300: chỉ chiến lược, không cần cửa sổ trend
        assert (lane.sink, lane.inputs_sink, lane.window_bars, lane.write_bars, lane.warmup_bars) == (whole.sink, whole.inputs_sink, whole.window_bars, whole.write_bars, whole.warmup_bars)
    with pytest.raises(ValueError, match=r"lane 'khong_co' is neither 'trend' nor a registered strategy"):
        configuration.build_runner_job(config, lane="khong_co")


def test_runner_job_theo_lan_giu_nguyen_nguon_nen_va_gioi_han_symbol() -> None:
    config = cell_config(og8=True)
    lane = configuration.build_runner_job(config, "og8", lane="combo")
    assert lane.input_name == "og8" and {pair.symbol for pair in lane.pairs} == {"EURUSD", "FR40"} and lane.strategies == ("combo",)
    only = configuration.build_runner_job(config, symbols=["hk50"], lane=configuration.TREND_LANE)
    assert {pair.symbol for pair in only.pairs} == {"HK50"} and only.strategies == ()


# =============================================================================
# 4. Việc của publisher: ô, cặp cấu phần cần nghe, bản lọc, nơi ghi.
# =============================================================================


def test_publisher_job_theo_chien_luoc_chia_o_khong_chong_nhau_va_gop_lai_du_bo_o() -> None:
    """Mỗi publisher một chiến lược: năm job gộp lại phải đúng bộ ô của một publisher trọn việc, không ô nào ở hai job; cặp cấu phần phải nghe của job con gồm đúng cặp mà ô của nó đọc."""
    config = cell_config()
    whole = configuration.build_publisher_job(config)
    parts = {key: configuration.build_publisher_job(config, strategy=key) for key in STRATEGIES}
    assert all({cell.strategy for cell in job.cells} == {key} and len(job.cells) == len(whole.cells) // len(STRATEGIES) for key, job in parts.items())
    assert {cell: job for part in parts.values() for cell, job in part.cells.items()} == whole.cells
    assert sum(len(part.cells) for part in parts.values()) == len(whole.cells)  # cộng số ô = số ô của bản trọn việc, mà hợp bằng nhau: không ô nào nằm ở hai job
    for key, job in parts.items():
        assert set(job.pairs) <= set(whole.pairs) and {cell for cells in job.routes.values() for cell in cells} == set(job.cells), key
        assert set(job.filtered_sinks) <= set(whole.filtered_sinks)
    assert parts["ichimoku"].filtered_sinks == {}  # ichimoku không lọc trend: không có bộ ghi bản lọc nào
    assert {pair.role for pair in parts["ichimoku"].pairs} == {"SETUP", "TRIGGER", "CONTEXT"}
    only = configuration.build_publisher_job(config, ["HK50"], "combo")
    assert {(cell.symbol, cell.strategy) for cell in only.cells} == {("HK50", "combo")}
    with pytest.raises(ValueError, match=r"strategy 'khong_co' is not a registered strategy"):
        configuration.build_publisher_job(config, strategy="khong_co")


def test_publisher_job_moi_o_doc_trigger_context_va_trend_tung_khoi() -> None:
    job = configuration.build_publisher_job(cell_config(), ["hk50"])
    assert len(job.cells) == len(ALL_STRATEGIES) * len(GROUP_TIMEFRAMES) and {cell.symbol for cell in job.cells} == {"HK50"}
    cell = Cell("HK50", "combo", "H1")
    cell_job = job.cells[cell]
    assert cell_job.trigger == Pair("HK50", "H1", "TRIGGER", "COMBO") and cell_job.context == Pair("HK50", "H1", "CONTEXT")  # context mặc định = chính khung trigger
    assert [(leg.method, leg.pair.timeframe, leg.pair.qualifier, leg.shifts) for leg in cell_job.legs] == [
        ("knn", "H4", "KNN", False), ("breakout", "H1", "BREAKOUT", True), ("zigzag", "H4", "ZIGZAG", False), ("tstat", "H4", "TSTAT", True), ("cloud", "H1", "CLOUD", True),
    ]
    assert cell_job.order_type == "STOP" and cell_job.validity == {"mode": "next_bar", "valid_bars": 3}
    assert cell_job.context_length == pd_timedelta("1h") and cell_job.legs[0].length == pd_timedelta("4h")
    assert job.cells[Cell("HK50", "ma_cross", "H1")].order_type == "MARKET" and job.cells[Cell("HK50", "ichimoku", "H1")].legs == ()


def pd_timedelta(text: str):
    import pandas as pd

    return pd.Timedelta(text)


def test_publisher_job_cac_cap_phai_nghe_va_duong_dan_su_kien_ve_o() -> None:
    job = configuration.build_publisher_job(cell_config(), ["HK50"])
    combo_h1 = Cell("HK50", "combo", "H1")
    assert job.routes[Pair("HK50", "H1", "TRIGGER", "COMBO")] == (combo_h1,)  # một cặp TRIGGER chỉ phục vụ đúng ô của nó
    context_cells = job.routes[Pair("HK50", "H1", "CONTEXT")]
    assert combo_h1 in context_cells and {cell.strategy for cell in context_cells} == set(ALL_STRATEGIES)  # CONTEXT dùng chung cho mọi chiến lược cùng khung
    knn_h4 = job.routes[Pair("HK50", "H4", "TREND", "KNN")]
    assert combo_h1 in knn_h4 and all(cell.strategy != "ichimoku" for cell in knn_h4)  # một cặp TREND đánh dấu mọi ô dùng nó
    assert set(job.pairs) == set(job.routes) and len(job.pairs) == len(set(job.pairs))
    assert set(job.filtered_sinks) == set(TREND_SOURCES)


def test_publisher_job_noi_doc_ghi_va_toan_bo_o_khi_khong_gioi_han_symbol() -> None:
    job = configuration.build_publisher_job(cell_config())
    assert len(job.cells) == 600 and job.source["components"] is True and job.source["snapshot_bars"] == 500 and job.source["key_prefix"] == ""
    assert (job.source["db"], job.source["role_db"], job.sink["db"]) == (0, {"TRIGGER": 1, "CONTEXT": 1, "TREND": 1}, 2)  # đọc SETUP ở db0, TRIGGER/CONTEXT/TREND ở db1, ghi tín hiệu ở db2
    knn = job.filtered_sinks["knn"]
    assert (knn["db"], knn["key_prefix"], knn["channel_prefix"], knn["source"]) == (2, "L_DE_KNN", "L_CHANNEL_KNN", "knn")


def test_publisher_job_gioi_han_symbol_va_thieu_han_hieu_luc() -> None:
    with pytest.raises(ValueError, match="no cells for symbols XXX"):
        configuration.build_publisher_job(cell_config(), ["XXX"])
    config = cell_config()
    del config["live"]["signal_validity"]["combo"]
    with pytest.raises(KeyError, match=r"thiếu 'live\.signal_validity\.combo'"):
        configuration.build_publisher_job(config)


# =============================================================================
# 5. Đầu vào và đầu ra Redis không lẫn port / mật khẩu / db.
# =============================================================================


def test_dau_vao_cau_phan_va_tin_hieu_dung_port_mat_khau_va_db_rieng() -> None:
    """Nến vào từ Redis nguồn (port gốc, mật khẩu nguồn); TRIGGER/CONTEXT (db0), TREND (db1) và tín hiệu (db2, cả bản thô lẫn bản lọc) đều ở Redis đầu ra (port và mật khẩu của `output`);
    publisher ĐỌC cấu phần đúng nơi runner GHI, và không bao giờ chạm db nến của Core."""
    config = cell_config()
    config["redis"]["password"] = "mat-khau-nguon"
    config["redis"]["output"].update(port=6380, password="mat-khau-dau-ra")
    runner, publisher = configuration.build_runner_job(config), configuration.build_publisher_job(config)
    assert (runner.source["port"], runner.source["password"], runner.source["db"]) == (6379, "mat-khau-nguon", 0)
    assert (runner.sink["port"], runner.sink["password"], runner.sink["db"], runner.sink["key_prefix"]) == (6380, "mat-khau-dau-ra", 0, "")
    assert (runner.inputs_sink["port"], runner.inputs_sink["password"], runner.inputs_sink["db"]) == (6380, "mat-khau-dau-ra", 1)
    assert (publisher.source["port"], publisher.source["password"], publisher.source["db"], publisher.source["key_prefix"]) == (
        runner.sink["port"], runner.sink["password"], runner.sink["db"], runner.sink["key_prefix"],
    )
    assert publisher.source["role_db"]["TREND"] == runner.inputs_sink["db"]  # publisher đọc TREND đúng db runner ghi
    assert (publisher.sink["port"], publisher.sink["password"], publisher.sink["db"]) == (6380, "mat-khau-dau-ra", 2)
    knn = publisher.filtered_sinks["knn"]
    assert (knn["port"], knn["password"], knn["db"], knn["key_prefix"], knn["channel_prefix"]) == (6380, "mat-khau-dau-ra", 2, "L_DE_KNN", "L_CHANNEL_KNN")
    assert runner.source["port"] != publisher.sink["port"] and len({runner.sink["db"], runner.inputs_sink["db"], publisher.sink["db"]}) == 3


@pytest.mark.parametrize(
    ("edit", "error", "message"),
    [
        (lambda redis: redis["setup"].update(db=2), ValueError, "must be three different databases"),
        (lambda redis: redis["signal_inputs"].update(db=0), ValueError, "must be three different databases"),
        (lambda redis: redis["signal_inputs"].update(db=2), ValueError, "must be three different databases"),
        (lambda redis: redis["setup"].update(window_bars=600, retention_max_entries=500), ValueError, "retention_max_entries must be at least"),
        (lambda redis: redis["setup"].update(window_bars=0), ValueError, "window_bars must be positive"),
        (lambda redis: redis["output"].update(retention_seconds=604801), ValueError, "exceeds the 7-day maximum"),
        (lambda redis: redis["output"].update(retention_seconds=0), ValueError, "retention_seconds must be positive"),
        (lambda redis: redis["setup"].pop("key_prefix"), KeyError, r"thiếu 'redis\.setup\.key_prefix'"),
        (lambda redis: redis.pop("setup"), KeyError, r"thiếu 'redis\.setup\.db'"),
        (lambda redis: redis.pop("signal_inputs"), KeyError, r"thiếu 'redis\.signal_inputs\.db'"),
        (lambda redis: redis["signal_inputs"].pop("key_prefix"), KeyError, r"thiếu 'redis\.signal_inputs\.key_prefix'"),
        (lambda redis: (redis.update(port=6380), redis["input"].update(db=0)), ValueError, "input and setup must use different databases"),
        (lambda redis: (redis.update(port=6380), redis["input"].update(db=1)), ValueError, "input and signal_inputs must use different databases"),
    ],
)
def test_cau_hinh_redis_cau_phan_sai_bi_tu_choi(edit, error: type[Exception], message: str) -> None:
    config = cell_config()
    edit(config["redis"])
    with pytest.raises(error, match=message):
        configuration._parse_redis(config)


def test_tin_hieu_db2_song_toi_da_7_ngay_va_cua_so_db0_db1_la_500_nen() -> None:
    """Chủ hệ chốt 09/11/2026: mỗi tín hiệu ở db2 sống tối đa 7 ngày (khai đúng 7 ngày thì hợp lệ, mặc định cũng 7 ngày); db0/db1 chỉ giữ 500 nến mỗi danh sách (chốt 10/11/2026, trước đó 100; config thật và config mẫu)."""
    config = cell_config()
    assert configuration._parse_redis(config)["sink"]["retention_seconds"] == 604800  # không khai: mặc định đúng 7 ngày
    config["redis"]["output"].update(retention_seconds=604800)
    assert configuration._parse_redis(config)["sink"]["retention_seconds"] == 604800
    config["redis"]["output"].update(retention_seconds=3600)  # ngắn hơn thì được
    assert configuration._parse_redis(config)["sink"]["retention_seconds"] == 3600
    for block in (CONFIG, EXAMPLE):
        parsed = configuration._parse_redis(block)
        assert parsed["snapshot_bars"] == 500 and parsed["reader_source"]["snapshot_bars"] == 500
        assert parsed["setup_sink"]["retention_max_entries"] == parsed["inputs_sink"]["retention_max_entries"] == 500
        assert parsed["sink"]["retention_seconds"] == 604800


def test_tham_so_context_bat_buoc_va_hop_le() -> None:
    assert configuration.context_params(cell_config()) == {"macd_fast": 5, "macd_slow": 25, "macd_signal": 5, "ma_period": 20}
    for block in (CONFIG, EXAMPLE):
        assert configuration.context_params(block) == {"macd_fast": 5, "macd_slow": 25, "macd_signal": 5, "ma_period": 20}
    missing = cell_config()
    del missing["context"]["ma_period"]
    with pytest.raises(KeyError, match=r"thiếu 'context\.ma_period'"):
        configuration.context_params(missing)
    bad = cell_config()
    bad["context"].update(macd_fast=30)
    with pytest.raises(ValueError, match="macd_fast must be smaller than context.macd_slow"):
        configuration.context_params(bad)
    zero = cell_config()
    zero["context"].update(ma_period=0)
    with pytest.raises(ValueError, match="positive"):
        configuration.context_params(zero)


def test_trend_outputs_ten_nguon_duoc_chuan_hoa_chu_thuong() -> None:
    config = cell_config()
    config["redis"]["trend_outputs"] = {" KNN ": {"key_prefix": "L_DE_KNN", "channel_prefix": "L_CHANNEL_KNN"}}
    assert list(configuration._parse_redis(config)["trend_outputs"]) == ["knn"]


def test_khung_live_la_khung_khuyen_nghi_neu_co_khong_thi_khung_duoc_ho_tro() -> None:
    for spec in STRATEGIES.values():
        assert spec.live_timeframes == (spec.recommended_timeframes or spec.supported_timeframes) and spec.live_timeframes


def test_cac_nguon_trend_theo_thu_tu_dang_ky_khong_theo_thu_tu_khai_bao() -> None:
    assert tuple(method_names()) == ("knn", "breakout", "zigzag", "tstat", "cloud")  # cùng thứ tự DB1 / DB2 / DB4 / DB3 / DB5 của we_core


def test_cau_hinh_alternating_cua_chien_luoc_cho_log_khoi_dong() -> None:
    assert all(isinstance(configuration.alternating_filter_for(key), bool) for key in ALL_STRATEGIES)


def test_bang_context_rieng_bat_buoc_du_va_hien_bang_khung_vao_lenh() -> None:
    """Chủ hệ chốt 10/11/2026: context có bảng riêng trong config, bằng khung vào lệnh; thiếu symbol hoặc khung thì lỗi, không có giá trị ngầm."""
    for block in (CONFIG, EXAMPLE):
        for key, strategy in block["strategies"].items():
            table = strategy["context_tf_by_symbol"]
            assert set(table) == set(OPERATING_SYMBOLS) and all(row == {tf: tf for tf in row} and len(row) == 8 for row in table.values()), key  # trigger M10 thì context M10
    with pytest.raises(KeyError, match=r"thiếu 'strategies\.combo\.context_tf_by_symbol'"):
        build_cells(_edit("combo", lambda cfg: cfg.pop("context_tf_by_symbol")))
    with pytest.raises(KeyError, match=r"thiếu 'strategies\.ichimoku\.context_tf_by_symbol\.HK50' cho khung H1"):
        build_cells(_edit("ichimoku", lambda cfg: cfg["context_tf_by_symbol"]["HK50"].pop("H1")))


@pytest.mark.parametrize(("value", "ok"), [(0, True), (5, True), (998, True), (999, True), (-1, False), (1000, False), (4.5, False), ("5", False), (True, False), (None, False)])
def test_live_max_shift_bars_la_so_nguyen_tu_0_den_999(value, ok: bool) -> None:
    config = cell_config()
    config["live"]["max_shift_bars"] = value
    if ok:
        assert configuration.build_publisher_job(config, ["HK50"]).max_shift_bars == value
    else:
        with pytest.raises(ValueError, match="live.max_shift_bars"):
            configuration.build_publisher_job(config, ["HK50"])


def test_live_max_shift_bars_bat_buoc_va_config_that_lan_config_mau_deu_khai_999() -> None:
    config = cell_config()
    del config["live"]["max_shift_bars"]
    with pytest.raises(KeyError, match=r"thiếu 'live\.max_shift_bars'"):
        configuration.build_publisher_job(config, ["HK50"])
    assert EXAMPLE["live"]["max_shift_bars"] == 999  # config mẫu: dời không giới hạn
    assert isinstance(CONFIG["live"]["max_shift_bars"], int) and 0 <= CONFIG["live"]["max_shift_bars"] <= 999  # config thật do chủ hệ chọn (10/11/2026: 5)
