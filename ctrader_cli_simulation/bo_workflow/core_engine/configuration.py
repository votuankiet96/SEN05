"""Đọc + kiểm tra bo_workflow/config.yaml (cấu hình kỹ thuật + lựa chọn người dùng), đối
chiếu với contract THẬT của .algo. Đây là phần "điều phối cấu hình" — không
gọi ctrader-cli/bridge API (2 engine riêng giữ độc quyền đó), không ghi gì
xuống đĩa.

Đổi tên từ catalog.py 2026-09-12, gộp thêm 2 loader (engine/ftmo) từng nằm rải
rác ở api.py hoặc hoàn toàn chưa có — giờ MỌI profile đều load qua đúng 1 nơi.
Dời vào core_engine/ 2026-09-17 khi tách 2 engine (cli_engine/, api_engine/)
ra làm 2 package ngang hàng (nay cli_engine đã gộp vào core_engine/, api_engine
đã xoá 2026-10-13) — file này là lõi dùng chung, không thuộc engine nào cả.

[2026-09-21] Thu hẹp lại đúng 1 vai "profile + tự-validate": SYMBOL_GROUPS/
SIGNAL_EXPORT_DIR/khuôn tên CSV tĩnh dời sang signal_trans.py (câu hỏi "signal
đến từ đâu" không phải "cấu hình chiến lược là gì"); validate_metadata() đổi
từ hàm rời sang method StrategyProfile.validate_metadata().
"""
from __future__ import annotations

import dataclasses
import json
import math
import os
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

from . import risk_allocation
from .models import RAW_SIGNAL_SOURCE, EngineProfile, SignalChannel

# File này ở bo_workflow/core_engine/configuration.py -> ROOT = core_engine.
# [2026-09-21] config.yaml dời ra bo_workflow/ (ngang hàng core_engine/, dùng
# chung cho cả hệ chứ không riêng core_engine) -> CONFIG_PATH
# = ROOT.parent / "config.yaml". ROBOTS_DIR trèo thêm 1 cấp nữa (core_engine ->
# bo_workflow -> Sources/Robots) mới tới chỗ ComboMulti.algo/MACrossMulti.algo/MACDBreakMulti.algo thật nằm.
ROOT = Path(__file__).resolve().parent
ROBOTS_DIR = ROOT.parent.parent
CONFIG_PATH = ROOT.parent / "config.yaml"

# Nguồn Redis mặc định (db0 RAW, không lọc trend) khi config experiment không khai `redis_profile`.
# Ở đây (nhà chung) thay vì facilitator.py để output_util/ dùng được mà không import facilitator.
DEFAULT_REDIS_PROFILE = RAW_SIGNAL_SOURCE

# [2026-09-16] Doi het gia tri sang phi^(n/2) (phi=1.618034) - KHONG con
# Fib2000/Fib3618/Fib4618/Fib0236 (so tron/quy uoc cu). Thu tu duoi day PHAI
# khop CHINH XAC thu tu enum SlFibLevel/TpFibLevel that trong ComboMulti.cs,
# MACrossMulti.cs va MACDBreakMulti.cs (encode() dung index() tren tuple nay -> lech thu tu = sai lenh
# ma khong bao loi). Moi .cbotset/ket qua optimize cu tham chieu gia tri cu
# deu het hieu luc.
SL_FIB_LEVELS = (
    "Fib0618", "Fib0786", "Fib1000", "Fib1272", "Fib1618",
    "Fib2058", "Fib2618", "Fib3330", "Fib4236", "Fib5388",
)
TP_FIB_LEVELS = (
    "Fib1000", "Fib1272", "Fib1618", "Fib2058", "Fib2618",
    "Fib3330", "Fib4236", "Fib5388", "Fib6854", "Fib8719",
)

