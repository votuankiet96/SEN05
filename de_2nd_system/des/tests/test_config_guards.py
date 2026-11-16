"""Test configuration.py phần CONFIG: đọc và kiểm des_config.yaml (``parse_plan``), tách phần nạp lên Redis, nạp và đọc config có phiên bản, dựng việc của từng tiến trình — hermetic, Redis giả.

Nguyên tắc: không có giá trị ngầm; thiếu khoá thì KeyError nêu đúng đường dẫn; config sai thì không được nạp lên Redis. Chiến lược và yếu tố trend chỉ được đăng ký bằng config (không tên nào viết cứng trong mã).
"""

from __future__ import annotations

import copy
import json

import pandas as pd
import pytest
import yaml
from engine_support import (
    SERVERS,
    STORE,
    FakeConfigRedis,
    local_config,
    plan,
    shared_config,
    with_break20,
    with_combo,
)

from des.src import configuration
from des.src.data_engine import Pair
from des.src.de_module import combo_strategy, mac_strategy
from des.src.de_module import trend as trend_module

# =============================================================================
# 1. parse_plan: đọc đúng và từ chối config sai.
# =============================================================================


def test_parse_plan_doc_dung_gia_tri_va_mo_rong_symbols_all_thanh_moi_symbol_cua_cac_nguon() -> None:
    result = plan(og8=True)
    assert result.enabled and result.log_level == "INFO" and (result.snapshot_bars, result.warmup_bars, result.process_on_startup) == (500, 300, True)
    ma_cross = result.strategies["ma_cross"]
    assert ma_cross.trigger == "M10" and ma_cross.symbols == ("US30", "BTCUSD", "EURUSD", "FR40") and ma_cross.order_type == "MARKET"
    assert ma_cross.params == {"fast_ma": 13, "slow_ma": 34, "macd_fast": 5, "macd_slow": 25, "macd_signal": 5} and ma_cross.detect is mac_strategy.detect
    assert (ma_cross.atr_period, ma_cross.max_shift_bars, ma_cross.validity_seconds) == (5, 3, 600) and ma_cross.trends == (("macd", "M30"), ("sma20", "M30"))
    assert set(result.strategies) == {"ma_cross"} and set(result.trends) == {"macd", "sma20"}
    assert result.trends["macd"].function is trend_module.bias_macd and result.trends["macd"].params == {"fast": 5, "slow": 25, "signal": 5}
    assert result.trends["sma20"].function is trend_module.bias_sma and result.trends["sma20"].params == {"period": 20}
    assert result.output["strategy_prefix"] == "L_MODSTR" and result.output["trend_prefix"] == "L_MODTRE" and result.output["signal_prefix"] == "L_DE" and result.output["host"] == "127.0.0.1" and result.output["db"] == 11
    assert result.price_decimals == {"EURUSD": 5} and result.timeouts == {"socket_timeout": 5.0, "connect_timeout": 5.0, "healthcheck_interval": 30} and result.reconnect_delay == 3.0


def test_parse_plan_symbols_la_danh_sach_rieng_thi_dung_danh_sach_do() -> None:
    config = shared_config()
    config["strategies"]["ma_cross"]["symbols"] = ["btcusd"]
    assert configuration.parse_plan(config, SERVERS).strategies["ma_cross"].symbols == ("BTCUSD",)


def test_dang_ky_them_combo_chi_bang_config() -> None:
    result = configuration.parse_plan(with_combo(shared_config()), SERVERS)
    combo = result.strategies["combo"]
    assert combo.detect is combo_strategy.detect and combo.trigger == "M30" and combo.order_type == "STOP" and combo.validity_seconds == 5400
    assert combo.params == {"ma_period": 20, "macd_fast": 5, "macd_slow": 25, "macd_signal": 5} and combo.trends == (("macd", "H1"),)
    assert set(result.trends) == {"macd", "sma20"}  # Combo dùng lại yếu tố macd ở khung H1, không có yếu tố riêng
    assert result.strategies["ma_cross"].trends == (("macd", "M30"), ("sma20", "M30"))
    assert combo.max_shift_bars == 0 and result.strategies["ma_cross"].max_shift_bars == 3  # chỉ MA Cross được dời tín hiệu, Combo không dời


