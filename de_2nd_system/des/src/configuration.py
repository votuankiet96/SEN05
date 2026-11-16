"""
Đọc config, đăng ký module và điều phối tiến trình cho DE.

Mô tả:
    1. CONFIG CÓ PHIÊN BẢN TRÊN REDIS. des_config.yaml là NGUỒN; mỗi tiến trình khi khởi động nạp nó lên Redis (``upload_config``: chỉ tạo phiên bản mới khi nội dung khác phiên bản mới nhất) rồi đọc config từ Redis
       (``fetch_plan``), nên đổi config rồi chạy lại ``main --load-config`` là mọi tiến trình đang chạy dùng ngay (``_Reload``), không cần restart. Hai mục CHỈ ở file yaml, không nạp lên Redis: ``redis.servers``
       (địa chỉ và MẬT KHẨU mọi Redis) và ``redis.store`` (nơi cất config trên Redis: phải biết trước khi đọc được config). Thiếu khoá nào thì ``parse_plan`` raise KeyError nêu đúng đường dẫn; không có giá trị ngầm
       (trừ vài tinh chỉnh kỹ thuật thuần của Redis ở ``_REDIS_DEFAULTS``). Config sai thì không được nạp lên Redis.
    2. ĐĂNG KÝ chỉ bằng config, không viết cứng tên nào trong mã. ``trends:`` khai các yếu tố trend (tên -> ``type`` là hàm ``bias_<type>`` trong de_module/trend.py cùng tham số); ``strategies:`` khai các chiến lược
       (tên -> ``file`` là file trong de_module/ có hàm ``detect``, khung trigger, symbol, tham số của ``detect``, atr/dời/hiệu lực, loại lệnh, và các yếu tố trend tham chiếu kèm khung của từng yếu tố). Tham số được kiểm
       theo chữ ký hàm và bằng một lần chạy thử trên nến giả; tên thừa, thiếu hay vô lý thì config bị từ chối. Mỗi (symbol, khung trigger) chỉ có một chiến lược. Sổ đăng ký các ô quyết định là của decision_engine.register.
    3. ĐIỀU PHỐI HAI loại tiến trình, dựng việc từ config trước khi chạm Redis (``build_module_job`` / ``build_decision_job``), vòng lặp sự kiện, ``run_runner`` / ``run_publisher``:
         * runner, mỗi (tên chiến lược hoặc yếu tố, nguồn nến) một tiến trình: nến AEN hoặc OG8 -> chạy hàm đã đăng ký (``detect`` của file chiến lược, hoặc hàm ``bias_<type>``) -> kết quả vào db11 (``process_module``);
         * publisher, mỗi nguồn nến một tiến trình: kết quả chiến lược và các yếu tố ở db11 + nến khung trigger -> luật epoch, dời, hủy (decision_engine.decide) -> tín hiệu cuối vào db11 và báo kênh (``process_decision``).
       Các tiến trình chỉ trao đổi qua Redis; chết hay restart tiến trình này không kéo theo tiến trình kia.

Không giữ logic tín hiệu hay quyết định nào: chỉ diễn giải config, nối các file lại với nhau và chạy vòng lặp.
KHÁC DE live đúng ở chỗ nguồn và giờ: nến lấy từ db do DFS phát, "bây giờ" của publisher là giờ ảo ``clock_key`` của nguồn (``_virtual_now``) thay cho giờ máy (nến là quá khứ nên giờ máy làm mọi tín hiệu quá hạn). Mọi thứ khác giống DE.
"""

from __future__ import annotations

import importlib
import inspect
import json
import logging
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import redis
import yaml

from des.src.data_engine import STAMP_FORMAT, DataEngine, Pair
from des.src.de_module import trend as trend_module
from des.src.decision_engine import Registration, TrendRef, decide, register
from des.src.redis_connector import (
    KeyspaceListener,
    keep_connected,
    make_client,
    read_config,
    write_config,
)
from des.src.util import (
    ORDER_TYPES,
    ModuleWriter,
    SignalWriter,
    bias_rows,
    module_token,
    signal_rows,
)

LOGGER = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent / "des_config.yaml"
STRATEGY_PACKAGE = "des.src.de_module"  # nơi chứa các file chiến lược (có hàm detect) và trend.py (các hàm bias_<tên>)
BIAS_PREFIX = "bias_"
DEFAULT_SOURCE = "default"
# Tinh chỉnh kỹ thuật thuần của Redis (chưa từng cần operator đổi); khai thêm ở ``redis:`` của des_config.yaml thì đè lên.
_REDIS_DEFAULTS: dict[str, Any] = {
    "socket_timeout_seconds": 5,
    "socket_connect_timeout_seconds": 5,
    "healthcheck_interval_seconds": 30,
    "reconnect_delay_seconds": 3,
}
_SOURCE_MAX_BARS = 1200  # số nến nguồn (Core/AEN, OG8) giữ mỗi cặp; ``redis.candles.source_max_bars`` đè lên
_MAX_SIGNAL_TTL_SECONDS = 604800  # 7 ngày: hạn lưu TỐI ĐA của một tín hiệu cuối (chủ hệ chốt 09/11/2026); khai quá thì từ chối lúc nạp config
_IDLE_WAIT_SECONDS = 1.0  # runner chờ event tối đa chừng này mỗi vòng (cũng là độ trễ phát hiện config mới)
_DECISION_WAIT_SECONDS = 0.1  # publisher nghe hai Redis nên chia nhỏ thời gian chờ cho từng bên


def require(cfg: dict[str, Any], key: str, path: str) -> Any:
    """Đọc bắt buộc 1 key: không có giá trị mặc định nào trong code. Thiếu key sẽ raise ngay, chỉ rõ đúng đường dẫn cần điền."""
    if key not in cfg:
        raise KeyError(f"des_config.yaml thiếu '{path}'")
    return cfg[key]


def _int(cfg: dict[str, Any], key: str, path: str, minimum: int) -> int:
    """Đọc bắt buộc một số nguyên (không phải bool) không nhỏ hơn ``minimum``."""
    value = require(cfg, key, f"{path}.{key}")
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"des_config.yaml: '{path}.{key}' must be an integer >= {minimum}")
    return value


def timeframe_duration(timeframe: object) -> pd.Timedelta:
    """Đổi mã khung (M10, M45, H3, D1, W...) thành độ dài chính xác. Mã sai thì ValueError. Nơi DUY NHẤT trong DE đọc mã khung: các file khác nhận độ dài (Timedelta) từ đây."""
    code = str(timeframe or "").strip().upper()
    units = {"M": "minutes", "H": "hours", "D": "days", "W": "weeks"}
    unit = units.get(code[:1])
    amount_text = code[1:] or "1"
    if unit is None or not amount_text.isdigit() or int(amount_text) <= 0:
        raise ValueError(f"Unsupported timeframe '{timeframe}'")
    return pd.Timedelta(**{unit: int(amount_text)})


def _names(raw: Any, path: str) -> tuple[str, ...]:
    """Đọc một danh sách tên (symbol hoặc mã khung): không rỗng, không có tên rỗng, không trùng; chuẩn hoá chữ hoa."""
    if not isinstance(raw, (list, tuple)) or not raw:
        raise TypeError(f"des_config.yaml: '{path}' must be a non-empty list.")
    names = tuple(str(item).strip().upper() for item in raw)
    if any(not name for name in names):
        raise ValueError(f"des_config.yaml: '{path}' contains an empty name.")
    duplicated = sorted({name for name in names if names.count(name) > 1})
    if duplicated:
        raise ValueError(f"des_config.yaml: '{path}' lists a name twice: {', '.join(duplicated)}.")
    return names