# Mã 2 chữ cái của từng chiến lược (khoá = token Redis): dùng trong NHÃN LỆNH của BacktestBot (cb_US30_H1_L1) và trong
# TÊN LƯỢT (cb-mc_S3_T4_L3_...). Bảng `Strategies` trong BacktestBot.cs phải khớp bảng này (có test chặn lệch).
STRATEGY_CODES = {"combo": "cb", "ma_cross": "mc", "macd_break": "mb"}


@dataclass(frozen=True)
class ParamDef:
    name: str
    kind: str
    default: Any
    optimize: bool = False
    levels: tuple[Any, ...] = ()
    enum_values: tuple[str, ...] = ()

    def encode(self, value: Any) -> str:
        if self.kind == "enum":
            values = self.enum_values
            if isinstance(value, int) or str(value).isdigit():
                index = int(value)
                if not 0 <= index < len(values):
                    raise ValueError(f"{self.name}: enum index out of range: {index}")
                return str(index)
            if value not in values:
                raise ValueError(f"{self.name}: unknown enum value {value!r}")
            return str(values.index(value))
        if self.kind == "bool":
            if isinstance(value, bool):
                return "True" if value else "False"
            text = str(value).strip().lower()
            if text in {"true", "1", "yes"}:
                return "True"
            if text in {"false", "0", "no"}:
                return "False"
            raise ValueError(f"{self.name}: not a boolean: {value!r}")
        if self.kind == "int":
            if isinstance(value, bool):
                raise ValueError(f"{self.name}: not an integer: {value!r}")
            number = float(value)
            if not math.isfinite(number) or not number.is_integer():
                raise ValueError(f"{self.name}: not a finite integer: {value!r}")
            return str(int(number))
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"{self.name}: not a finite number: {value!r}")
        return str(int(number)) if number.is_integer() else str(number)


_META_TYPES = {"enum": "Enum", "float": "Double", "int": "Integer", "bool": "Boolean"}