def test_dang_ky_them_yeu_to_break20_chi_bang_config() -> None:
    result = configuration.parse_plan(with_break20(with_combo(shared_config())), SERVERS)
    assert result.strategies["combo"].trends == (("break20", "M45"),)
    assert result.trends["break20"].function is trend_module.bias_break and result.trends["break20"].params == {"lookback": 20}


@pytest.mark.parametrize(
    ("path", "missing"),
    [
        (("redis", "enabled"), "redis.enabled"),
        (("redis", "candles", "snapshot_bars"), "redis.candles.snapshot_bars"),
        (("redis", "candles", "sources", "default", "clock_key"), "redis.candles.sources.default.clock_key"),
        (("redis", "candles", "process_on_startup"), "redis.candles.process_on_startup"),
        (("redis", "output", "strategy_prefix"), "redis.output.strategy_prefix"),
        (("redis", "output", "trend_prefix"), "redis.output.trend_prefix"),
        (("redis", "output", "retention_seconds"), "redis.output.retention_seconds"),
        (("strategies", "ma_cross", "file"), "strategies.ma_cross.file"),
        (("strategies", "ma_cross", "trigger"), "strategies.ma_cross.trigger"),
        (("strategies", "ma_cross", "symbols"), "strategies.ma_cross.symbols"),
        (("strategies", "ma_cross", "params"), "strategies.ma_cross.params"),
        (("strategies", "ma_cross", "max_shift_bars"), "strategies.ma_cross.max_shift_bars"),
        (("strategies", "ma_cross", "atr_period"), "strategies.ma_cross.atr_period"),
        (("strategies", "ma_cross", "validity_seconds"), "strategies.ma_cross.validity_seconds"),
        (("strategies", "ma_cross", "order_type"), "strategies.ma_cross.order_type"),
        (("strategies", "ma_cross", "trend"), "strategies.ma_cross.trend"),
        (("trends", "macd", "type"), "trends.macd.type"),
        (("live", "log_level"), "live.log_level"),
        (("trends",), "trends"),
        (("strategies",), "strategies"),
    ],
)
def test_thieu_khoa_nao_thi_keyerror_neu_dung_duong_dan(path: tuple[str, ...], missing: str) -> None:
    config = shared_config()
    node = config
    for key in path[:-1]:
        node = node[key]
    del node[path[-1]]
    with pytest.raises(KeyError, match=missing.replace(".", r"\.")):
        configuration.parse_plan(config, SERVERS)