# -- bootstrap: phần CHỈ có ở des_config.yaml -----------------------------------------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    if not isinstance(loaded, dict):
        raise TypeError(f"{path.name} must be a mapping")
    return loaded


def _servers(redis_cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Đọc ``redis.servers``: mọi Redis mà DE nói chuyện tới (các nguồn nến và Redis đầu ra), mỗi cái khai MỘT lần với đủ host, port, password (password "" = không AUTH)."""
    raw = require(redis_cfg, "servers", "redis.servers")
    if not isinstance(raw, dict) or not raw:
        raise TypeError("des_config.yaml: 'redis.servers' must map server name -> {host, port, password}")
    for name, block in raw.items():
        if not isinstance(block, dict):
            raise TypeError(f"des_config.yaml: 'redis.servers.{name}' must be a mapping of host, port, password")
        for key in ("host", "port", "password"):
            require(block, key, f"redis.servers.{name}.{key}")
    return raw


def _endpoint(servers: dict[str, dict[str, Any]], name: object, path: str) -> dict[str, Any]:
    """Địa chỉ (host, port, password) của server ``name`` khai ở ``redis.servers``; ``path`` là khoá đang trỏ tới nó (để báo lỗi đúng chỗ)."""
    if name not in servers:
        raise ValueError(f"des_config.yaml: '{path}' names server '{name}', which is not declared in redis.servers (declared: {', '.join(map(str, servers))})")
    block = servers[name]
    return {"host": str(block["host"]), "port": int(block["port"]), "password": str(block["password"] or "")}


def _bootstrap(file_config: dict[str, Any]) -> dict[str, Any]:
    """``{"servers": ..., "store": ...}``: ``redis.servers`` và ``redis.store`` (server, db, key_prefix, keep_versions của nơi cất config trên Redis)."""
    redis_cfg = require(file_config, "redis", "redis")
    servers = _servers(redis_cfg)
    store_cfg = require(redis_cfg, "store", "redis.store")
    for key in ("server", "db", "key_prefix", "keep_versions"):
        require(store_cfg, key, f"redis.store.{key}")
    prefix = str(store_cfg["key_prefix"]).strip()
    if not prefix:
        raise ValueError("des_config.yaml: 'redis.store.key_prefix' must be configured")
    store = {
        **_endpoint(servers, store_cfg["server"], "redis.store.server"),
        "db": _int(store_cfg, "db", "redis.store", 0),
        "key_prefix": prefix,
        "keep_versions": _int(store_cfg, "keep_versions", "redis.store", 1),
    }
    return {"servers": servers, "store": store}


def load_bootstrap(path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Phần bootstrap của des_config.yaml (xem ``_bootstrap``): đủ để nối tới Redis và đọc config từ đó."""
    return _bootstrap(_load_yaml(path))


def shared_config(file_config: dict[str, Any]) -> dict[str, Any]:
    """Phần của des_config.yaml được nạp lên Redis: mọi thứ trừ ``redis.servers`` (mật khẩu) và ``redis.store`` (chỗ cất config)."""
    redis_cfg = {key: value for key, value in require(file_config, "redis", "redis").items() if key not in ("servers", "store")}
    return {**{key: value for key, value in file_config.items() if key != "redis"}, "redis": redis_cfg}


def _store_client(store: dict[str, Any]) -> redis.Redis:
    return make_client(host=store["host"], port=store["port"], password=store["password"], db=store["db"])


def upload_config(path: Path = CONFIG_PATH, *, client: redis.Redis | None = None) -> tuple[int, bool]:
    """Nạp config trong des_config.yaml lên Redis. Config được kiểm đầy đủ (``parse_plan``) TRƯỚC khi nạp: config sai thì raise và không có gì lên Redis. Trả về ``(số phiên bản đang có hiệu lực,
    đã tạo phiên bản mới?)``: nội dung không đổi so với phiên bản mới nhất thì không tạo phiên bản."""
    file_config = _load_yaml(path)
    local = _bootstrap(file_config)
    shared = shared_config(file_config)
    parse_plan(shared, local["servers"])
    text = json.dumps(shared, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    store = local["store"]
    connection = client if client is not None else _store_client(store)
    try:
        return write_config(connection, key_prefix=store["key_prefix"], text=text, keep_versions=store["keep_versions"])
    finally:
        if client is None:
            connection.close()


def fetch_plan(local: dict[str, Any], *, client: redis.Redis | None = None) -> tuple[int, Plan]:
    """Đọc phiên bản config MỚI NHẤT trên Redis và kiểm nó. Chưa có phiên bản nào thì RuntimeError. Trả về ``(số phiên bản, Plan)``."""
    store = local["store"]
    connection = client if client is not None else _store_client(store)
    try:
        latest = read_config(connection, key_prefix=store["key_prefix"])
    finally:
        if client is None:
            connection.close()
    if latest is None:
        raise RuntimeError("Redis chưa có config nào: chạy `python -m des.src.main --load-config` để nạp des_config.yaml")
    return latest[0], parse_plan(json.loads(latest[1]), local["servers"])


# -- Plan: config đã kiểm -------------------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Source:
    """Một nguồn nến: Redis nguồn (``endpoint``), db, tiền tố key, key giờ ảo (``clock_key``, vd ``dfs:clock`` do DFS công bố ở chính db nguồn) và các symbol nó nuôi."""

    endpoint: dict[str, Any]
    db: int
    key_prefix: str
    clock_key: str
    symbols: tuple[str, ...]


@dataclass(frozen=True)
class TrendFactor:
    """Một yếu tố trend đã đăng ký (``trends.<tên>``): hàm ``bias_<type>`` của de_module/trend.py và tham số của nó (khoá viết thường)."""

    function: Callable[..., Any]
    params: dict[str, Any]


@dataclass(frozen=True)
class Strategy:
    """Một chiến lược đã đăng ký (``strategies.<tên>``): hàm ``detect`` của file chiến lược và tham số của nó, khung trigger, symbol, tham số quyết định (atr, số nến dời, hiệu lực), loại lệnh và các yếu tố trend
    tham chiếu: ``trends`` là các cặp (tên yếu tố, khung của yếu tố)."""

    detect: Callable[..., pd.DataFrame]
    params: dict[str, Any]
    trigger: str
    symbols: tuple[str, ...]
    atr_period: int
    max_shift_bars: int
    validity_seconds: int  # hiệu lực tính bằng GIÂY; config khai ``validity_seconds`` hoặc ``validity_bars`` (số nến của khung trigger, đổi ra giây ở ``_validity_seconds``)
    order_type: str
    trends: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Plan:
    """Toàn bộ config đã kiểm của một phiên bản."""

    enabled: bool
    log_level: str
    reconnect_delay: float
    timeouts: dict[str, Any]
    snapshot_bars: int
    warmup_bars: int
    process_on_startup: bool
    sources: dict[str, Source]
    output: dict[str, Any]
    strategies: dict[str, Strategy]
    trends: dict[str, TrendFactor]
    price_decimals: dict[str, int]


def _sources(candles_cfg: dict[str, Any], servers: dict[str, dict[str, Any]]) -> dict[str, Source]:
    """Đọc ``redis.candles.sources``: mọi nguồn nến, mỗi nguồn cùng một hình dạng (server, db, key_prefix, symbols). Tên là chữ thường + số vì nó là hậu tố tên tiến trình (vd ``des-runner@ma_cross.og8``);
    bắt buộc có nguồn ``default``. Mỗi symbol chỉ có MỘT nguồn nến: hai nguồn cùng nuôi một symbol là cấu hình sai (cùng khoá đầu ra, hai tiến trình tranh nhau ghi)."""
    raw = require(candles_cfg, "sources", "redis.candles.sources")
    if not isinstance(raw, dict):
        raise TypeError("des_config.yaml: 'redis.candles.sources' must map source name -> {server, db, key_prefix, symbols}")
    sources: dict[str, Source] = {}
    owner: dict[str, str] = {}
    for raw_name, block in raw.items():
        name = str(raw_name).strip().lower()
        path = f"redis.candles.sources.{name}"
        if not re.fullmatch(r"[a-z0-9]+", name):
            raise ValueError(f"des_config.yaml: 'redis.candles.sources.{raw_name}': a source name uses lowercase letters and digits only (it ends the process name, e.g. des-runner@ma_cross.og8)")
        if name in sources:
            raise ValueError(f"des_config.yaml: 'redis.candles.sources' lists source '{name}' twice")
        if not isinstance(block, dict):
            raise TypeError(f"des_config.yaml: '{path}' must be a mapping of server, db, key_prefix, symbols")
        for key in ("server", "db", "key_prefix", "clock_key", "symbols"):
            require(block, key, f"{path}.{key}")
        symbols = _names(block["symbols"], f"{path}.symbols")
        for symbol in symbols:
            if symbol in owner:
                raise ValueError(f"des_config.yaml: symbol {symbol} is listed by both '{owner[symbol]}' and '{path}': each symbol needs exactly one candle source")
            owner[symbol] = path
        sources[name] = Source(_endpoint(servers, block["server"], f"{path}.server"), _int(block, "db", path, 0), str(block["key_prefix"]), str(block["clock_key"]), symbols)
    if DEFAULT_SOURCE not in sources:
        raise KeyError(f"des_config.yaml thiếu 'redis.candles.sources.{DEFAULT_SOURCE}'")
    return sources


def _output(redis_cfg: dict[str, Any], servers: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Đọc ``redis.output``: Redis đầu ra của DE và MỘT db duy nhất cho mọi đầu ra (kết quả chiến lược ``strategy_prefix``, kết quả yếu tố trend ``trend_prefix``, tín hiệu cuối ``signal_prefix``, kênh ``channel_prefix``)."""
    cfg = require(redis_cfg, "output", "redis.output")
    for key in ("server", "db", "strategy_prefix", "trend_prefix", "signal_prefix", "channel_prefix", "retention_seconds", "retention_max_entries"):
        require(cfg, key, f"redis.output.{key}")
    for key in ("strategy_prefix", "trend_prefix", "signal_prefix", "channel_prefix"):
        if not str(cfg[key]).strip():
            raise ValueError(f"des_config.yaml: 'redis.output.{key}' must be configured")
    key_prefixes = [str(cfg[key]).strip() for key in ("strategy_prefix", "trend_prefix", "signal_prefix")]
    if len(set(key_prefixes)) != len(key_prefixes):
        raise ValueError("des_config.yaml: 'redis.output.strategy_prefix', 'trend_prefix' and 'signal_prefix' must differ from each other (they name different keys in the same db)")
    retention = _int(cfg, "retention_seconds", "redis.output", 1)
    if retention > _MAX_SIGNAL_TTL_SECONDS:
        raise ValueError(f"redis.output.retention_seconds ({retention}) exceeds the 7-day maximum a signal may live ({_MAX_SIGNAL_TTL_SECONDS} seconds)")
    return {
        **_endpoint(servers, cfg["server"], "redis.output.server"),
        "db": _int(cfg, "db", "redis.output", 0),
        "strategy_prefix": str(cfg["strategy_prefix"]).strip(),
        "trend_prefix": str(cfg["trend_prefix"]).strip(),
        "signal_prefix": str(cfg["signal_prefix"]).strip(),
        "channel_prefix": str(cfg["channel_prefix"]).strip(),
        "retention_seconds": retention,
        "retention_max_entries": _int(cfg, "retention_max_entries", "redis.output", 1),
    }


def _name(raw: object, path: str) -> str:
    """Tên một chiến lược hoặc yếu tố trend (khoá config): chữ thường, số và gạch dưới, vì nó là một phần tên tiến trình runner (``des-runner@<tên>.<nguồn>``) và (bỏ gạch dưới, viết hoa) là tên trong key kết quả."""
    name = str(raw).strip()
    if not re.fullmatch(r"[a-z0-9_]+", name):
        raise ValueError(f"des_config.yaml: '{path}.{raw}': a name uses lowercase letters, digits and underscores only")
    return name


def _symbols(cfg: dict[str, Any], path: str, operating: tuple[str, ...]) -> tuple[str, ...]:
    """Symbol một chiến lược chạy: danh sách symbol hoặc ``all`` (mọi symbol của các nguồn nến)."""
    raw = require(cfg, "symbols", f"{path}.symbols")
    if isinstance(raw, str):
        if raw.strip().lower() != "all":
            raise ValueError(f"des_config.yaml: '{path}.symbols' must be a list of symbols or the word 'all'")
        return operating
    symbols = _names(raw, f"{path}.symbols")
    unknown = [symbol for symbol in symbols if symbol not in operating]
    if unknown:
        raise ValueError(f"des_config.yaml: '{path}.symbols' lists symbols no candle source feeds: {', '.join(unknown)}")
    return symbols


def _sample_candles(count: int = 120) -> pd.DataFrame:
    """Bộ nến giả xác định (dao động quanh một mức) để chạy thử hàm của chiến lược hoặc yếu tố lúc kiểm config: tham số vô lý (chu kỳ nhanh >= chậm...) làm hàm raise ValueError ngay lúc nạp config."""
    index = np.arange(count, dtype=float)
    close = 100.0 + 5.0 * np.sin(index / 7.0) + np.sin(index / 2.3)
    return pd.DataFrame(
        {"bartime": pd.date_range("2026-01-01", periods=count, freq="h"), "open": close - 0.2, "high": close + 1.0, "low": close - 1.0, "close": close, "volume": np.nan}
    )


def _bind_params(function: Callable[..., Any], raw: Any, path: str) -> dict[str, Any]:
    """Tham số của một hàm đã đăng ký: ánh xạ tên -> số nguyên >= 1, khớp chữ ký hàm (thiếu hoặc thừa là lỗi) và hàm chạy được trên nến giả. Trả về bản chuẩn hoá (khoá viết thường)."""
    if not isinstance(raw, dict):
        raise TypeError(f"des_config.yaml: '{path}' must be a mapping of parameters")
    params = {str(key).strip().lower(): value for key, value in raw.items()}
    for key, value in params.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"des_config.yaml: '{path}.{key}' must be an integer >= 1")
    try:
        inspect.signature(function).bind(None, **params)
    except TypeError as exc:
        raise ValueError(f"des_config.yaml: '{path}' does not match {function.__name__}(): {exc}") from exc
    try:
        function(_sample_candles(), **params)
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError(f"des_config.yaml: '{path}': {exc}") from exc
    return params


def _trends(config: dict[str, Any]) -> dict[str, TrendFactor]:
    """Đọc ``trends``: mỗi yếu tố là tên -> {type, tham số}; ``type`` là hàm ``bias_<type>`` trong de_module/trend.py."""
    raw = require(config, "trends", "trends")
    if not isinstance(raw, dict) or not raw:
        raise TypeError("des_config.yaml: 'trends' must map trend name -> {type, parameters}")
    trends: dict[str, TrendFactor] = {}
    for raw_name, block in raw.items():
        name = _name(raw_name, "trends")
        path = f"trends.{name}"
        if not isinstance(block, dict):
            raise TypeError(f"des_config.yaml: '{path}' must be a mapping of type and parameters")
        kind = str(require(block, "type", f"{path}.type")).strip().lower()
        function = getattr(trend_module, f"{BIAS_PREFIX}{kind}", None)
        if function is None:
            raise ValueError(f"des_config.yaml: '{path}.type' names '{kind}', but de_module/trend.py has no function {BIAS_PREFIX}{kind}")
        trends[name] = TrendFactor(function, _bind_params(function, {key: value for key, value in block.items() if key != "type"}, path))
    return trends


def _validity_seconds(block: dict[str, Any], path: str, trigger_length: pd.Timedelta) -> int:
    """Hạn hiệu lực của tín hiệu, đổi ra GIÂY, tính từ giờ đóng của nến phát. Khai ĐÚNG MỘT trong hai cách: ``validity_seconds`` là số giây cố định (vd MA Cross 600); ``validity_bars`` là số NẾN của khung trigger
    (vd Combo 3 nến: khung M30 là 90 phút, khung H1 là 3 giờ), nên đổi khung trigger thì hạn tự đổi theo."""
    has_seconds, has_bars = "validity_seconds" in block, "validity_bars" in block
    if not has_seconds and not has_bars:
        raise KeyError(f"des_config.yaml thiếu '{path}.validity_seconds' hoặc '{path}.validity_bars' (khai đúng một trong hai)")
    if has_seconds and has_bars:
        raise ValueError(f"des_config.yaml: '{path}' declares both validity_seconds and validity_bars: use exactly one")
    if has_seconds:
        return _int(block, "validity_seconds", path, 1)
    return _int(block, "validity_bars", path, 1) * int(trigger_length.total_seconds())


def _strategies(
    config: dict[str, Any], trends: dict[str, TrendFactor], allowed: tuple[str, ...], operating: tuple[str, ...], snapshot_bars: int
) -> dict[str, Strategy]:
    """Đọc ``strategies``: mỗi chiến lược là tên -> {file, trigger, symbols, params, atr_period, max_shift_bars, validity_seconds hoặc validity_bars, order_type, trend}. Mỗi (symbol, khung trigger) chỉ được một chiến lược,
    mỗi yếu tố khai ở ``trends`` phải có chiến lược tham chiếu, và tên chiến lược/yếu tố không được trùng nhau (kể cả sau khi bỏ gạch dưới và viết hoa làm tên key)."""
    raw = require(config, "strategies", "strategies")
    if not isinstance(raw, dict) or not raw:
        raise TypeError("des_config.yaml: 'strategies' must map strategy name -> its settings")
    strategies: dict[str, Strategy] = {}
    owner: dict[tuple[str, str], str] = {}
    for raw_name, block in raw.items():
        name = _name(raw_name, "strategies")
        path = f"strategies.{name}"
        if not isinstance(block, dict):
            raise TypeError(f"des_config.yaml: '{path}' must be a mapping")
        file = str(require(block, "file", f"{path}.file")).strip()
        if not re.fullmatch(r"[a-z0-9_]+", file):
            raise ValueError(f"des_config.yaml: '{path}.file' must be a file name in de_module/ (lowercase letters, digits, underscores)")
        try:
            module = importlib.import_module(f"{STRATEGY_PACKAGE}.{file}")
        except ModuleNotFoundError as exc:
            raise ValueError(f"des_config.yaml: '{path}.file' names '{file}', but there is no de_module/{file}.py") from exc
        detect_function = getattr(module, "detect", None)
        if detect_function is None:
            raise ValueError(f"des_config.yaml: de_module/{file}.py has no function detect (named by '{path}.file')")
        trigger = str(require(block, "trigger", f"{path}.trigger")).strip().upper()
        trigger_length = timeframe_duration(trigger)
        if trigger not in allowed:
            raise ValueError(f"des_config.yaml: '{path}.trigger' is {trigger}, which is not in redis.candles.timeframes ({', '.join(allowed)})")
        symbols = _symbols(block, path, operating)
        params = _bind_params(detect_function, require(block, "params", f"{path}.params"), f"{path}.params")
        shift = _int(block, "max_shift_bars", path, 0)
        if shift >= snapshot_bars:
            raise ValueError(f"des_config.yaml: '{path}.max_shift_bars' ({shift}) must be smaller than redis.candles.snapshot_bars ({snapshot_bars})")
        order_type = str(require(block, "order_type", f"{path}.order_type")).strip().upper()
        if order_type not in ORDER_TYPES:
            raise ValueError(f"des_config.yaml: '{path}.order_type' must be one of {', '.join(ORDER_TYPES)}, got '{order_type}'")
        references = require(block, "trend", f"{path}.trend")
        if not isinstance(references, dict) or not references:
            raise TypeError(f"des_config.yaml: '{path}.trend' must map trend name -> timeframe (at least one)")
        refs: list[tuple[str, str]] = []
        for raw_ref, raw_frame in references.items():
            ref_name, frame = str(raw_ref).strip(), str(raw_frame).strip().upper()
            if ref_name not in trends:
                raise ValueError(f"des_config.yaml: '{path}.trend' references '{ref_name}', which is not declared in trends (declared: {', '.join(trends)})")
            timeframe_duration(frame)
            if frame not in allowed:
                raise ValueError(f"des_config.yaml: '{path}.trend.{ref_name}' is {frame}, which is not in redis.candles.timeframes ({', '.join(allowed)})")
            refs.append((ref_name, frame))
        register(name, ("-",), trigger, trigger_length, [TrendRef(ref_name, frame, timeframe_duration(frame)) for ref_name, frame in refs])  # luật khung: yếu tố >= khung trigger
        for symbol in symbols:
            if (symbol, trigger) in owner:
                raise ValueError(
                    f"des_config.yaml: strategies '{owner[(symbol, trigger)]}' and '{name}' both run {symbol} on {trigger}: each (symbol, trigger timeframe) needs exactly one strategy "
                    f"(the final signal key L_DE_{symbol}_{trigger} has no strategy name)"
                )
            owner[(symbol, trigger)] = name
        strategies[name] = Strategy(
            detect_function, params, trigger, symbols, _int(block, "atr_period", path, 1), shift, _validity_seconds(block, path, trigger_length), order_type, tuple(refs)
        )
    tokens: dict[str, str] = {}
    for name in (*strategies, *trends):
        if module_token(name) in tokens:
            raise ValueError(f"des_config.yaml: '{name}' and '{tokens[module_token(name)]}' give the same result name {module_token(name)}: strategy and trend names must stay different")
        tokens[module_token(name)] = name
    unused = [name for name in trends if not any(name == ref for each in strategies.values() for ref, _ in each.trends)]
    if unused:
        raise ValueError(f"des_config.yaml: trends declared but referenced by no strategy: {', '.join(unused)}")
    return strategies


def _price_decimals(live: dict[str, Any], operating: tuple[str, ...]) -> dict[str, int]:
    """Đọc ``live.price_decimals`` (tuỳ chọn): symbol -> số lẻ làm tròn entry/atr của tín hiệu (forex cần 5 hoặc 3; symbol không khai dùng 2). Symbol không thuộc DE hoặc số lẻ ngoài 0..8 thì ValueError,
    để gõ sai tên symbol không âm thầm làm tròn forex về 2 số lẻ."""
    raw = live.get("price_decimals") or {}
    if not isinstance(raw, dict):
        raise TypeError("des_config.yaml: 'live.price_decimals' must map symbol -> number of decimals")
    decimals: dict[str, int] = {}
    for raw_symbol, digits in raw.items():
        symbol = str(raw_symbol).strip().upper()
        if symbol not in operating:
            raise ValueError(f"des_config.yaml: 'live.price_decimals.{raw_symbol}' is not an operating symbol")
        if symbol in decimals:
            raise ValueError(f"des_config.yaml: 'live.price_decimals' lists symbol {symbol} twice")
        if isinstance(digits, bool) or not isinstance(digits, int) or not 0 <= digits <= 8:
            raise ValueError(f"des_config.yaml: 'live.price_decimals.{raw_symbol}' must be an integer between 0 and 8")
        decimals[symbol] = digits
    return decimals


def parse_plan(config: dict[str, Any], servers: dict[str, dict[str, Any]]) -> Plan:
    """Đọc và kiểm TOÀN BỘ config (phần được nạp lên Redis) cùng địa chỉ các Redis. Sai thì KeyError/TypeError/ValueError nêu đúng đường dẫn."""
    redis_cfg = require(config, "redis", "redis")
    enabled = require(redis_cfg, "enabled", "redis.enabled")
    if not isinstance(enabled, bool):
        raise TypeError("redis.enabled must be true or false")
    top = {**_REDIS_DEFAULTS, **redis_cfg}
    candles_cfg = require(redis_cfg, "candles", "redis.candles")
    timeframes = _names(require(candles_cfg, "timeframes", "redis.candles.timeframes"), "redis.candles.timeframes")
    for timeframe in timeframes:
        timeframe_duration(timeframe)
    snapshot_bars = _int(candles_cfg, "snapshot_bars", "redis.candles", 1)
    warmup_bars = _int(candles_cfg, "warmup_bars", "redis.candles", 0)
    source_max_bars = int(candles_cfg.get("source_max_bars", _SOURCE_MAX_BARS))
    if snapshot_bars + warmup_bars > source_max_bars:
        raise ValueError(
            f"redis.candles.snapshot_bars + warmup_bars ({snapshot_bars + warmup_bars}) exceeds the candles a source keeps ({source_max_bars}): the candle source never holds that many candles"
        )
    on_startup = require(candles_cfg, "process_on_startup", "redis.candles.process_on_startup")
    if not isinstance(on_startup, bool):
        raise TypeError("redis.candles.process_on_startup must be true or false")
    sources = _sources(candles_cfg, servers)
    operating = tuple(symbol for source in sources.values() for symbol in source.symbols)
    output = _output(redis_cfg, servers)

    trends = _trends(config)
    strategies = _strategies(config, trends, timeframes, operating, snapshot_bars)

    live = require(config, "live", "live")
    log_level = str(require(live, "log_level", "live.log_level")).upper()
    if log_level not in logging.getLevelNamesMapping():
        raise ValueError(f"des_config.yaml: 'live.log_level' is not a logging level: {log_level}")
    return Plan(
        enabled=enabled,
        log_level=log_level,
        reconnect_delay=float(top["reconnect_delay_seconds"]),
        timeouts={
            "socket_timeout": float(top["socket_timeout_seconds"]),
            "connect_timeout": float(top["socket_connect_timeout_seconds"]),
            "healthcheck_interval": int(top["healthcheck_interval_seconds"]),
        },
        snapshot_bars=snapshot_bars,
        warmup_bars=warmup_bars,
        process_on_startup=on_startup,
        sources=sources,
        output=output,
        strategies=strategies,
        trends=trends,
        price_decimals=_price_decimals(live, operating),
    )


# -- việc của từng tiến trình ---------------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ModuleJob:
    """Việc của một tiến trình runner: chạy MỘT chiến lược hoặc MỘT yếu tố trend (``kind`` là strategy hoặc trend, ``function`` là hàm đã đăng ký) trên nến của MỘT nguồn. ``source`` là tham số ``DataEngine`` (nến), ``sink`` là tham số ``ModuleWriter``, ``store`` là nơi cất config (để nghe phiên bản mới)."""

    module: str
    kind: str
    function: Callable[..., Any]
    pairs: tuple[Pair, ...]
    params: dict[str, Any]
    lengths: dict[str, pd.Timedelta]
    retention: dict[str, int]
    warmup_bars: int
    process_on_startup: bool
    reconnect_delay_seconds: float
    source: dict[str, Any]
    sink: dict[str, Any]
    store: dict[str, Any]


@dataclass(frozen=True)
class DecisionJob:
    """Việc của một tiến trình publisher: ra quyết định cho các symbol của MỘT nguồn nến. ``candle_source`` là tham số ``DataEngine`` đọc nến khung trigger, ``strategy_source`` và ``trend_source`` là tham số ``DataEngine`` đọc kết quả chiến lược
    và kết quả các yếu tố trend ở db đầu ra của DES (db11) (hai tiền tố khác nhau), ``sink`` là tham số ``SignalWriter``. ``routes`` đổi cặp vừa bị ghi thành các ô cần đánh giá lại."""

    registrations: tuple[Registration, ...]
    routes: dict[Pair, tuple[Registration, ...]]
    strategies: dict[str, Strategy]
    process_on_startup: bool
    reconnect_delay_seconds: float
    candle_source: dict[str, Any]
    candle_pairs: tuple[Pair, ...]
    clock_key: str
    strategy_source: dict[str, Any]
    strategy_pairs: tuple[Pair, ...]
    trend_source: dict[str, Any]
    trend_pairs: tuple[Pair, ...]
    sink: dict[str, Any]
    store: dict[str, Any]


def _wanted_symbols(symbols: Sequence[str] | None, operating: tuple[str, ...]) -> frozenset[str] | None:
    """Tập symbol một tiến trình bị giới hạn vào (``--symbols``), hoặc None = mọi symbol. Symbol không thuộc DE thì ValueError."""
    if not symbols:
        return None
    wanted = frozenset(str(symbol).strip().upper() for symbol in symbols)
    unknown = sorted(wanted - set(operating))
    if unknown:
        raise ValueError(f"no work for symbols {', '.join(unknown)}")
    return wanted


def _source_of(plan: Plan, name: str) -> Source:
    if name not in plan.sources:
        raise ValueError(f"candle source '{name or DEFAULT_SOURCE}' is not declared in redis.candles.sources (declared: {', '.join(plan.sources)})")
    return plan.sources[name]


def build_module_job(plan: Plan, local: dict[str, Any], module: str, source_name: str = DEFAULT_SOURCE, symbols: Sequence[str] | None = None) -> ModuleJob:
    """Dựng và kiểm việc của một tiến trình runner TRƯỚC khi chạm Redis: ``module`` là tên một chiến lược hoặc một yếu tố trend đã đăng ký, nguồn nến ``source_name``, ``symbols`` (tuỳ chọn) giới hạn symbol để
    chia việc. Chiến lược chạy ở khung trigger của nó; yếu tố trend chạy ở mọi khung mà các chiến lược tham chiếu nó khai, cho symbol của các chiến lược đó. Chỉ chạy các (symbol, khung) mà nguồn nuôi."""
    if module in plan.strategies:
        strategy = plan.strategies[module]
        kind, function, params = "strategy", strategy.detect, strategy.params
        wanted_pairs = [(symbol, strategy.trigger) for symbol in strategy.symbols]
    elif module in plan.trends:
        factor = plan.trends[module]
        kind, function, params = "trend", factor.function, factor.params
        wanted_pairs = [(symbol, frame) for each in plan.strategies.values() for ref, frame in each.trends if ref == module for symbol in each.symbols]
    else:
        raise ValueError(f"module '{module}' is not a registered strategy or trend (registered: {', '.join([*plan.strategies, *plan.trends])})")
    source = _source_of(plan, source_name or DEFAULT_SOURCE)
    operating = tuple(symbol for each in plan.sources.values() for symbol in each.symbols)
    wanted = _wanted_symbols(symbols, operating)
    pairs = tuple(dict.fromkeys(Pair(symbol, frame) for symbol, frame in wanted_pairs if symbol in source.symbols and (wanted is None or symbol in wanted)))
    if not pairs:
        raise ValueError(f"no pair to run for module {module} on source {source_name or DEFAULT_SOURCE}")
    lengths = {pair.timeframe: timeframe_duration(pair.timeframe) for pair in pairs}
    output = plan.output
    return ModuleJob(
        module=module,
        kind=kind,
        function=function,
        pairs=pairs,
        params=params,
        lengths=lengths,
        retention={timeframe: int(2 * plan.snapshot_bars * length.total_seconds()) for timeframe, length in lengths.items()},
        warmup_bars=plan.warmup_bars,
        process_on_startup=plan.process_on_startup,
        reconnect_delay_seconds=plan.reconnect_delay,
        source={**source.endpoint, "db": source.db, "key_prefix": source.key_prefix, "snapshot_bars": plan.snapshot_bars + plan.warmup_bars, **plan.timeouts},
        sink={
            **{key: output[key] for key in ("host", "port", "password", "db")},
            "key_prefix": output["strategy_prefix" if kind == "strategy" else "trend_prefix"], "retention_max_entries": plan.snapshot_bars, **plan.timeouts,
        },
        store=local["store"],
    )


def build_decision_job(plan: Plan, local: dict[str, Any], source_name: str = DEFAULT_SOURCE, symbols: Sequence[str] | None = None) -> DecisionJob:
    """Dựng và kiểm việc của một tiến trình publisher TRƯỚC khi chạm Redis: với mỗi chiến lược đã đăng ký, các symbol của nguồn nến ``source_name`` (``symbols`` tuỳ chọn giới hạn thêm để chia việc) ở khung trigger của nó,
    mỗi ô tham chiếu các yếu tố trend của chiến lược đó (sổ đăng ký của decision_engine)."""
    source = _source_of(plan, source_name or DEFAULT_SOURCE)
    operating = tuple(symbol for each in plan.sources.values() for symbol in each.symbols)
    wanted = _wanted_symbols(symbols, operating)
    book: list[Registration] = []
    for name, strategy in plan.strategies.items():
        mine = tuple(symbol for symbol in strategy.symbols if symbol in source.symbols and (wanted is None or symbol in wanted))
        if mine:
            refs = [TrendRef(ref, frame, timeframe_duration(frame)) for ref, frame in strategy.trends]
            book.extend(register(name, mine, strategy.trigger, timeframe_duration(strategy.trigger), refs))
    if not book:
        raise ValueError(f"no symbol to decide for source {source_name or DEFAULT_SOURCE}")
    routes: dict[Pair, list[Registration]] = {}
    for registration in book:
        for pair in (
            Pair(registration.symbol, registration.trigger_tf, module_token(registration.strategy)),
            *(Pair(registration.symbol, ref.timeframe, module_token(ref.name)) for ref in registration.refs),
            Pair(registration.symbol, registration.trigger_tf),
        ):
            routes.setdefault(pair, []).append(registration)
    strategy_tokens = {module_token(registration.strategy) for registration in book}
    trend_tokens = {module_token(ref.name) for registration in book for ref in registration.refs}
    output = plan.output
    window = plan.snapshot_bars + plan.warmup_bars
    return DecisionJob(
        registrations=tuple(book),
        routes={pair: tuple(items) for pair, items in routes.items()},
        strategies=plan.strategies,
        process_on_startup=plan.process_on_startup,
        reconnect_delay_seconds=plan.reconnect_delay,
        candle_source={**source.endpoint, "db": source.db, "key_prefix": source.key_prefix, "snapshot_bars": window, **plan.timeouts},
        candle_pairs=tuple(sorted({pair for pair in routes if not pair.module})),
        clock_key=source.clock_key,
        strategy_source={
            **{key: output[key] for key in ("host", "port", "password", "db")},
            "key_prefix": output["strategy_prefix"], "snapshot_bars": plan.snapshot_bars, "modules": True, **plan.timeouts,
        },
        strategy_pairs=tuple(sorted({pair for pair in routes if pair.module in strategy_tokens})),
        trend_source={
            **{key: output[key] for key in ("host", "port", "password", "db")},
            "key_prefix": output["trend_prefix"], "snapshot_bars": plan.snapshot_bars, "modules": True, **plan.timeouts,
        },
        trend_pairs=tuple(sorted({pair for pair in routes if pair.module in trend_tokens})),
        sink={
            **{key: output[key] for key in ("host", "port", "password", "db", "channel_prefix", "retention_seconds", "retention_max_entries")},
            "key_prefix": output["signal_prefix"], "price_decimals": plan.price_decimals, **plan.timeouts,
        },
        store=local["store"],
    )


# -- xử lý một việc -------------------------------------------------------------------------------------------------------------------------------------


def process_module(job: ModuleJob, engine: Any, writer: Any, pair: Pair) -> int:
    """Một cặp nến vừa đổi: đọc cửa sổ nến (warm-up cộng cửa sổ ghi), chạy hàm đã đăng ký (``detect`` của chiến lược, hoặc ``bias_<type>`` của yếu tố trend), ghi các dòng của cửa sổ ghi (nến sau warm-up) vào db11
    rồi bỏ các mốc cũ hơn cửa sổ đó. Chiến lược chỉ có dòng ở nến có tín hiệu, yếu tố trend có dòng ở mọi nến. Trả về số mốc ghi mới. Cặp có không quá ``warmup_bars`` nến thì không ghi (chỉ báo chưa ấm), ghi cảnh báo."""
    frame = engine.snapshot(pair)
    if frame is None:
        return 0
    if len(frame) <= job.warmup_bars:
        LOGGER.warning("event=warmup_incomplete module=%s symbol=%s timeframe=%s candles=%d warmup_bars=%d", job.module, pair.symbol, pair.timeframe, len(frame), job.warmup_bars)
        return 0
    if job.kind == "strategy":
        computed = job.function(frame, **job.params)
        build = signal_rows
    else:
        computed = frame.assign(trend_bias=job.function(frame, **job.params))
        build = bias_rows
    window = computed.iloc[job.warmup_bars :]
    return writer.write(
        module=job.module,
        symbol=pair.symbol,
        timeframe=pair.timeframe,
        rows=build(window, job.lengths[pair.timeframe]),
        retention_seconds=job.retention[pair.timeframe],
        keep_from=window["bartime"].iloc[0].strftime(STAMP_FORMAT),
    )


def process_decision(job: DecisionJob, strategy_engine: Any, trend_engine: Any, candle_engine: Any, writer: Any, registration: Registration, now: pd.Timestamp) -> int:
    """Một ô quyết định cần đánh giá lại (kết quả chiến lược, một yếu tố trend hoặc nến khung trigger của nó vừa đổi): đọc kết quả chiến lược, nến khung trigger và kết quả của từng yếu tố tham chiếu, để
    decision_engine.decide ra các tín hiệu cuối đang đến lúc phát, rồi ghi từng tín hiệu (``now`` để kiểm hạn hiệu lực). Thiếu kết quả chiến lược hoặc nến thì chưa có gì để quyết. Trả về số tín hiệu ghi mới."""
    strategy = job.strategies[registration.strategy]
    signals = strategy_engine.snapshot(Pair(registration.symbol, registration.trigger_tf, module_token(registration.strategy)))
    candles = candle_engine.snapshot(Pair(registration.symbol, registration.trigger_tf))
    if signals is None or candles is None:
        return 0
    trends = {ref.name: trend_engine.snapshot(Pair(registration.symbol, ref.timeframe, module_token(ref.name))) for ref in registration.refs}
    published = 0
    for row in decide(
        signals, candles, trends, registration, max_shift_bars=strategy.max_shift_bars, atr_period=strategy.atr_period, stop_entry=strategy.order_type == "STOP"
    ):
        outcome = writer.publish(
            strategy=registration.strategy, timeframe=registration.trigger_tf, symbol=registration.symbol, row=row,
            length=registration.trigger_length, validity_seconds=strategy.validity_seconds, order_type=strategy.order_type, now=now,
        )
        published += outcome.published
    return published


# -- vòng lặp sự kiện -----------------------------------------------------------------------------------------------------------------------------------


class _Reload(Exception):
    """Config trên Redis có phiên bản mới: dựng lại việc từ phiên bản đó (không restart tiến trình)."""


def _describe(key: Any) -> str:
    """Nhãn của một việc cho dòng log lỗi: cặp (symbol, khung) của runner hoặc ô quyết định (symbol, khung trigger, khung trend) của publisher."""
    if isinstance(key, Registration):
        return f"strategy={key.strategy} symbol={key.symbol} timeframe={key.trigger_tf}"
    if isinstance(key, Pair):
        return f"symbol={key.symbol} timeframe={key.timeframe}"
    return f"key={key}"


def _guarded(step: Callable[[], Any], key: Any) -> Any:
    """Chạy một bước của một việc, cô lập lỗi riêng của việc đó (ghi log, trả 0) nhưng để redis.RedisError nổi lên cho vòng nối lại."""
    try:
        return step()
    except redis.RedisError:
        raise
    except Exception:
        LOGGER.exception("event=process_failed %s", _describe(key))
        return 0


def _run_loop(
    *,
    engines: Sequence[Any],
    keys: Sequence[Any],
    route: Callable[[Any], Sequence[Any]],
    step: Callable[[Any], int],
    changed: Callable[[], bool],
    process_on_startup: bool,
    wait_seconds: float,
    clock: Callable[[], float] = time.monotonic,
    should_stop: Callable[[], bool] = lambda: False,
) -> None:
    """Vòng lặp sự kiện chung của mọi loại tiến trình. Sự kiện keyspace chỉ đánh dấu việc "bẩn"; vòng lặp lấy từng việc bẩn và chạy ``step`` trên dữ liệu mới nhất, nên nhiều sự kiện của cùng một việc
    (đợt ghi bù) gộp thành một lần xử lý. Giữa hai việc luôn hút tiếp event đang chờ. Lúc khởi động quét toàn bộ việc MỘT lần (``process_on_startup``) để bù những gì đã tới lúc tiến trình tắt; không có nhịp
    quét định kỳ. ``route`` đổi cặp của sự kiện thành CÁC khoá việc. ``changed`` báo config trên Redis có phiên bản mới: vòng lặp raise ``_Reload``. Chạy mãi; test dừng bằng ``should_stop``."""
    dirty: dict[Any, None] = {}
    pending: set[Any] = set()
    started = clock()
    total = published = 0
    if process_on_startup:
        LOGGER.info("event=reconcile_start keys=%d", len(keys))
        pending, total = set(keys), len(keys)
        for key in keys:
            dirty.setdefault(key)
    while not should_stop():
        if changed():
            raise _Reload
        timeout = 0.0 if dirty else wait_seconds
        for engine in engines:
            for pair in engine.poll_events(timeout / len(engines)):
                for key in route(pair):
                    dirty.setdefault(key)
        if not dirty:
            continue
        key = next(iter(dirty))
        del dirty[key]
        written = step(key)
        if pending:
            pending.discard(key)
            published += written
            if not pending:
                LOGGER.info("event=reconcile_end keys=%d written=%d duration_s=%.1f", total, published, clock() - started)


def _watcher_parts(store: dict[str, Any], register_part: Callable[[Any], Any]) -> KeyspaceListener:
    """Kết nối nghe phiên bản config mới (HASH ``{key_prefix}:{phiên bản}`` vừa được ghi) ở Redis cất config. Phải bật lớp 'K' + 'h' của notify-keyspace-events."""
    watcher = register_part(KeyspaceListener(host=store["host"], port=store["port"], password=store["password"], db=store["db"]))
    watcher.ping()
    watcher.check_flags()
    watcher.subscribe([f"__keyspace@{store['db']}__:{store['key_prefix']}:*"])
    return watcher


def run_module_lane(
    job: ModuleJob, engine: Any, writer: Any, watcher: Any, *, clock: Callable[[], float] = time.monotonic, should_stop: Callable[[], bool] = lambda: False
) -> None:
    """Vòng lặp sự kiện của một tiến trình runner (xem ``_run_loop``): mỗi cặp nến bẩn chạy ``process_module``; quét khởi động theo thứ tự khung lớn trước."""
    keys = tuple(sorted(job.pairs, key=lambda pair: (-job.lengths[pair.timeframe], pair.symbol)))
    _run_loop(
        engines=[engine], keys=keys, route=lambda pair: (pair,), step=lambda pair: _guarded(lambda: process_module(job, engine, writer, pair), pair),
        changed=lambda: bool(watcher.poll(0.0)), process_on_startup=job.process_on_startup, wait_seconds=_IDLE_WAIT_SECONDS, clock=clock, should_stop=should_stop,
    )


def run_decision_lane(
    job: DecisionJob,
    strategy_engine: Any,
    trend_engine: Any,
    candle_engine: Any,
    writer: Any,
    watcher: Any,
    *,
    now: Callable[[], pd.Timestamp] = lambda: pd.Timestamp.now(tz="UTC"),
    clock: Callable[[], float] = time.monotonic,
    should_stop: Callable[[], bool] = lambda: False,
) -> None:
    """Vòng lặp sự kiện của một tiến trình publisher (xem ``_run_loop``): mỗi ô bẩn chạy ``process_decision``; sự kiện của kết quả chiến lược, của một yếu tố trend hoặc của nến khung trigger đều đánh dấu ô đó."""
    _run_loop(
        engines=[strategy_engine, trend_engine, candle_engine],
        keys=job.registrations,
        route=lambda pair: job.routes.get(pair, ()),
        step=lambda registration: _guarded(lambda: process_decision(job, strategy_engine, trend_engine, candle_engine, writer, registration, now()), registration),
        changed=lambda: bool(watcher.poll(0.0)),
        process_on_startup=job.process_on_startup,
        wait_seconds=_DECISION_WAIT_SECONDS,
        clock=clock,
        should_stop=should_stop,
    )


def _run_module_forever(job: ModuleJob) -> None:
    """Nối Redis, đăng ký nghe đúng các cặp nến và phiên bản config, rồi chạy vòng lặp mãi mãi. Việc nối và nối lại khi mất Redis do ``redis_connector.keep_connected`` làm."""

    def connect(register_part: Callable[[Any], Any]) -> tuple[Any, Any, Any]:
        engine = register_part(DataEngine(**job.source, pairs=job.pairs))
        writer = register_part(ModuleWriter(**job.sink))
        engine.ping()
        writer.ping()
        LOGGER.info("event=redis_connected input_db=%s output_db=%s", job.source["db"], job.sink["db"])
        flags = engine.check_keyspace_flags()
        subscribed = engine.subscribe()
        LOGGER.info("event=subscribed db=%s key_prefix=%s notify_flags=%s patterns=%d pairs=%d", job.source["db"], job.source["key_prefix"], flags, subscribed, len(job.pairs))
        return engine, writer, _watcher_parts(job.store, register_part)

    keep_connected(connect, lambda parts: run_module_lane(job, *parts), retry_delay_seconds=job.reconnect_delay_seconds)


def _virtual_now(candle_engine: Any, clock_key: str) -> Callable[[], pd.Timestamp]:
    """"Bây giờ" của mô phỏng: giờ ảo ``clock_key`` (``dfs:clock``) đọc ở db nguồn nến mỗi khi publisher ra quyết định (DE live dùng giờ máy). Chưa có giờ ảo (DFS chưa seed) thì RuntimeError."""

    def now() -> pd.Timestamp:
        text = candle_engine.read_key(clock_key)
        if not text:
            raise RuntimeError(f"simulated clock '{clock_key}' is not set in the candle source db yet (is DFS running and seeded?)")
        return pd.Timestamp(text, tz="UTC")

    return now


def _run_decision_forever(job: DecisionJob) -> None:
    """Như ``_run_module_forever`` cho publisher: nối nến của nguồn, kết quả chiến lược, kết quả các yếu tố trend (hai bộ đọc, hai tiền tố) và đầu ra ở db đầu ra của DES (db11), nghe các cặp của mọi ô, chạy vòng lặp mãi mãi.
    Redis đầu ra phải bật lớp 'K' + 'h' của notify-keyspace-events (thiếu thì ``check_keyspace_flags`` báo RuntimeError rõ ràng ngay lúc khởi động)."""

    def connect(register_part: Callable[[Any], Any]) -> tuple[Any, Any, Any, Any, Any]:
        candle_engine = register_part(DataEngine(**job.candle_source, pairs=job.candle_pairs))
        strategy_engine = register_part(DataEngine(**job.strategy_source, pairs=job.strategy_pairs))
        trend_engine = register_part(DataEngine(**job.trend_source, pairs=job.trend_pairs))
        writer = register_part(SignalWriter(**job.sink))
        candle_engine.ping()
        strategy_engine.ping()
        trend_engine.ping()
        writer.ping()
        LOGGER.info("event=redis_connected candle_db=%s output_db=%s", job.candle_source["db"], job.sink["db"])
        candle_flags, output_flags = candle_engine.check_keyspace_flags(), strategy_engine.check_keyspace_flags()
        trend_engine.check_keyspace_flags()
        subscribed = candle_engine.subscribe() + strategy_engine.subscribe() + trend_engine.subscribe()
        LOGGER.info("event=subscribed candle_flags=%s output_flags=%s patterns=%d registrations=%d", candle_flags, output_flags, subscribed, len(job.registrations))
        return strategy_engine, trend_engine, candle_engine, writer, _watcher_parts(job.store, register_part)

    keep_connected(connect, lambda parts: run_decision_lane(job, *parts, now=_virtual_now(parts[2], job.clock_key)), retry_delay_seconds=job.reconnect_delay_seconds)


# -- lối vào của main.py --------------------------------------------------------------------------------------------------------------------------------


def _lane_name(role: str, source_name: str, symbols: Sequence[str] | None, part: str = "") -> str:
    """Tên tiến trình trong log (cũng là tên file log ``des_run_<tên>.log``): vai trò, rồi module (runner), rồi nguồn nến, rồi các symbol nếu tiến trình bị giới hạn. Chỉ chữ thường, số và gạch dưới."""
    wanted = sorted(str(symbol).strip().lower() for symbol in (symbols or ()))
    return "_".join(name for name in (role, part, source_name, *wanted) if name)


def _serve(name: str, make_job: Callable[[Plan, dict[str, Any]], Any], forever: Callable[[Any], None]) -> int:
    """Khung chạy chung của runner và publisher: ghi log, nạp des_config.yaml lên Redis (khởi tạo), đọc config từ Redis, dựng việc rồi chạy. Có phiên bản config mới thì dựng lại việc và chạy tiếp, không restart.
    Mất Redis lúc đọc config thì đợi rồi thử lại. Trả về mã thoát: 0 khi dừng êm hoặc redis.enabled=false, 1 khi cấu hình sai (đã ghi event=config_error)."""
    from des.src.log import configure_logging, set_log_level

    configure_logging("INFO", name)
    try:
        local = load_bootstrap()
        uploaded = False
        while True:
            try:
                if not uploaded:
                    version, created = upload_config()
                    uploaded = True
                    LOGGER.info("event=config_uploaded version=%d new_version=%s", version, created)
                version, plan = fetch_plan(local)
                set_log_level(plan.log_level)
                if not plan.enabled:
                    LOGGER.info("event=disabled reason=redis_disabled_by_config")
                    return 0
                job = make_job(plan, local)
                LOGGER.info("event=startup lane=%s config_version=%d log_level=%s", name, version, plan.log_level)
                forever(job)
            except _Reload:
                LOGGER.info("event=config_reload lane=%s", name)
            except redis.RedisError as exc:
                LOGGER.error("event=redis_connection_lost error=%s retry_in_s=%s", exc, _REDIS_DEFAULTS["reconnect_delay_seconds"])
                time.sleep(_REDIS_DEFAULTS["reconnect_delay_seconds"])
    except (KeyboardInterrupt, SystemExit):
        LOGGER.info("event=stopped")
    except (KeyError, TypeError, ValueError, RuntimeError, OSError) as exc:
        LOGGER.critical("event=config_error error=%s", exc)
        return 1
    return 0


def run_runner(source_name: str = "", symbols: Sequence[str] | None = None, lane: str = "") -> int:
    """Chạy một tiến trình runner (gọi từ main.py): module ``lane`` (``ma_cross`` hoặc ``trend``) trên nguồn nến ``source_name`` (rỗng = ``default``), ``symbols`` (tuỳ chọn) giới hạn symbol để chia việc.
    File log ``des_run_runner_<module>[_<nguồn>][_<symbol>...].log``."""
    return _serve(_lane_name("runner", source_name, symbols, lane), lambda plan, local: build_module_job(plan, local, lane, source_name, symbols), _run_module_forever)


def run_publisher(source_name: str = "", symbols: Sequence[str] | None = None) -> int:
    """Chạy một tiến trình publisher (gọi từ main.py) cho nguồn nến ``source_name`` (rỗng = ``default``); ``symbols`` (tuỳ chọn) giới hạn symbol để chia việc. File log ``des_run_publisher[_<nguồn>][_<symbol>...].log``."""
    return _serve(_lane_name("publisher", source_name, symbols), lambda plan, local: build_decision_job(plan, local, source_name, symbols), _run_decision_forever)


def run_load_config() -> int:
    """Nạp des_config.yaml lên Redis rồi thoát (gọi từ ``main --load-config``): các tiến trình đang chạy thấy phiên bản mới và dùng ngay. Mã thoát 0 khi xong, 1 khi cấu hình sai."""
    from des.src.log import configure_logging

    configure_logging("INFO", "load_config")
    try:
        version, created = upload_config()
    except (KeyError, TypeError, ValueError, RuntimeError, OSError, redis.RedisError) as exc:
        LOGGER.critical("event=config_error error=%s", exc)
        return 1
    LOGGER.info("event=config_uploaded version=%d new_version=%s", version, created)
    return 0