@dataclass(frozen=True)
class StrategyProfile:
    id: str
    strategy: str
    cbot_name: str
    algo_path: Path
    signal_prefix: str
    default_timeframes: tuple[str, ...]
    params: Mapping[str, ParamDef]
    infra_params: tuple[str, ...] = ("SignalFilePath",)
    # signal_label/signal_mode la 2 manh CAU HINH cho ten file CSV tinh do
    # core_python.export_cli sinh — khuon ten day du + SYMBOL_GROUPS/
    # SIGNAL_EXPORT_DIR gio nam o signal_trans.py (dời 2026-09-21, gom moi câu
    # hỏi "signal đến từ đâu" về 1 file — cũng là chỗ nguồn Redis sẽ cắm vào).
    signal_label: str = "full_history"
    signal_mode: str = "nt"
    # File .cs sinh ra .algo (tương đối ROBOTS_DIR). .algo KHÔNG chứa mã nguồn
    # (manifest ghi SourceIncluded=false) nên .cs phải được chụp riêng lúc chạy.
    source_path: Path | None = None
    # Bot chạy nhiều khung cùng lúc (ComboMulti): các khung lấy tín hiệu từ Redis và ghép vào MỘT file có cột `tf`.
    # Rỗng = bot 1 khung (vd combomulti-1tf-v1; tín hiệu của đúng `spec.timeframe`). `spec.timeframe` khi đó chỉ là
    # khung biểu đồ để chạy backtest.
    signal_timeframes: tuple[str, ...] = ()
    # Bot nhiều SYMBOL (ComboMulti): các symbol (tên cTrader, vd "US30.cash") lấy tín hiệu từ Redis, ghép cùng
    # `signal_timeframes` vào MỘT file có thêm cột `symbol`. Rỗng = chỉ symbol của spec (file không có cột symbol).
    # `spec.symbol` khi đó chỉ là symbol biểu đồ để chạy CLI (phải là một trong các symbol này).
    signal_symbols: tuple[str, ...] = ()
    # Bot nhiều CHIẾN LƯỢC (BacktestBot): danh mục `token Redis -> khung mặc định`. Rỗng = bot một chiến lược (profile
    # cũ). Sau `with_signal_overrides` nó chỉ còn các chiến lược ĐÃ CHỌN, với khung đã thu hẹp.
    strategy_frames: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    # Các chiến lược ĐÃ CHỌN (token Redis, theo thứ tự danh mục) của lần chạy này; chỉ do `with_signal_overrides` đặt.
    signal_strategies: tuple[str, ...] = ()
    # Exact streams selected for this run. Empty keeps the legacy dashboard
    # selectors working; new single/multi backtests use this contract.
    signal_channels: tuple[SignalChannel, ...] = ()

    def defaults(self) -> dict[str, str]:
        return {name: pdef.encode(pdef.default) for name, pdef in self.params.items()}

    def resolve_params(self, fixed: Mapping[str, Any] | None = None) -> dict[str, str]:
        fixed = fixed or {}
        unknown = sorted(set(fixed) - set(self.params))
        if unknown:
            raise KeyError(f"{self.id}: unknown params: {unknown}")
        out = self.defaults()
        for name, value in fixed.items():
            out[name] = self.params[name].encode(value)
        return out

    def param_levels(self, name: str, value: Any) -> list[str]:
        if name not in self.params:
            raise KeyError(f"{self.id}: unknown param: {name}")
        pdef = self.params[name]
        if not pdef.optimize:
            raise ValueError(f"{self.id}: param is not allowlisted for optimize: {name}")
        raw = pdef.levels if value is True else value
        items = list(raw) if isinstance(raw, (list, tuple)) else [raw]
        if not items:
            raise ValueError(f"{name}: empty parameter level list")
        out: list[str] = []
        for item in items:
            encoded = pdef.encode(item)
            if encoded not in out:
                out.append(encoded)
        return out

    def validate_metadata(self, metadata: Mapping[str, Any]) -> dict[str, Any]:
        """Đối chiếu metadata THẬT (do cli_runner.read_metadata() moi ra từ
        .algo) với đúng profile này — đây là hành vi tự-kiểm-tra của profile
        (không phải "đọc config.yaml"), nên là method thay vì hàm rời (dời từ
        module-level function 2026-09-21)."""
        if metadata.get("Name") != self.cbot_name:
            raise ValueError(f"metadata Name drift: {metadata.get('Name')!r} != {self.cbot_name!r}")
        rows = metadata.get("Parameters")
        if not isinstance(rows, list):
            raise ValueError("metadata missing Parameters list")
        actual = {str(row["PropertyName"]): row for row in rows if isinstance(row, dict)}
        expected = set(self.params) | set(self.infra_params)
        if set(actual) != expected:
            raise ValueError(
                f"metadata parameter drift: missing={sorted(expected - set(actual))}, "
                f"extra={sorted(set(actual) - expected)}"
            )
        for infra in self.infra_params:
            if actual[infra].get("Type") != "String":
                raise ValueError(f"{infra}: expected metadata Type=String")
        for name, pdef in self.params.items():
            row = actual[name]
            if row.get("Type") != _META_TYPES[pdef.kind]:
                raise ValueError(f"{name}: metadata type drift")
            if pdef.kind == "enum":
                enum = row.get("EnumValues")
                expected_enum = {value: i for i, value in enumerate(pdef.enum_values)}
                actual_enum = {str(k): int(v) for k, v in (enum or {}).items()}
                if actual_enum != expected_enum:
                    raise ValueError(f"{name}: enum mapping drift")
            # ctrader-cli bỏ trống DefaultValue của tham số số có mặc định 0 (đo 2026-10-17 trên ComboRiskPercent).
            declared = 0 if pdef.kind in ("float", "int") and "DefaultValue" not in row else row.get("DefaultValue")
            if pdef.encode(declared) != pdef.encode(pdef.default):
                raise ValueError(f"{name}: default drift")
        return {
            "name": metadata.get("Name"),
            "type": metadata.get("Type"),
            "access_rights": metadata.get("AccessRights"),
            "parameter_count": len(actual),
        }