def _edit(config: dict, path: tuple[str, ...], value: object) -> dict:
    node = config
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return config


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("strategies", "ma_cross", "params", "fast_ma"), 34, "fast_ma.*smaller than.*slow_ma"),
        (("strategies", "ma_cross", "params", "macd_fast"), 25, "macd_fast.*smaller than.*macd_slow"),
        (("strategies", "ma_cross", "params", "slow_ma"), 0, r"slow_ma.*integer >= 1"),
        (("strategies", "ma_cross", "params", "fast_ma"), True, "integer"),
        (("strategies", "ma_cross", "params", "extra"), 5, r"does not match detect\(\).*extra"),
        (("strategies", "ma_cross", "params"), {"fast_ma": 13}, r"does not match detect\(\).*missing"),
        (("strategies", "ma_cross", "params"), [13, 34], "mapping of parameters"),
        (("strategies", "ma_cross", "max_shift_bars"), 500, "max_shift_bars.*smaller than redis.candles.snapshot_bars"),
        (("strategies", "ma_cross", "max_shift_bars"), -1, "max_shift_bars"),
        (("strategies", "ma_cross", "trigger"), "M5", "M5.*not in redis.candles.timeframes"),
        (("strategies", "ma_cross", "trigger"), "X7", "Unsupported timeframe"),
        (("strategies", "ma_cross", "symbols"), ["DOGE"], "no candle source feeds: DOGE"),
        (("strategies", "ma_cross", "symbols"), "some", "list of symbols or the word 'all'"),
        (("strategies", "ma_cross", "symbols"), ["US30", "US30"], "twice"),
        (("strategies", "ma_cross", "order_type"), "LIMIT", "order_type.*MARKET, STOP"),
        (("strategies", "ma_cross", "file"), "khong_co", "no de_module/khong_co.py"),
        (("strategies", "ma_cross", "file"), "Khong Co!", "file name in de_module"),
        (("strategies", "ma_cross", "file"), "trend", "has no function detect"),
        (("strategies", "ma_cross", "trend"), {}, "at least one"),
        (("strategies", "ma_cross", "trend"), {"khong_co": "M30"}, "references 'khong_co'.*not declared"),
        (("strategies", "ma_cross", "trend"), {"macd": "M5", "sma20": "M30"}, "M5.*not in redis.candles.timeframes"),
        (("strategies", "ma_cross", "trend"), {"macd": "X7", "sma20": "M30"}, "Unsupported timeframe"),
        (("trends", "macd", "fast"), 25, "fast.*smaller than.*slow"),
        (("trends", "sma20", "period"), 0, "period.*integer >= 1"),
        (("trends", "macd", "extra"), 1, r"does not match bias_macd\(\).*extra"),
        (("trends", "macd", "signal"), 1.5, "signal.*integer >= 1"),
        (("trends", "sma20", "type"), "khong_co", "no function bias_khong_co"),
        (("trends", "sma20"), "sma", "mapping of type and parameters"),
        (("live", "log_level"), "LOUD", "not a logging level"),
        (("live", "price_decimals"), {"DOGE": 2}, "not an operating symbol"),
        (("live", "price_decimals"), {"BTCUSD": 9}, "between 0 and 8"),
        (("redis", "enabled"), "yes", "true or false"),
        (("redis", "candles", "process_on_startup"), "yes", "true or false"),
        (("redis", "candles", "warmup_bars"), 800, "exceeds the candles a source keeps"),
        (("redis", "candles", "snapshot_bars"), 0, "snapshot_bars"),
        (("redis", "candles", "timeframes"), ["M10", "Q9"], "Unsupported timeframe"),
        (("redis", "output", "retention_seconds"), 604801, "7-day maximum"),
        (("redis", "output", "channel_prefix"), " ", "channel_prefix.*must be configured"),
        (("redis", "output", "trend_prefix"), " ", "trend_prefix.*must be configured"),
        (("redis", "output", "trend_prefix"), "L_MODSTR", "must differ from each other"),
        (("redis", "output", "signal_prefix"), "L_MODTRE", "must differ from each other"),
        (("redis", "output", "server"), "khong-co", "redis.output.server.*not declared"),
    ],
)
def test_config_sai_thi_tu_choi_voi_thong_bao_dung_cho(path: tuple[str, ...], value: object, message: str) -> None:
    with pytest.raises((ValueError, TypeError), match=message):
        configuration.parse_plan(_edit(shared_config(), path, value), SERVERS)


def test_khung_yeu_to_nho_hon_khung_trigger_thi_tu_choi_va_bang_hoac_lon_hon_thi_nhan() -> None:
    config = with_combo(shared_config())
    config["strategies"]["combo"]["trend"] = {"macd": "M20"}  # combo trigger M30, yếu tố ở M20 nhỏ hơn
    with pytest.raises(ValueError, match="macd on M20 is smaller than trigger timeframe M30"):
        configuration.parse_plan(config, SERVERS)
    for frame in ("M30", "M45", "H4"):
        config["strategies"]["combo"]["trend"] = {"macd": frame}
        assert configuration.parse_plan(config, SERVERS).strategies["combo"].trends == (("macd", frame),)


def test_moi_symbol_va_khung_trigger_chi_co_mot_chien_luoc() -> None:
    config = with_combo(shared_config())
    config["strategies"]["combo"]["trigger"] = "M10"
    config["strategies"]["combo"]["trend"] = {"macd": "M45"}
    with pytest.raises(ValueError, match="strategies 'ma_cross' and 'combo' both run US30 on M10.*exactly one strategy"):
        configuration.parse_plan(config, SERVERS)
    config["strategies"]["combo"]["symbols"] = []  # danh sách rỗng bị từ chối trước
    with pytest.raises(TypeError, match="non-empty list"):
        configuration.parse_plan(config, SERVERS)


def test_yeu_to_khai_ma_khong_chien_luoc_nao_tham_chieu_thi_tu_choi() -> None:
    config = shared_config()
    config["trends"]["break20"] = {"type": "break", "lookback": 20}
    with pytest.raises(ValueError, match="referenced by no strategy: break20"):
        configuration.parse_plan(config, SERVERS)


