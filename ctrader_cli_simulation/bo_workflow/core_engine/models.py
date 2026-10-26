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


def _points_by_day(points: Any, timezone: str) -> dict[str, list[Mapping[str, Any]]]:
    """Nhóm điểm equity theo ngày lịch `timezone`, giữ đúng thứ tự đầu vào
    trong mỗi ngày (điểm cuối danh sách = điểm ghi nhận sau cùng của ngày)."""
    tz = ZoneInfo(timezone)
    days: dict[str, list[Mapping[str, Any]]] = {}
    for point in points:
        day = parse_point_time(point).astimezone(tz).date().isoformat()
        days.setdefault(day, []).append(point)
    return days


def daily_returns_by_day(
    points: Mapping[str, Any] | Any,
    timezone: str,
    initial: float,
    *,
    field_name: str = "balance",
) -> list[tuple[str, float]]:
    """Như `daily_returns()` nhưng giữ kèm ngày (ISO) của từng return — cần
    khi phải gắn thêm dữ liệu khác (vd lệnh đóng trong ngày) vào ĐÚNG ngày
    của chuỗi, không đoán lại cách chia ngày ở nơi khác."""
    out: list[tuple[str, float]] = []
    previous = initial
    for day, bucket in sorted(_points_by_day(points, timezone).items()):
        close = float(bucket[-1].get(field_name, initial))
        out.append((day, (close - previous) / previous if previous else 0.0))
        previous = close
    return out


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
    return [value for _day, value in daily_returns_by_day(points, timezone, initial, field_name=field_name)]


def daily_balance_and_floor(
    points: Mapping[str, Any] | Any,
    timezone: str,
    initial: float,
) -> list[tuple[float, float]]:
    """Mỗi ngày 1 cặp (return theo balance chốt ngày, tỷ lệ sụt trong ngày).

    Luật lỗ ngày FTMO: mốc là balance lúc 00:00 (= balance chốt ngày hôm
    trước), so với equity THẤP NHẤT trong ngày. Report cTrader chỉ có
    `balance` (điểm chốt) và `minEquity` (đáy của TỪNG nến), nên 2 khái niệm
    phải lấy từ 2 nguồn riêng: return nhân dồn qua ngày dùng `balance` của
    điểm cuối ngày; sụt trong ngày = (balance chốt hôm trước − đáy `minEquity`
    của MỌI điểm trong ngày) / balance chốt hôm trước. Lấy `minEquity` của
    điểm cuối ngày (cách cũ) chỉ là đáy của nến cuối, bỏ sót mọi đợt lỗ nổi
    sâu hơn xảy ra sớm hơn trong ngày.

    Tỷ lệ (không phải số tiền) để Monte Carlo áp lại được lên số dư mô phỏng
    khác với lịch sử. Điểm thiếu `minEquity` coi đáy = `balance` của nó."""
    out: list[tuple[float, float]] = []
    previous = initial
    for _day, bucket in sorted(_points_by_day(points, timezone).items()):
        close = float(bucket[-1].get("balance", initial))
        low = min(float(p.get("minEquity", p.get("balance", initial))) for p in bucket)
        if previous:
            out.append(((close - previous) / previous, (previous - low) / previous))
        else:
            out.append((0.0, 0.0))
        previous = close
    return out


def daily_equity_rows(points: Mapping[str, Any] | Any, timezone: str, initial: float) -> list[dict[str, Any]]:
    """Mỗi ngày lịch `timezone` (giờ Prague cho luật FTMO) 1 dòng cho bảng master `daily_equity`: balance chốt
    ngày và equity THẤP NHẤT trong ngày (từ `minEquity` của MỌI điểm). Balance lúc 00:00 của một ngày chính là
    balance chốt ngày trước (ngày đầu = `initial`) nên không lưu — view v_ftmo_daily_loss tính bằng LAG. Chia ngày
    bằng cùng `_points_by_day` với `daily_balance_and_floor` nên hai nơi không thể lệch nhau."""
    rows: list[dict[str, Any]] = []
    previous = initial
    for day, bucket in sorted(_points_by_day(points, timezone).items()):
        close = float(bucket[-1].get("balance", previous))
        rows.append({
            "trading_day": day,
            "balance_at_close": close,
            "lowest_equity": min(float(p.get("minEquity", p.get("balance", previous))) for p in bucket),
        })
        previous = close
    return rows


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
    """Hợp đồng kết quả 1 lần chạy mà engine trả về cho facilitator.py —
    dời từ cli_engine/runner.py 2026-09-17 khi tách 2 engine, vì facilitator.py
    (core_engine) cần đúng 1 kiểu dữ liệu dùng chung, không được mượn kiểu từ
    riêng engine nào (mượn từ cli_engine sẽ thành gọi chéo core_engine→cli_engine
    chỉ để lấy type). status/failure_code dùng chung enum ở trên; UNSUPPORTED_
    CAPABILITY/INVALID_JOB/REPORT_MISMATCH từng chỉ engine API dùng (api_engine
    đã xoá 2026-10-13), CLI luôn trả NONE cho 3 mã đó."""
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
    # events.json của cTrader (vòng đời lệnh) đã chép về work_dir + thư mục gốc của nó; None/"" khi
    # không nối được đúng 1 thư mục (xem cli_runner.find_native_output) — chỉ thiếu events, run vẫn hợp lệ.
    events_json: Path | None = None
    native_dir: str = ""


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