@dataclass(frozen=True)
class RedisProfile:
    """Kết nối Redis nguồn tín hiệu (thay CSV) — do strategy_lab (SEN05)
    cấp. CHỈ chứa đường dẫn file mật khẩu (`pwd_file`), giống hệt nguyên tắc đã
    dùng cho `CTRADER_PWD_FILE`/`.ctrader-cli-pwd.txt` — KHÔNG BAO GIỜ hardcode
    mật khẩu thật vào config.yaml hay bất kỳ đâu khác trong repo.

    [2026-10-23] WE (OG8) cấp 3 nguồn CỐ ĐỊNH, mỗi DB/tiền tố giữ một nghĩa không bao giờ đổi: db0 `L_PastSignal` = RAW,
    db1 `L_PastSignal_Knn` = lọc trend KNN, db2 `L_PastSignal_Breakout` = lọc trend breakout. Kiểu lọc mới sau này sẽ
    lấy DB/tiền tố mới chứ không dùng lại tên cũ (tên `L_PastSignal_Trend` từng đổi nội dung KNN <-> breakout).

    `key_prefix` khác nhau giữa các nguồn VÀ số db khác nhau. Mặc định "L_PastSignal" giữ nguyên hành vi nguồn RAW.

    `label` = nhãn nguồn ghi vào kho SQL (signal_snapshot.channel): `original` / `knn` / `breakout`. Khai TƯỜNG MINH
    trong config.yaml, không suy ra từ tiền tố: cách suy cũ ("có chữ Trend trong tiền tố") sẽ ghi nhầm KNN và
    breakout thành `original`."""
    id: str
    host: str
    port: int
    db: int
    username: str
    pwd_file: str
    key_prefix: str = "L_PastSignal"
    label: str = ""

    def password(self) -> str:
        """Đọc mật khẩu thật từ pwd_file — chỉ gọi đúng lúc cần tạo kết nối
        Redis, không bao giờ log/in giá trị trả về ra bất cứ đâu."""
        return Path(self.pwd_file).read_text(encoding="utf-8").strip()


@dataclass(frozen=True)
class FtmoProfile:
    id: str
    timezone: str
    initial_balance: float
    daily_loss_pct: float
    max_loss_pct: float
    max_loss_type: str
    daily_loss_base: str


@dataclass(frozen=True)
class MasterDbProfile:
    """Kho SQL master kết quả backtest (SQL Server Express trên cùng máy). Đăng nhập Windows
    (Trusted_Connection) — KHÔNG có mật khẩu ở config.yaml hay bất kỳ đâu."""
    enabled: bool
    server: str
    database: str
    driver: str = "ODBC Driver 18 for SQL Server"


_CONFIG_CACHE: dict[str, Any] | None = None


def _config() -> dict[str, Any]:
    """Đọc config.yaml đúng 1 lần/tiến trình; cache theo module-level singleton
    vì file này không đổi giữa lúc chạy (đổi profile giữa chừng 1 experiment là
    lỗi, không phải tính năng)."""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is None:
        _CONFIG_CACHE = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    return _CONFIG_CACHE


