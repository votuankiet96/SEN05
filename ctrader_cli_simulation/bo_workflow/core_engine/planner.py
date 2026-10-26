"""planner.py — biến config dict thành danh sách `RunSpec` để engine chạy.

Ranh giới [2026-09-22]: chỉ giữ phần chia cửa sổ/dựng spec dùng cho MỌI phương
pháp (`research_windows()` cho 1 cửa sổ hoặc train/validation, `expand_grid()`
cho lưới tham số, các validator `positive_int`/`as_date`...). Phần chia cửa sổ
trượt riêng của walk-forward (`walkforward_windows()` + helper `_month_add()`)
đã dời sang `walkforward.py` vì không nơi nào khác dùng tới.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
from datetime import date, datetime, timedelta
from typing import Any, Mapping, Sequence

from .configuration import (
    DEFAULT_REDIS_PROFILE, STRATEGY_CODES, StrategyProfile, load_backtest_settings,
    load_strategy_profile, with_signal_overrides,
)
from .models import EngineProfile, Experiment, OptimizeBox, RunSpec, Window


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


def run_name(config: Mapping[str, Any]) -> str:
    """Tên lượt (experiment) cho profile nhiều chiến lược:
    `<mã>[-<mã>]_S<số symbol>_T<số khung>_L<số chân>_<bắt đầu>_<kết thúc>_<hash6>`, vd `cb_S5_T4_L3_20260101_20261010_7f3a9c`
    hoặc `cb-mc_S2_T8_L3_...`. Phần đọc được cho NGƯỜI (tra theo tiền tố trong cột `batch_name` của kho SQL); `S` = số
    symbol, `T` = số khung KHÁC NHAU gộp mọi chiến lược đã chọn, `L` = LegCount. Đuôi hash6 phân biệt các lượt cùng
    S/T/L/ngày nhưng khác tập symbol, tập khung từng chiến lược, tham số (KSL/KTP/risk/chân...), lưới tham số, vốn, engine,
    nguồn tín hiệu (db0/db1/db2, mặc định của lượt và `source` của từng channel). Đây KHÔNG phải khoá tra chính xác — khoá chính xác là `run_key` của kho SQL (gồm cả hash `.algo`
    và hash nội dung tín hiệu, nên đổi bản bot hay tín hiệu Redis thì khác). Thứ tự chiến lược/symbol người dùng khai không
    đổi tên. `S` đếm symbol khai ở `signal_symbols`, thiếu thì ở `symbols`."""
    profile = with_signal_overrides(load_strategy_profile(str(config["strategy_profile"])), config)
    if not profile.signal_strategies:
        raise ValueError(f"{profile.id}: run_name chỉ dành cho profile nhiều chiến lược")
    symbols = profile.signal_symbols or tuple(_symbols(config))
    frames = {token: list(profile.strategy_frames[token]) for token in profile.signal_strategies}
    fixed = profile.resolve_params(config.get("fixed_params") or {})
    windows = research_windows(config)
    start, end = min(item.start for item in windows), max(item.end for item in windows)
    definition = {
        "strategy_profile": profile.id,
        "strategies": frames,
        "symbols": sorted(symbols),
        "channels": [item.canonical() for item in profile.signal_channels],
        "account_mode": str(config.get("account_mode", "isolated")),
        "params": fixed,
        "space": {str(key): value for key, value in (config.get("parameter_space") or config.get("params") or {}).items()},
        "balance": float(config.get("balance", 100000)),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "engine_profile": str(config["engine_profile"]),
        "redis_profile": str(config.get("redis_profile", DEFAULT_REDIS_PROFILE)),
    }
    digest = hashlib.sha256(
        json.dumps(definition, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()[:6]
    codes = "-".join(STRATEGY_CODES[token] for token in profile.signal_strategies)
    timeframes = {item for values in frames.values() for item in values}
    return f"{codes}_S{len(symbols)}_T{len(timeframes)}_L{int(fixed['LegCount'])}_{start:%Y%m%d}_{end:%Y%m%d}_{digest}"


def _single_window(config: Mapping[str, Any]) -> Window:
    start = as_date(config["start"])
    end = as_date(config["end"])
    if start > end:
        raise ValueError("start must be <= end")
    return Window("single", start, end, str(config.get("zone", "single")))


def research_windows(config: Mapping[str, Any]) -> list[Window]:
    period_group = config.get("period_group")
    if period_group:
        groups = load_backtest_settings().get("period_groups") or {}
        if period_group not in groups:
            raise KeyError(f"unknown backtest period_group: {period_group!r}")
        raw = groups[period_group]
        start_year = positive_int(raw["start_year"], "start_year")
        cutoff = as_date(raw["cutoff"])
        if cutoff not in {date(cutoff.year, 6, 30), date(cutoff.year, 12, 31)}:
            raise ValueError("backtest cutoff must be a completed half-year boundary (June 30 or December 31)")
        if start_year > cutoff.year:
            raise ValueError("period_group start_year must not exceed cutoff year")
        windows: list[Window] = []
        for year in range(start_year, cutoff.year + 1):
            for half, start, end in (
                ("h1", date(year, 1, 1), date(year, 6, 30)),
                ("h2", date(year, 7, 1), date(year, 12, 31)),
            ):
                if end <= cutoff:
                    windows.append(Window(f"{half}_{str(year)[2:]}", start, end, "half"))
        for year in range(start_year, cutoff.year + 1):
            end = date(year, 12, 31)
            if end <= cutoff:
                windows.append(Window(f"y{str(year)[2:]}", date(year, 1, 1), end, "year"))
        for year in range(start_year, cutoff.year):
            windows.append(Window(f"c{str(year)[2:]}", date(year, 1, 1), cutoff, "cumulative"))
        return windows
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
    balance = finite_positive_float(config.get("balance", 100000), "balance")
    combos = _param_combinations(config, profile)
    specs: list[RunSpec] = []
    seen: set[str] = set()
    if profile.signal_channels:
        sweep = config.get("parameter_space") or config.get("params") or {}
        if "RiskPercent" in sweep:
            raise ValueError("RiskPercent sweep is not allowed with channels; set risk_percent on each channel")
        mode = str(config.get("account_mode", "isolated")).strip().lower()
        if mode not in {"isolated", "shared"}:
            raise ValueError("account_mode must be 'isolated' or 'shared'")
        channel_groups = [(channel,) for channel in profile.signal_channels] if mode == "isolated" else [profile.signal_channels]
        anchors = [
            (group[0].symbol, group[0].timeframe.lower(), group)
            for group in channel_groups
        ]
    else:
        anchors = [(symbol, timeframe, ()) for symbol, timeframe in itertools.product(_symbols(config), _timeframes(config, profile))]
    for window, (symbol, timeframe, channels), params in itertools.product(windows, anchors, combos):
        run_params = dict(params)
        if len(channels) == 1:
            run_params["RiskPercent"] = profile.params["RiskPercent"].encode(channels[0].risk_percent)
        spec_strategy = "+".join(dict.fromkeys(item.strategy for item in channels)) if channels else profile.strategy
        spec = RunSpec(
            strategy=spec_strategy,
            strategy_profile=profile.id,
            engine_profile=engine.id,
            symbol=symbol,
            timeframe=timeframe,
            start=window.start,
            end=window.end,
            balance=balance,
            data_mode=engine.data_mode,
            params=run_params,
            channels=channels,
            zone=window.zone,
            window=window.name,
            # [2026-09-21] Luôn ticks (server data) — nhánh CSV cục bộ
            # (m1-csv/tick-csv, cần data_file) đã bỏ: không engine profile nào
            # dùng, chỉ tổ máy vô dụng. RunSpec.data_file vẫn còn trong
            # model nhưng planner không còn gán gì cho nó.
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

def _is_arithmetic(values: list[float], *, integer: bool) -> bool:
    """`optimize` chỉ nhận dải Min/Max/Step, tức dãy cách đều (kiểu số nguyên: bước nguyên)."""
    ordered = sorted(values)
    step = ordered[1] - ordered[0]
    return step > 0 and (not integer or step.is_integer()) and all(
        abs(b - a - step) <= 1e-9 * max(1.0, step) for a, b in zip(ordered, ordered[1:])
    )


def _split_box(names: list[str], kinds: Mapping[str, str], dims: list[list[str]], chunk: int) -> list[list[list[str]]]:
    """Cắt tích Descartes `dims` thành các hộp <= chunk pass; chiều số không cách đều thì mỗi giá trị 1 hộp."""
    for i, values in enumerate(dims):
        kind = kinds[names[i]]
        if kind in {"int", "float"} and len(values) > 2 and not _is_arithmetic([float(v) for v in values], integer=kind == "int"):
            return [box for v in values for box in _split_box(names, kinds, [*dims[:i], [v], *dims[i + 1:]], chunk)]
    sizes = [len(v) for v in dims]
    if math.prod(sizes) <= chunk:
        return [dims]
    cut = len(dims) - 1          # chiều bị cắt lát; các chiều sau nó giữ nguyên
    while cut > 0 and math.prod(sizes[cut:]) <= chunk:
        cut -= 1
    step = chunk // math.prod(sizes[cut + 1:])
    return [
        [[v] for v in head] + [dims[cut][i:i + step]] + dims[cut + 1:]
        for head in itertools.product(*dims[:cut])
        for i in range(0, sizes[cut], step)
    ]


def plan_optimize_boxes(specs: Sequence[RunSpec], strategy: StrategyProfile, chunk_size: int) -> list[OptimizeBox]:
    """Gom spec cùng bối cảnh thành hộp <= chunk_size pass chạy được bằng 1 lệnh `optimize`;
    spec không vào được hộp nào (không đủ tích Descartes) thành hộp 1 spec."""
    chunk = positive_int(chunk_size, "optimize_chunk_size")
    groups: dict[str, list[RunSpec]] = {}
    for spec in specs:
        context = json.dumps({k: v for k, v in spec.canonical().items() if k != "params"}, sort_keys=True)
        groups.setdefault(context, []).append(spec)
    boxes: list[OptimizeBox] = []
    for members in groups.values():
        names = [n for n in strategy.params if len({s.params[n] for s in members}) > 1]
        kinds = {n: strategy.params[n].kind for n in names}
        dims = [sorted({s.params[n] for s in members}, key=str if kinds[n] == "bool" else float) for n in names]
        by_point = {tuple(s.params[n] for n in names): s for s in members}
        if not names or len(by_point) != len(members) or len(members) != math.prod(map(len, dims)):
            boxes += [OptimizeBox((s,)) for s in members]
            continue
        for piece in _split_box(names, kinds, dims, chunk):
            swept = tuple((n, tuple(v)) for n, v in zip(names, piece) if len(v) > 1)
            boxes.append(OptimizeBox(tuple(by_point[p] for p in itertools.product(*piece)), swept))
    return boxes