def test_ten_chien_luoc_va_yeu_to_khong_duoc_trung_ten_key_ket_qua() -> None:
    config = shared_config()
    config["trends"]["macross"] = {"type": "sma", "period": 5}  # MACROSS: cùng tên key với chiến lược ma_cross (bỏ gạch dưới, viết hoa)
    config["strategies"]["ma_cross"]["trend"]["macross"] = "M30"
    with pytest.raises(ValueError, match="same result name MACROSS"):
        configuration.parse_plan(config, SERVERS)
    config = shared_config()
    config["trends"]["MACD"] = config["trends"].pop("macd")
    with pytest.raises(ValueError, match="lowercase letters, digits and underscores"):
        configuration.parse_plan(config, SERVERS)


def test_tinh_chinh_ky_thuat_cua_redis_dat_trong_code_va_de_duoc_bang_config() -> None:
    config = shared_config()
    config["redis"]["reconnect_delay_seconds"] = 7
    assert configuration.parse_plan(config, SERVERS).reconnect_delay == 7.0
    config["redis"]["candles"]["source_max_bars"] = 700
    with pytest.raises(ValueError, match="exceeds the candles a source keeps"):
        configuration.parse_plan(config, SERVERS)


def test_timeframe_duration_la_noi_duy_nhat_doc_ma_khung() -> None:
    assert configuration.timeframe_duration("m10") == pd.Timedelta(minutes=10)
    assert configuration.timeframe_duration("H4") == pd.Timedelta(hours=4) and configuration.timeframe_duration("d") == pd.Timedelta(days=1)
    for bad in ("", None, "X5", "M0", "Mx"):
        with pytest.raises(ValueError, match="Unsupported timeframe"):
            configuration.timeframe_duration(bad)


# =============================================================================
# 2. Bootstrap và phần nạp lên Redis.
# =============================================================================


def _file_config(**changes: object) -> dict:
    config = shared_config(**changes)
    config["redis"] = {**config["redis"], "servers": copy.deepcopy(SERVERS), "store": dict(STORE)}
    return config


def test_bootstrap_doc_servers_va_noi_cat_config_voi_dia_chi_that() -> None:
    local = local_config()
    assert local["servers"] == SERVERS
    assert local["store"] == {"host": "127.0.0.1", "port": 6380, "password": "mat-khau-de", "db": 11, "key_prefix": "L_CONFIG", "keep_versions": 20}


def test_bootstrap_thieu_khoa_hoac_server_la_thi_loi() -> None:
    with pytest.raises(KeyError, match=r"redis\.store\.keep_versions"):
        configuration._bootstrap({"redis": {"servers": SERVERS, "store": {k: v for k, v in STORE.items() if k != "keep_versions"}}})
    with pytest.raises(ValueError, match=r"redis\.store\.server.*not declared"):
        configuration._bootstrap({"redis": {"servers": SERVERS, "store": {**STORE, "server": "khong-co"}}})
    with pytest.raises(ValueError, match="key_prefix.*must be configured"):
        configuration._bootstrap({"redis": {"servers": SERVERS, "store": {**STORE, "key_prefix": " "}}})
    with pytest.raises(KeyError, match=r"redis\.servers"):
        configuration._bootstrap({"redis": {"store": STORE}})
    with pytest.raises(TypeError, match="host, port, password"):
        configuration._bootstrap({"redis": {"servers": {"de": "x"}, "store": STORE}})


def test_phan_nap_len_redis_khong_co_servers_va_store_nen_khong_co_mat_khau() -> None:
    shared = configuration.shared_config(_file_config())
    assert "servers" not in shared["redis"] and "store" not in shared["redis"] and set(shared) == {"redis", "trends", "strategies", "live"}
    text = json.dumps(shared)
    assert "mat-khau" not in text and "10.11.12.8" not in text  # địa chỉ và mật khẩu Redis chỉ ở file yaml


def _write_yaml(tmp_path, config: dict):
    path = tmp_path / "des_config.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    return path