def load_strategy_profile(profile_id: str) -> StrategyProfile:
    raw = _config()["strategies"][profile_id]
    params: dict[str, ParamDef] = {}
    for item in raw["params"]:
        enum_values = tuple(item.get("enum_values") or ())
        if item.get("enum") == "sl_fib":
            enum_values = SL_FIB_LEVELS
        elif item.get("enum") == "tp_fib":
            enum_values = TP_FIB_LEVELS
        # [2026-09-21] levels mac dinh = enum_values khi khong khai rieng —
        # trước đây config.yaml chép tay lại NGUYÊN VĂN SL_FIB_LEVELS/
        # TP_FIB_LEVELS ở đây (2 nguồn cùng 1 sự thật, lệch thứ tự = sai lệnh
        # mà không báo lỗi). enum sl_fib/tp_fib giờ không cần khai `levels:`
        # trong config.yaml nữa — sweep mặc định = toàn bộ enum.
        levels = tuple(item.get("levels") or ()) or enum_values
        params[item["name"]] = ParamDef(
            name=item["name"],
            kind=item["kind"],
            default=item.get("default"),
            optimize=bool(item.get("optimize", False)),
            levels=levels,
            enum_values=enum_values,
        )
    return StrategyProfile(
        id=profile_id,
        strategy=raw["strategy"],
        cbot_name=raw["cbot_name"],
        algo_path=ROBOTS_DIR / raw["algo"],
        signal_prefix=raw["signal_prefix"],
        default_timeframes=tuple(raw.get("default_timeframes") or ()),
        params=params,
        infra_params=tuple(raw.get("infra_params") or ("SignalFilePath",)),
        signal_label=str(raw.get("signal_label") or "full_history"),
        signal_mode=str(raw.get("signal_mode") or "nt"),
        source_path=ROBOTS_DIR / raw["source"] if raw.get("source") else None,
        signal_timeframes=tuple(str(item).upper() for item in raw.get("signal_timeframes") or ()),
        signal_symbols=tuple(str(item) for item in raw.get("signal_symbols") or ()),
        strategy_frames={
            str(token).strip().lower(): tuple(str(item).upper() for item in frames or ())
            for token, frames in (raw.get("strategy_frames") or {}).items()
        },
    )