ENGINE_BACKENDS = frozenset({"cli"})


@dataclass(frozen=True)
class EngineProfile:
    id: str
    data_mode: str = "ticks"
    precise_conversion: bool = False
    commission: float | None = None
    commission_type: str | None = None
    commission_auto: bool = False
    spread: float | None = None
    # [2026-09-17] Chọn engine thật thi hành backtest — hiện chỉ còn "cli"
    # (spawn ctrader-cli.exe, core_engine/cli_runner.py); engine "api" (Plugin
    # qua bridge) đã xoá 2026-10-13. facilitator.py đọc đúng field này để chọn
    # engine, không suy đoán từ tên profile.
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
            data_mode=str(raw.get("data_mode", "ticks")).lower(),
            precise_conversion=bool(raw.get("precise_conversion", False)),
            commission=raw.get("commission"),
            commission_type=raw.get("commission_type"),
            commission_auto=bool(raw.get("commission_auto", False)),
            spread=raw.get("spread"),
            backend=str(raw.get("backend", "cli")).lower(),
        )


# Nguồn RAW (db0, không lọc trend) là nguồn mặc định và KHÔNG đổi nghĩa: không ghi vào canonical của kênh để run_key của
# mọi lượt RAW đã chạy giữ nguyên. Nhà chung ở đây (models.py) để configuration.py dùng lại mà không import vòng.
RAW_SIGNAL_SOURCE = "sen05-signal-v1"


@dataclass(frozen=True)
class SignalChannel:
    """One independent signal stream inside a BacktestBot account.

    `source` = id profile Redis cấp tín hiệu cho kênh này ("" = nguồn mặc định của lượt chạy). Nguồn KHÁC RAW đi vào
    canonical (nên vào run_key) để cùng một kênh lấy từ KNN và từ breakout không bao giờ trùng khoá."""
    strategy: str
    symbol: str
    timeframe: str
    risk_percent: float
    source: str = ""

    def canonical(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "strategy": self.strategy,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "risk_percent": float(self.risk_percent),
        }
        if self.source and self.source != RAW_SIGNAL_SOURCE:
            out["source"] = self.source
        return out


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
    channels: tuple[SignalChannel, ...] = ()
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
        object.__setattr__(self, "channels", tuple(self.channels))

    def canonical(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "strategy_profile": self.strategy_profile,
            "engine_profile": self.engine_profile,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            # Danh dau quy uoc ngay ngay TRONG canonical doc: experiment cu se
            # khong bao gio so bang voi experiment moi khi quy uoc doi, nen
            # preflight bao "config KHAC" thay vi am tham dung lai ket qua cu.
            # `end` di thang vao --end va ctrader-cli chay TRON ngay do (do
            # 2026-10-13: diem equity cuoi 23:59 cua ngay end) -> "end_inclusive".
            # Truoc 2026-10-14 nhan nay ghi sai la "end_exclusive".
            "date_convention": "end_inclusive",
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
            "channels": [channel.canonical() for channel in self.channels],
            "tags": {k: str(v) for k, v in sorted(self.tags.items())},
        }

    def canonical_json(self) -> str:
        return json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"))

    def label(self) -> str:
        bits = [self.strategy, self.symbol, self.timeframe, self.zone]
        for key, tag in (("KslLevel", "ksl"), ("KtpLevel", "ktp"), ("RiskPercent", "r")):
            if key in self.params:
                bits.append(f"{tag}{str(self.params[key]).replace('.', 'p')}")
        # combo-legs-v1: chỉ ghi kTP của các chân đang dùng (Leg2Ktp..Leg{LegCount}Ktp), để 2 run khác số chân/kTP chân không trùng nhãn.
        if "LegCount" in self.params:
            legs = int(self.params["LegCount"])
            bits.append(f"legs{legs}")
            bits.extend(f"k{leg}_{self.params[f'Leg{leg}Ktp']}" for leg in range(2, legs + 1) if f"Leg{leg}Ktp" in self.params)
        return "__".join(bits)


@dataclass(frozen=True)
class OptimizeBox:
    """RunSpec cùng bối cảnh mà tham số quét `dims` tạo thành tích Descartes — chạy bằng 1 lệnh `ctrader-cli optimize`."""
    specs: tuple[RunSpec, ...]
    dims: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def __post_init__(self) -> None:
        if not self.specs or len(self.specs) != math.prod(len(values) for _, values in self.dims):
            raise ValueError(f"OptimizeBox: {len(self.specs)} spec không khớp dims {[name for name, _ in self.dims]}")


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
