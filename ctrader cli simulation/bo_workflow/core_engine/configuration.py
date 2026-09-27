"""Đọc + kiểm tra bo_workflow/config.yaml (cấu hình kỹ thuật + lựa chọn người dùng), đối
chiếu với contract THẬT của .algo. Đây là phần "điều phối cấu hình" — không
gọi ctrader-cli/bridge API (2 engine riêng giữ độc quyền đó), không ghi gì
xuống đĩa.

Đổi tên từ catalog.py 2026-09-12, gộp thêm 2 loader (engine/ftmo) từng nằm rải
rác ở api.py hoặc hoàn toàn chưa có — giờ MỌI profile đều load qua đúng 1 nơi.
Dời vào core_engine/ 2026-09-17 khi tách 2 engine (cli_engine/, api_engine/)
ra làm 2 package ngang hàng — file này là lõi dùng chung, không thuộc engine
nào cả.

[2026-09-21] Thu hẹp lại đúng 1 vai "profile + tự-validate": SYMBOL_GROUPS/
SIGNAL_EXPORT_DIR/khuôn tên CSV tĩnh dời sang signal_trans.py (câu hỏi "signal
đến từ đâu" không phải "cấu hình chiến lược là gì"); validate_metadata() đổi
từ hàm rời sang method StrategyProfile.validate_metadata().
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from .models import EngineProfile

# File này ở bo_workflow/core_engine/configuration.py -> ROOT = core_engine.
# [2026-09-21] config.yaml dời ra bo_workflow/ (ngang hàng core_engine/cli_engine/
# api_engine, dùng chung cho cả hệ chứ không riêng core_engine) -> CONFIG_PATH
# = ROOT.parent / "config.yaml". ROBOTS_DIR trèo thêm 1 cấp nữa (core_engine ->
# bo_workflow -> Sources/Robots) mới tới chỗ Combo.algo/MA Cross.algo thật nằm.
ROOT = Path(__file__).resolve().parent
ROBOTS_DIR = ROOT.parent.parent
CONFIG_PATH = ROOT.parent / "config.yaml"

# [2026-09-16] Doi het gia tri sang phi^(n/2) (phi=1.618034) - KHONG con
# Fib2000/Fib3618/Fib4618/Fib0236 (so tron/quy uoc cu). Thu tu duoi day PHAI
# khop CHINH XAC thu tu enum SlFibLevel/TpFibLevel that trong Combo.cs va
# MA Cross.cs (encode() dung index() tren tuple nay -> lech thu tu = sai lenh
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
            if pdef.encode(row.get("DefaultValue")) != pdef.encode(pdef.default):
                raise ValueError(f"{name}: default drift")
        return {
            "name": metadata.get("Name"),
            "type": metadata.get("Type"),
            "access_rights": metadata.get("AccessRights"),
            "parameter_count": len(actual),
        }


@dataclass(frozen=True)
class RedisProfile:
    """Kết nối Redis nguồn tín hiệu (thay CSV) — vd DB2 do strategy_lab (SEN05)
    cấp. CHỈ chứa đường dẫn file mật khẩu (`pwd_file`), giống hệt nguyên tắc đã
    dùng cho `CTRADER_PWD_FILE`/`.ctrader-cli-pwd.txt` — KHÔNG BAO GIỜ hardcode
    mật khẩu thật vào config.yaml hay bất kỳ đâu khác trong repo.

    `key_prefix` (2026-09-26): strategy_lab giờ publish 2 kênh song song trên
    cùng Redis server — DB2 "L_PastSignal" (không lọc trend) và DB3
    "L_PastSignal_Trend" (có lọc trend khung lớn, xem sl_config.yaml/worker.py
    bên strategy_lab). Khác nhau đúng 1 chỗ: tiền tố key List/Hash. Mặc định
    "L_PastSignal" giữ nguyên hành vi DB2 cho mọi profile cũ không khai field
    này trong config.yaml."""
    id: str
    host: str
    port: int
    db: int
    username: str
    pwd_file: str
    key_prefix: str = "L_PastSignal"

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
    )


def load_engine_profile(profile_id: str) -> EngineProfile:
    raw = _config()["engines"][profile_id]
    return EngineProfile.from_dict({**raw, "id": profile_id})


def load_redis_profile(profile_id: str) -> RedisProfile:
    raw = _config()["redis"][profile_id]
    return RedisProfile(
        id=profile_id,
        host=str(raw["host"]),
        port=int(raw.get("port", 6379)),
        db=int(raw["db"]),
        username=str(raw.get("username", "")),
        pwd_file=str(raw["pwd_file"]),
        key_prefix=str(raw.get("key_prefix") or "L_PastSignal"),
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