def _select_strategies(
    profile: StrategyProfile, chosen: Any, wanted: tuple[str, ...], per_strategy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Phần ghi đè của profile nhiều chiến lược: chọn chiến lược (BẮT BUỘC) và thu hẹp khung theo `wanted`. Mọi chỗ
    không khớp là LỖI rõ ràng — không bỏ qua ngầm một chiến lược hay khung đã khai. `per_strategy` (`signal_frames`,
    {chiến lược: [khung]}) khai khung RIÊNG từng chiến lược, thay cho `wanted` chung; không dùng chung với `wanted`."""
    catalog = profile.strategy_frames
    if per_strategy:
        if wanted:
            raise ValueError(f"{profile.id}: signal_frames và signal_timeframes không dùng cùng nhau")
        per = {str(key).strip().lower(): tuple(str(item).upper() for item in value) for key, value in per_strategy.items()}
        tokens = [str(item).strip().lower() for item in chosen or ()]
        if sorted(per) != sorted(set(tokens)):
            raise ValueError(f"{profile.id}: signal_frames phải khai đúng các chiến lược của signal_strategies {tokens}, có {list(per)}")
        for token, frames in per.items():
            bad = [item for item in frames if item not in catalog.get(token, ())]
            if bad or not frames:
                raise ValueError(f"{profile.id}: chiến lược {token} không có khung {list(bad or frames)} (có: {list(catalog.get(token, ()))})")
        selected = tuple(token for token in catalog if token in per)
        return {"signal_strategies": selected, "strategy_frames": {token: tuple(dict.fromkeys(per[token])) for token in selected},
                "strategy": "+".join(selected)}
    if not chosen:
        raise ValueError(f"{profile.id}: cấu hình lần chạy phải khai signal_strategies, chọn trong {list(catalog)}")
    tokens = [str(item).strip().lower() for item in chosen]
    unknown = [token for token in tokens if token not in catalog]
    if unknown:
        raise ValueError(f"{profile.id}: chiến lược không có trong profile: {unknown}; chọn trong {list(catalog)}")
    selected = tuple(token for token in catalog if token in tokens)   # thứ tự danh mục, tự khử trùng
    frames: dict[str, tuple[str, ...]] = {}
    for token in selected:
        frames[token] = tuple(item for item in catalog[token] if item in wanted) if wanted else catalog[token]
        if not frames[token]:
            raise ValueError(
                f"{profile.id}: chiến lược {token} không có khung nào trong {list(wanted)} (có: {list(catalog[token])})"
            )
    unused = [item for item in wanted if not any(item in values for values in frames.values())]
    if unused:
        raise ValueError(f"{profile.id}: khung {unused} không thuộc chiến lược nào đã chọn {list(selected)}")
    return {"signal_strategies": selected, "strategy_frames": frames, "strategy": "+".join(selected)}


def with_signal_overrides(profile: StrategyProfile, config: Mapping[str, Any]) -> StrategyProfile:
    """Profile đã áp `signal_symbols` / `signal_timeframes` (và `signal_strategies` cho profile nhiều chiến lược) của
    CẤU HÌNH LẦN CHẠY — để mỗi experiment tự chọn nạp bao nhiêu chiến lược, symbol, khung mà không phải sửa
    config.yaml. Khoá thiếu = giữ giá trị của profile; danh sách rỗng được hiểu là "không ghi đè", TRỪ
    `signal_strategies` của profile nhiều chiến lược: thiếu là lỗi (không bao giờ chạy cả danh mục ngầm).
    Profile nhiều chiến lược: `strategy` của profile đổi thành các chiến lược đã chọn nối bằng "+" (vd "combo+macd_break")
    để RunSpec, nhãn và kho SQL phân biệt được các lượt chạy khác chiến lược."""
    raw_channels = config.get("channels")
    if raw_channels is not None:
        legacy = [key for key in ("signal_symbols", "signal_timeframes", "signal_strategies", "signal_frames") if config.get(key)]
        if legacy:
            raise ValueError(f"{profile.id}: channels cannot be combined with legacy selectors {legacy}")
        if not profile.strategy_frames:
            raise ValueError(f"{profile.id}: channels require a multi-strategy profile")
        if not isinstance(raw_channels, (list, tuple)) or not raw_channels:
            raise ValueError(f"{profile.id}: channels must be a non-empty list")
        channels: list[SignalChannel] = []
        seen: set[tuple[str, str, str]] = set()
        # Nguồn tín hiệu mặc định của lượt; mỗi kênh có thể đè bằng `source` (id profile Redis). Điền nguồn thực vào MỌI
        # kênh: lượt chỉ một nguồn thì mọi kênh cùng nguồn đó; lượt nhiều nguồn thì từng kênh một nguồn.
        default_source = str(config.get("redis_profile") or DEFAULT_REDIS_PROFILE).strip()
        for index, raw in enumerate(raw_channels):
            if not isinstance(raw, Mapping):
                raise ValueError(f"channels[{index}] must be a mapping")
            token = str(raw.get("strategy", "")).strip().lower()
            symbol = str(raw.get("symbol", "")).strip()
            timeframe = str(raw.get("timeframe", "")).strip().upper()
            source = str(raw.get("source") or default_source).strip()
            if raw.get("source"):
                try:
                    load_redis_profile(source)
                except KeyError:
                    raise ValueError(f"channels[{index}]: unknown signal source {source!r}") from None
            if token not in profile.strategy_frames:
                raise ValueError(f"channels[{index}]: unknown strategy {token!r}")
            if timeframe not in profile.strategy_frames[token]:
                raise ValueError(f"channels[{index}]: {timeframe!r} is not supported by {token}")
            if not symbol:
                raise ValueError(f"channels[{index}]: symbol cannot be empty")
            try:
                risk = float(raw["risk_percent"])
            except (KeyError, TypeError, ValueError):
                raise ValueError(f"channels[{index}]: risk_percent must be finite and > 0") from None
            if not math.isfinite(risk) or risk <= 0:
                raise ValueError(f"channels[{index}]: risk_percent must be finite and > 0")
            # Trùng kênh bị chặn BẤT KỂ nguồn: hai nguồn cho cùng (chiến lược, symbol, khung) sẽ cho hai dòng cùng nến trong
            # file tín hiệu và BacktestBot.cs ghi đè IM LẶNG dòng đọc trước.
            key = (token, symbol, timeframe)
            if key in seen:
                raise ValueError(f"duplicate channel: {token}/{symbol}/{timeframe}")
            seen.add(key)
            channels.append(SignalChannel(token, symbol, timeframe, risk, source))
        selected = tuple(token for token in profile.strategy_frames if any(c.strategy == token for c in channels))
        frames = {token: tuple(dict.fromkeys(c.timeframe for c in channels if c.strategy == token)) for token in selected}
        symbols = tuple(dict.fromkeys(c.symbol for c in channels))
        return dataclasses.replace(
            profile,
            strategy="+".join(selected),
            signal_strategies=selected,
            strategy_frames=frames,
            signal_symbols=symbols,
            signal_channels=tuple(channels),
        )

    updates: dict[str, Any] = {}
    symbols = config.get("signal_symbols")
    if symbols:
        updates["signal_symbols"] = tuple(str(item) for item in symbols)
    timeframes = config.get("signal_timeframes")
    wanted = tuple(str(item).upper() for item in timeframes) if timeframes else ()
    if profile.strategy_frames:
        updates.update(_select_strategies(profile, config.get("signal_strategies"), wanted, config.get("signal_frames")))
    else:
        if config.get("signal_strategies"):
            raise ValueError(f"{profile.id}: profile này chỉ có một chiến lược, không dùng signal_strategies")
        if wanted:
            updates["signal_timeframes"] = wanted
    return dataclasses.replace(profile, **updates) if updates else profile


def read_algo_manifest(algo_path: str | Path) -> dict[str, Any]:
    """Manifest JSON nằm trong .algo: tiêu đề `algo` + 1 byte phiên bản + 4 byte độ dài
    (big-endian), rồi 1 luồng gzip chứa manifest (đo 2026-10-13 trên cả 3 bot: ngày build
    `Timestamp`, `Store.SourceIncluded`, `Types[].Parameters` kèm `EnumValues`). Chỉ đọc."""
    data = Path(algo_path).read_bytes()
    if data[:4] != b"algo":
        raise ValueError(f"{algo_path}: không phải file .algo (thiếu tiêu đề 'algo')")
    stream = zlib.decompressobj(16 + zlib.MAX_WBITS)
    return json.loads(stream.decompress(data[9:]).decode("utf-8-sig"))


def ctrader_data_dir() -> Path:
    """Thư mục `Documents\\cAlgo\\Data\\cBots` nơi cTrader tự ghi 1 thư mục kết quả cho mỗi lần
    chạy CLI (`<sha256 .algo>\\<guid>\\Backtesting\\`). Đặt ở `ctrader.data_dir` trong config.yaml;
    thiếu thì dùng đường dẫn mặc định của cTrader. Hỗ trợ biến môi trường kiểu %USERPROFILE%."""
    raw = (_config().get("ctrader") or {}).get("data_dir")
    if raw:
        return Path(os.path.expandvars(str(raw)))
    return Path.home() / "Documents" / "cAlgo" / "Data" / "cBots"


def load_engine_profile(profile_id: str) -> EngineProfile:
    raw = _config()["engines"][profile_id]
    return EngineProfile.from_dict({**raw, "id": profile_id})


def load_redis_profile(profile_id: str) -> RedisProfile:
    if not isinstance(profile_id, str):
        raise ValueError("redis_profile must select exactly one profile")
    raw = _config()["redis"][profile_id]
    label = str(raw.get("label") or "").strip()
    if not label:
        raise ValueError(f"config.yaml redis.{profile_id}: missing `label` (source label stored in SQL: original / knn / breakout)")
    return RedisProfile(
        id=profile_id,
        host=str(raw["host"]),
        port=int(raw.get("port", 6379)),
        db=int(raw["db"]),
        username=str(raw.get("username", "")),
        pwd_file=str(raw["pwd_file"]),
        key_prefix=str(raw.get("key_prefix") or "L_PastSignal"),
        label=label,
    )


def load_backtest_settings() -> dict[str, Any]:
    """Static defaults for the one core backtest flow in config.yaml."""
    raw = _config().get("backtest") or {}
    if not isinstance(raw, Mapping):
        raise ValueError("backtest must be a mapping")
    return dict(raw)


def resolve_backtest_config(overrides: Mapping[str, Any]) -> dict[str, Any]:
    """Merge one runtime request onto the static backtest defaults."""
    settings = load_backtest_settings()
    defaults = settings.get("defaults") or {}
    if not isinstance(defaults, Mapping):
        raise ValueError("backtest.defaults must be a mapping")
    config = dict(defaults)
    fixed = dict(defaults.get("fixed_params") or {})
    fixed.update(overrides.get("fixed_params") or {})
    config.update(overrides)
    config["fixed_params"] = fixed
    runner = settings.get("runner") or {}
    if "max_parallel" not in config and isinstance(runner, Mapping) and "max_parallel" in runner:
        config["max_parallel"] = runner["max_parallel"]
    redis_profile = config.get("redis_profile")
    if not isinstance(redis_profile, str) or not redis_profile.strip():
        raise ValueError("redis_profile must select exactly one default source profile (a channel may override it with `source`)")
    config["redis_profile"] = redis_profile.strip()
    if not config.get("channels"):
        raise ValueError("channels must contain at least one signal channel")
    return config


@dataclass(frozen=True)
class PortfolioRiskSettings:
    """Mặc định phân bổ rủi ro Multi-channel (xem core_engine/risk_allocation.py)."""
    total_risk_percent: float
    risk_decimals: int


def load_portfolio_risk_settings() -> PortfolioRiskSettings:
    """Mục `backtest.portfolio` của config.yaml. Thiếu `total_risk_percent` là LỖI (không có hạn mức ngầm định);
    thiếu `risk_decimals` dùng `risk_allocation.DEFAULT_RISK_DECIMALS`."""
    raw = load_backtest_settings().get("portfolio")
    if not isinstance(raw, Mapping) or "total_risk_percent" not in raw:
        raise ValueError("config.yaml backtest.portfolio.total_risk_percent is required")
    return PortfolioRiskSettings(
        total_risk_percent=risk_allocation.validated_total_percent(raw["total_risk_percent"]),
        risk_decimals=risk_allocation.validated_decimals(raw.get("risk_decimals", risk_allocation.DEFAULT_RISK_DECIMALS)),
    )


def load_master_db_profile() -> MasterDbProfile:
    """Mục `master_db:` của config.yaml; thiếu mục thì coi là TẮT (pipeline không ghi SQL, file vẫn là nguồn sự thật)."""
    raw = _config().get("master_db") or {}
    return MasterDbProfile(
        enabled=bool(raw.get("enabled", False)),
        server=str(raw.get("server") or r"localhost\SQLEXPRESS"),
        database=str(raw.get("database") or "bo_research"),
        driver=str(raw.get("driver") or "ODBC Driver 18 for SQL Server"),
    )


def load_ftmo_profile(profile_id: str) -> FtmoProfile:
    raw = _config()["ftmo"][profile_id]
    return FtmoProfile(
        id=profile_id,
        timezone=raw["timezone"],
        initial_balance=float(raw["initial_balance"]),
        daily_loss_pct=float(raw["daily_loss_pct"]),
        max_loss_pct=float(raw["max_loss_pct"]),
        max_loss_type=raw.get("max_loss_type", "static"),
        daily_loss_base=raw.get("daily_loss_base", "start_of_day_balance"),
    )