def test_upload_config_phien_ban_dau_la_1_noi_dung_khong_doi_thi_khong_tao_phien_ban_moi(tmp_path) -> None:
    client, path = FakeConfigRedis(), _write_yaml(tmp_path, _file_config())
    assert configuration.upload_config(path, client=client) == (1, True)
    assert configuration.upload_config(path, client=client) == (1, False)  # khởi động lại mà config không đổi
    stored = json.loads(client.hashes["L_CONFIG:1"]["config"])
    assert stored == configuration.shared_config(_file_config())
    changed = _file_config()
    changed["trends"]["sma20"]["period"] = 25
    assert configuration.upload_config(_write_yaml(tmp_path, changed), client=client) == (2, True)  # chỉnh config: phiên bản mới
    assert client.lists["L_CONFIG"] == ["1", "2"]


def test_config_sai_thi_khong_duoc_nap_len_redis(tmp_path) -> None:
    broken = _file_config()
    broken["strategies"]["ma_cross"]["params"]["fast_ma"] = 99
    client = FakeConfigRedis()
    with pytest.raises(ValueError, match="fast_ma"):
        configuration.upload_config(_write_yaml(tmp_path, broken), client=client)
    assert client.lists == {} and client.hashes == {} and client.evals == 0


def test_fetch_plan_doc_phien_ban_moi_nhat_va_chua_co_config_thi_bao_chay_load_config(tmp_path) -> None:
    client, local = FakeConfigRedis(), local_config()
    with pytest.raises(RuntimeError, match="--load-config"):
        configuration.fetch_plan(local, client=client)
    configuration.upload_config(_write_yaml(tmp_path, _file_config()), client=client)
    version, fetched = configuration.fetch_plan(local, client=client)
    assert version == 1 and fetched == plan()
    changed = _file_config()
    changed["strategies"]["ma_cross"]["max_shift_bars"] = 5
    configuration.upload_config(_write_yaml(tmp_path, changed), client=client)
    version, fetched = configuration.fetch_plan(local, client=client)
    assert version == 2 and fetched.strategies["ma_cross"].max_shift_bars == 5  # tiến trình đọc lại là dùng ngay phiên bản mới


# =============================================================================
# 3. Dựng việc của từng tiến trình.
# =============================================================================


def test_build_module_job_chien_luoc_chay_o_khung_trigger_va_do_nguon_nuoi() -> None:
    job = configuration.build_module_job(plan(og8=True), local_config(), "ma_cross", "default")
    assert job.module == "ma_cross" and job.kind == "strategy" and job.function is mac_strategy.detect
    assert job.pairs == (Pair("US30", "M10"), Pair("BTCUSD", "M10"))  # EURUSD, FR40 thuộc nguồn og8
    assert job.source == {
        "host": "127.0.0.1", "port": 6379, "password": "", "db": 10, "key_prefix": "L_CANDLE", "snapshot_bars": 800,
        "socket_timeout": 5.0, "connect_timeout": 5.0, "healthcheck_interval": 30,
    }
    assert job.sink["key_prefix"] == "L_MODSTR" and job.sink["retention_max_entries"] == 500 and job.sink["db"] == 11 and job.sink["password"] == "mat-khau-de"
    assert job.retention == {"M10": 600_000}  # 2 x 500 nến x 600 giây: HASH sống lâu hơn cửa sổ giữ
    assert job.warmup_bars == 300 and job.process_on_startup and job.params["fast_ma"] == 13 and job.store["key_prefix"] == "L_CONFIG"


def test_build_module_job_yeu_to_chay_o_khung_cua_cac_chien_luoc_tham_chieu_no() -> None:
    job = configuration.build_module_job(plan(og8=True), local_config(), "macd", "og8")
    assert job.kind == "trend" and job.function is trend_module.bias_macd and job.params == {"fast": 5, "slow": 25, "signal": 5} and job.sink["key_prefix"] == "L_MODTRE"
    assert job.pairs == (Pair("EURUSD", "M30"), Pair("FR40", "M30")) and job.source["host"] == "10.11.12.8" and job.retention == {"M30": 1_800_000}
    two_frames = configuration.build_module_job(configuration.parse_plan(with_combo(shared_config()), SERVERS), local_config(), "macd", "default")  # macd còn được combo tham chiếu ở khung H1
    assert two_frames.pairs == (Pair("US30", "M30"), Pair("BTCUSD", "M30"), Pair("US30", "H1"), Pair("BTCUSD", "H1")) and set(two_frames.lengths) == {"M30", "H1"}
    break20 = configuration.build_module_job(configuration.parse_plan(with_break20(with_combo(shared_config())), SERVERS), local_config(), "break20", "default")
    assert break20.pairs == (Pair("US30", "M45"), Pair("BTCUSD", "M45")) and break20.function is trend_module.bias_break and break20.params == {"lookback": 20}


