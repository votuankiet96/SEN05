"""planner.py — biến config dict thành danh sách `RunSpec` để engine chạy.

Ranh giới [2026-09-22]: chỉ giữ phần chia cửa sổ/dựng spec dùng cho MỌI phương
pháp (`research_windows()` cho 1 cửa sổ hoặc train/validation, `expand_grid()`
cho lưới tham số, các validator `positive_int`/`as_date`...). Phần chia cửa sổ
trượt riêng của walk-forward (`walkforward_windows()` + helper `_month_add()`)
đã dời sang `walkforward.py` vì không nơi nào khác dùng tới.
"""
from __future__ import annotations

import itertools
import math
from datetime import date, datetime, timedelta
from typing import Any, Mapping

from .configuration import StrategyProfile
from .models import EngineProfile, Experiment, RunSpec, Window


def as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive integer")
    text = str(value).strip()
    parsed = int(text)
    if parsed <= 0 or text not in {str(parsed), f"{parsed}.0"}:
        raise ValueError(f"{field} must be a positive integer")
    return parsed


def nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a non-negative integer")
    text = str(value).strip()
    parsed = int(text)
    if parsed < 0 or text not in {str(parsed), f"{parsed}.0"}:
        raise ValueError(f"{field} must be a non-negative integer")
    return parsed


def finite_positive_float(value: Any, field: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{field} must be finite and > 0")
    return number


def _list(value: Any, predefined: tuple[Any, ...] = ()) -> list[Any]:
    if value is True:
        if not predefined:
            raise ValueError("True requires predefined levels")
        return list(predefined)
    if isinstance(value, (list, tuple)):
        if not value:
            raise ValueError("empty list is not allowed")
        return list(value)
    return [value]


def _symbols(config: Mapping[str, Any]) -> list[str]:
    raw = config.get("symbols") or config.get("universe", {}).get("symbols")
    if raw is None:
        raise KeyError("symbols")
    out: list[str] = []
    for item in _list(raw):
        text = str(item).strip()
        if not text:
            raise ValueError("symbol cannot be empty")
        if text not in out:
            out.append(text)
    return out


def _timeframes(config: Mapping[str, Any], profile: StrategyProfile) -> list[str]:
    raw = config.get("timeframe", config.get("universe", {}).get("timeframes", True))
    out: list[str] = []
    for item in _list(raw, profile.default_timeframes):
        text = str(item).strip().lower()
        if not text:
            raise ValueError("timeframe cannot be empty")
        if text not in out:
            out.append(text)
    return out


def build_experiment(config: Mapping[str, Any]) -> Experiment:
    return Experiment(
        name=str(config["name"] if "name" in config else config["experiment"]),
        method=str(config.get("method", "grid")).lower(),
        strategy=str(config["strategy"]).lower().replace(" ", ""),
        strategy_profile=str(config["strategy_profile"]),
        engine_profile=str(config["engine_profile"]),
        locked=bool(config.get("locked", True)),
        config=dict(config),
    )


def _single_window(config: Mapping[str, Any]) -> Window:
    start = as_date(config["start"])
    end = as_date(config["end"])
    if start > end:
        raise ValueError("start must be <= end")
    return Window("single", start, end, str(config.get("zone", "single")))


def research_windows(config: Mapping[str, Any]) -> list[Window]:
    zones = config.get("zones")
    if not isinstance(zones, Mapping):
        return [_single_window(config)]
    embargo = nonnegative_int(zones.get("embargo_days", 0), "embargo_days")
    windows: list[Window] = []
    previous_end: date | None = None
    for name in ("train", "validation"):
        if name not in zones:
            continue
        start, end = map(as_date, zones[name])
        if start > end:
            raise ValueError(f"{name}: start must be <= end")
        if previous_end and start <= previous_end + timedelta(days=embargo):
            raise ValueError(f"{name}: violates embargo_days={embargo}")
        windows.append(Window(name, start, end, name))
        previous_end = end
    return windows


def _param_combinations(config: Mapping[str, Any], profile: StrategyProfile) -> list[dict[str, str]]:
    fixed = profile.resolve_params(config.get("fixed_params") or {})
    space = config.get("parameter_space") or config.get("params") or {}
    grids = {name: profile.param_levels(name, value) for name, value in space.items()}
    keys = list(grids)
    if not keys:
        return [fixed]
    combos: list[dict[str, str]] = []
    for values in itertools.product(*(grids[k] for k in keys)):
        row = dict(fixed)
        row.update(zip(keys, values))
        combos.append(row)
    return combos


def expand_grid(
    config: Mapping[str, Any],
    profile: StrategyProfile,
    engine: EngineProfile,
    windows: list[Window] | None = None,
) -> list[RunSpec]:
    windows = windows or research_windows(config)
    symbols = _symbols(config)
    timeframes = _timeframes(config, profile)
    balance = finite_positive_float(config.get("balance", 100000), "balance")
    combos = _param_combinations(config, profile)
    specs: list[RunSpec] = []
    seen: set[str] = set()
    for window, symbol, timeframe, params in itertools.product(windows, symbols, timeframes, combos):
        spec = RunSpec(
            strategy=profile.strategy,
            strategy_profile=profile.id,
            engine_profile=engine.id,
            symbol=symbol,
            timeframe=timeframe,
            start=window.start,
            end=window.end,
            balance=balance,
            data_mode=engine.data_mode,
            params=params,
            zone=window.zone,
            window=window.name,
            # [2026-09-21] Luôn ticks (server data) — nhánh CSV cục bộ
            # (m1-csv/tick-csv, cần data_file) đã bỏ: không engine profile nào
            # dùng, chỉ tổ máy vô dụng. RunSpec.data_file vẫn giữ (api_engine
            # contract cần field này) nhưng planner không còn gán gì cho nó.
            data_file=None,
            precise_conversion=engine.precise_conversion,
            commission=engine.commission,
            commission_type=engine.commission_type,
            commission_auto=engine.commission_auto,
            spread=engine.spread,
        )
        key = spec.canonical_json()
        if key not in seen:
            seen.add(key)
            specs.append(spec)
    return specs
