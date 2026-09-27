from __future__ import annotations

import enum
import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping
from zoneinfo import ZoneInfo


def parse_point_time(point: Mapping[str, Any]) -> datetime:
    """Đọc mốc giờ của 1 điểm equity/report — dùng chung cho selection.py
    (ftmo_screen) và montecarlo.py (từng có 2 bản giống hệt nhau, gộp lại
    2026-09-12 vì không có ranh giới nào cấm 2 module thuần chia sẻ 1 tiện ích
    đọc dữ liệu, nhưng thêm file thứ 11 thì không đáng cho đúng 5 dòng này)."""
    raw = point.get("time") or point.get("timestamp") or point.get("date")
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(raw / 1000 if raw > 10_000_000_000 else raw)
    return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))


def coerce_float(value: Any, default: float = 0.0) -> float:
    """Ép về float an toàn: giá trị None/chuỗi rác/NaN/inf đều trả `default`.

    [2026-09-22] Nâng từ `selection._num()` (riêng tư) lên đây khi tách
    `walkforward.py`: `plateau()` dời sang file mới nhưng `rank()`/
    `resolve_profit_factor()` ở lại selection.py — cùng cần hàm này. Thay vì
    để 1 file import ký hiệu gạch-dưới của file kia (phá vỡ quy ước riêng tư)
    hoặc chép lại 6 dòng ở 2 nơi, đưa vào đúng chỗ dành cho tiện ích THUẦN
    dùng chung — cùng lý do `parse_point_time()` ở trên đã nằm đây."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def daily_returns(
    points: Mapping[str, Any] | Any,
    timezone: str,
    initial: float,
    *,
    field_name: str = "balance",
) -> list[float]:
    """Gộp N điểm equity (report đã lưu) thành 1 chuỗi %lời/lỗ theo NGÀY —
    lấy giá trị CUỐI CÙNG ghi nhận mỗi ngày làm mốc "chốt ngày".

    [2026-09-22] Nâng từ `optimize/montecarlo.py::_daily_returns()` (riêng tư)
    lên đây khi thêm `optimize/dsr.py`+`optimize/pbo.py`: cả 2 module mới đều
    cần ĐÚNG bước này (đọc report đã lưu -> chuỗi ngày) để tính Sharpe/dựng ma
    trận N-trial — cùng lý do `coerce_float()`/`parse_point_time()` ở trên đã
    nằm đây thay vì để 3 file import ký hiệu gạch-dưới của nhau. Tham số đặt
    tên `field_name` (không phải `field`) để không đụng `dataclasses.field`
    đã import ở đầu file."""
    tz = ZoneInfo(timezone)
    closes: dict[str, float] = {}
    for point in points:
        dt = parse_point_time(point).astimezone(tz)
        value = float(point.get(field_name, initial))
        closes[dt.date().isoformat()] = value
    out: list[float] = []
    previous = initial
    for day in sorted(closes):
        close = closes[day]
        out.append((close - previous) / previous if previous else 0.0)
        previous = close
    return out


class RunStatus(str, enum.Enum):
    RUNNING = "running"
    OK = "ok"
    SKIPPED = "skipped"
    BUSY = "busy"
    CLI_ERROR = "cli_error"
    NO_REPORT = "no_report"
    PERIOD_MISMATCH = "period_mismatch"
    TIMEOUT = "timeout"
    STALLED = "stalled"
    CANCELLED = "cancelled"

    @property
    def terminal_ok(self) -> bool:
        return self in {RunStatus.OK, RunStatus.SKIPPED}


class FailureCode(str, enum.Enum):
    NONE = ""
    MESSAGE_EXPECTED = "message_expected"
    ACCESS_REQUIRED = "access_required"
    AUTH_FAILED = "auth_failed"
    HARD_TIMEOUT = "hard_timeout"
    PROGRESS_STALLED = "progress_stalled"
    NO_REPORT = "no_report"
    PERIOD_MISMATCH = "period_mismatch"
    PIPELINE_ERROR = "pipeline_error"
    ABANDONED = "abandoned"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    INVALID_JOB = "invalid_job"
    REPORT_MISMATCH = "report_mismatch"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class RunResult:
    """Hợp đồng CHUNG mà cả 2 engine (cli_engine, api_engine) phải trả về —
    dời từ cli_engine/runner.py 2026-09-17 khi tách 2 engine, vì facilitator.py
    (core_engine) cần đúng 1 kiểu dữ liệu dùng chung, không được mượn kiểu từ
    riêng engine nào (mượn từ cli_engine sẽ thành gọi chéo core_engine→cli_engine
    chỉ để lấy type). status/failure_code dùng chung enum ở trên; UNSUPPORTED_
    CAPABILITY/INVALID_JOB/REPORT_MISMATCH chỉ engine API dùng tới, CLI luôn trả
    NONE cho 3 mã đó."""
    status: RunStatus
    failure_code: FailureCode
    reason: str
    exit_code: int | None
    timed_out: bool
    wall_seconds: float
    started_utc: str
    ended_utc: str
    cli_path: str
    cli_version: str
    report_json: Path
    cli_log: Path
    bot_log: Path
    cbotset: Path


@dataclass(frozen=True)
class Window:
    # [2026-09-21] engine_start/evaluation_start + canonical() đã bỏ — audit
    # cho thấy không nơi nào tạo Window với 2 field đó khác None, và
    # canonical() không ai gọi. Thêm lại khi thực sự có nhu cầu (vd warm-up
    # period riêng biệt engine_start != evaluation start).
    name: str
    start: date
    end: date
    zone: str


# Port từ pipeline/core/plan.py::resolve_market_model — cùng ràng buộc, áp một
# lần lúc load profile (JSON review 1 lần) thay vì mỗi experiment.
COMMISSION_TYPES = frozenset({
    "UsdPerMillionUsdVolume", "UsdPerOneLot",
    "PercentageOfTradingVolume", "QuoteCurrencyPerOneLot",
})


ENGINE_BACKENDS = frozenset({"cli", "api"})


@dataclass(frozen=True)
class EngineProfile:
    id: str
    cli_version: str
    data_mode: str = "ticks"
    precise_conversion: bool = False
    commission: float | None = None
    commission_type: str | None = None
    commission_auto: bool = False
    spread: float | None = None
    # [2026-09-17] Chọn engine thật thi hành backtest — "cli" (spawn
    # ctrader-cli.exe, cli_engine/cli_runner.py) hoặc "api" (Plugin cTrader
    # Desktop qua bridge, api_engine/api_runner.py). facilitator.py đọc đúng
    # field này để rẽ nhánh, không suy đoán từ tên profile.
    backend: str = "cli"

    def __post_init__(self) -> None:
        if self.commission_type is not None and self.commission_type not in COMMISSION_TYPES:
            raise ValueError(
                f"engine profile {self.id!r}: commission_type {self.commission_type!r} "
                f"không thuộc {sorted(COMMISSION_TYPES)}"
            )
        if self.commission_auto and (self.commission is not None or self.commission_type is not None):
            raise ValueError(
                f"engine profile {self.id!r}: commission_auto không được đi cùng "
                "commission/commission_type"
            )
        if self.commission_type is not None and self.commission is None:
            raise ValueError(f"engine profile {self.id!r}: commission_type cần commission")
        if self.backend not in ENGINE_BACKENDS:
            raise ValueError(
                f"engine profile {self.id!r}: backend {self.backend!r} không thuộc "
                f"{sorted(ENGINE_BACKENDS)}"
            )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "EngineProfile":
        return cls(
            id=str(raw["id"]),
            cli_version=str(raw.get("cli_version", "")),
            data_mode=str(raw.get("data_mode", "ticks")).lower(),
            precise_conversion=bool(raw.get("precise_conversion", False)),
            commission=raw.get("commission"),
            commission_type=raw.get("commission_type"),
            commission_auto=bool(raw.get("commission_auto", False)),
            spread=raw.get("spread"),
            backend=str(raw.get("backend", "cli")).lower(),
        )

    def canonical(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "cli_version": self.cli_version,
            "data_mode": self.data_mode,
            "precise_conversion": self.precise_conversion,
            "commission": self.commission,
            "commission_type": self.commission_type,
            "commission_auto": self.commission_auto,
            "spread": self.spread,
            "backend": self.backend,
        }


@dataclass(frozen=True)
class RunSpec:
    strategy: str
    strategy_profile: str
    engine_profile: str
    symbol: str
    timeframe: str
    start: date
    end: date
    balance: float
    data_mode: str
    params: Mapping[str, str]
    zone: str = "single"
    window: str | None = None
    data_file: str | None = None
    precise_conversion: bool = False
    commission: float | None = None
    commission_type: str | None = None
    commission_auto: bool = False
    spread: float | None = None
    tags: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))
        object.__setattr__(self, "tags", MappingProxyType(dict(self.tags)))

    def canonical(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "strategy_profile": self.strategy_profile,
            "engine_profile": self.engine_profile,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            # Danh dau quy uoc ngay ngay TRONG canonical doc: experiment cu (end
            # hieu la "bao gom") se khong bao gio so bang voi experiment moi, nen
            # preflight bao "config KHAC" thay vi am tham dung lai ket qua cu chay
            # tren khoang dai hon 1 ngay.
            "date_convention": "end_exclusive",
            "balance": float(self.balance),
            "data_mode": self.data_mode,
            "data_file": self.data_file,
            "precise_conversion": self.precise_conversion,
            "commission": self.commission,
            "commission_type": self.commission_type,
            "commission_auto": self.commission_auto,
            "spread": self.spread,
            "zone": self.zone,
            "window": self.window,
            "params": {k: str(v) for k, v in sorted(self.params.items())},
            "tags": {k: str(v) for k, v in sorted(self.tags.items())},
        }

    def canonical_json(self) -> str:
        return json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"))

    def label(self) -> str:
        bits = [self.strategy, self.symbol, self.timeframe, self.zone]
        for key, tag in (("KslLevel", "ksl"), ("KtpLevel", "ktp"), ("RiskPercent", "r")):
            if key in self.params:
                bits.append(f"{tag}{str(self.params[key]).replace('.', 'p')}")
        return "__".join(bits)


@dataclass(frozen=True)
class Experiment:
    name: str
    method: str
    strategy: str
    strategy_profile: str
    engine_profile: str
    config: Mapping[str, Any]
    locked: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "config", MappingProxyType(dict(self.config)))

    def canonical(self) -> dict[str, Any]:
        return {
            "schema": "bo-research-experiment/v1",
            "name": self.name,
            "method": self.method,
            "strategy": self.strategy,
            "strategy_profile": self.strategy_profile,
            "engine_profile": self.engine_profile,
            "locked": self.locked,
            "config": json.loads(json.dumps(dict(self.config), default=str)),
        }