def test_build_module_job_symbols_gioi_han_chia_viec_va_tu_choi_viec_khong_hop_le() -> None:
    assert configuration.build_module_job(plan(), local_config(), "sma20", "default", ["btcusd"]).pairs == (Pair("BTCUSD", "M30"),)
    with pytest.raises(ValueError, match="not a registered strategy or trend"):
        configuration.build_module_job(plan(), local_config(), "combo", "default")
    with pytest.raises(ValueError, match="candle source 'khac' is not declared"):
        configuration.build_module_job(plan(), local_config(), "macd", "khac")
    with pytest.raises(ValueError, match="no work for symbols DOGE"):
        configuration.build_module_job(plan(), local_config(), "macd", "default", ["DOGE"])
    with pytest.raises(ValueError, match="no pair to run for module macd on source og8"):
        configuration.build_module_job(plan(og8=True), local_config(), "macd", "og8", ["US30"])  # US30 không thuộc nguồn og8


def test_build_decision_job_dang_ky_o_tham_chieu_moi_yeu_to_va_dinh_tuyen_cac_su_kien_ve_o() -> None:
    job = configuration.build_decision_job(plan(og8=True), local_config(), "default")
    assert [(r.strategy, r.symbol, r.trigger_tf) for r in job.registrations] == [("ma_cross", "US30", "M10"), ("ma_cross", "BTCUSD", "M10")]
    registration = job.registrations[1]
    assert [(ref.name, ref.timeframe) for ref in registration.refs] == [("macd", "M30"), ("sma20", "M30")]
    for pair in (Pair("BTCUSD", "M10", "MACROSS"), Pair("BTCUSD", "M30", "MACD"), Pair("BTCUSD", "M30", "SMA20"), Pair("BTCUSD", "M10")):  # tín hiệu MA Cross, từng yếu tố trend, nến khung trigger
        assert job.routes[pair] == (registration,)
    assert job.candle_pairs == (Pair("BTCUSD", "M10"), Pair("US30", "M10"))
    assert job.strategy_pairs == (Pair("BTCUSD", "M10", "MACROSS"), Pair("US30", "M10", "MACROSS"))
    assert job.trend_pairs == (Pair("BTCUSD", "M30", "MACD"), Pair("BTCUSD", "M30", "SMA20"), Pair("US30", "M30", "MACD"), Pair("US30", "M30", "SMA20"))
    assert job.candle_source["snapshot_bars"] == 800 and job.strategy_source["snapshot_bars"] == 500 and job.strategy_source["modules"] is True and job.strategy_source["key_prefix"] == "L_MODSTR"
    assert job.trend_source["snapshot_bars"] == 500 and job.trend_source["modules"] is True and job.trend_source["key_prefix"] == "L_MODTRE"
    assert job.sink["key_prefix"] == "L_DE" and job.sink["channel_prefix"] == "L_CHANNEL" and job.sink["retention_seconds"] == 604800 and job.sink["price_decimals"] == {"EURUSD": 5}
    assert job.strategies["ma_cross"].max_shift_bars == 3 and job.strategies["ma_cross"].validity_seconds == 600
    assert [r.symbol for r in configuration.build_decision_job(plan(og8=True), local_config(), "og8").registrations] == ["EURUSD", "FR40"]


def test_build_decision_job_nhieu_chien_luoc_moi_chien_luoc_mot_khung_trigger_va_yeu_to_rieng() -> None:
    job = configuration.build_decision_job(configuration.parse_plan(with_combo(shared_config()), SERVERS), local_config(), "default")
    assert [(r.strategy, r.symbol, r.trigger_tf) for r in job.registrations] == [
        ("ma_cross", "US30", "M10"), ("ma_cross", "BTCUSD", "M10"), ("combo", "US30", "M30"), ("combo", "BTCUSD", "M30"),
    ]
    combo = job.registrations[3]
    assert [(ref.name, ref.timeframe) for ref in combo.refs] == [("macd", "H1")]
    assert job.routes[Pair("BTCUSD", "H1", "MACD")] == (combo,) and job.routes[Pair("BTCUSD", "M30", "COMBO")] == (combo,) and job.routes[Pair("BTCUSD", "M30")] == (combo,)
    assert job.strategies["combo"].order_type == "STOP" and job.strategies["ma_cross"].order_type == "MARKET"


def test_build_decision_job_khong_co_symbol_nao_cua_nguon_thi_loi() -> None:
    config = shared_config(og8=True)
    config["strategies"]["ma_cross"]["symbols"] = ["US30"]  # ma_cross không chạy symbol nào của nguồn og8
    with pytest.raises(ValueError, match="no symbol to decide for source og8"):
        configuration.build_decision_job(configuration.parse_plan(config, SERVERS), local_config(), "og8")


def test_ten_tien_trinh_trong_log() -> None:
    assert configuration._lane_name("runner", "og8", None, "ma_cross") == "runner_ma_cross_og8"
    assert configuration._lane_name("runner", "", None, "macd") == "runner_macd"
    assert configuration._lane_name("publisher", "", ["US30", "btcusd"]) == "publisher_btcusd_us30"


def test_build_decision_job_mang_clock_key_cua_nguon_va_runner_khong_can() -> None:
    job = configuration.build_decision_job(plan(og8=True), local_config(), "default")
    assert job.clock_key == "dfs:clock" and job.candle_source["db"] == 10
    assert not hasattr(configuration.build_module_job(plan(), local_config(), "ma_cross", "default"), "clock_key")


def test_virtual_now_doc_gio_ao_o_db_nguon_moi_lan_goi_va_chua_co_thi_bao_loi() -> None:
    class _Engine:
        def __init__(self) -> None:
            self.value: str | None = None

        def read_key(self, key: str) -> str | None:
            assert key == "dfs:clock"
            return self.value

    engine = _Engine()
    now = configuration._virtual_now(engine, "dfs:clock")
    with pytest.raises(RuntimeError, match="simulated clock 'dfs:clock' is not set"):
        now()
    engine.value = "2026-01-05 09:30:00"
    assert now() == pd.Timestamp("2026-01-05 09:30:00", tz="UTC")
    engine.value = "2026-01-05 09:40:00"  # giờ ảo chạy tiếp: lần gọi sau thấy giờ mới
    assert now() == pd.Timestamp("2026-01-05 09:40:00", tz="UTC")


def test_hieu_luc_khai_bang_so_nen_thi_theo_do_dai_khung_trigger_con_khai_bang_giay_thi_giu_nguyen() -> None:
    plan = configuration.parse_plan(with_combo(shared_config()), SERVERS)
    assert plan.strategies["combo"].trigger == "M30" and plan.strategies["combo"].validity_seconds == 3 * 1800  # 3 nến M30 = 90 phút
    assert plan.strategies["ma_cross"].validity_seconds == 600  # khai bằng giây: giữ nguyên
    for trigger, seconds in (("M45", 3 * 2700), ("H1", 3 * 3600), ("H2", 3 * 7200)):
        config = with_combo(shared_config())
        config["strategies"]["combo"]["trigger"] = trigger
        config["strategies"]["combo"]["trend"] = {"macd": trigger}
        assert configuration.parse_plan(config, SERVERS).strategies["combo"].validity_seconds == seconds  # 3 nến của chính khung trigger


def test_hieu_luc_phai_khai_dung_mot_trong_hai_cach_va_la_so_nguyen_duong() -> None:
    both = with_combo(shared_config())
    both["strategies"]["combo"]["validity_seconds"] = 1800
    with pytest.raises(ValueError, match="both validity_seconds and validity_bars"):
        configuration.parse_plan(both, SERVERS)
    neither = with_combo(shared_config())
    del neither["strategies"]["combo"]["validity_bars"]
    with pytest.raises(KeyError, match=r"validity_seconds' hoặc"):
        configuration.parse_plan(neither, SERVERS)
    for bad in (0, -1, 1.5, True):
        config = with_combo(shared_config())
        config["strategies"]["combo"]["validity_bars"] = bad
        with pytest.raises(ValueError, match=r"validity_bars.*integer >= 1"):
            configuration.parse_plan(config, SERVERS)
