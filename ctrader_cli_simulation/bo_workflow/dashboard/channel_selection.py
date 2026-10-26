"""Pure canonicalisation for one BacktestBot multi-channel dashboard run."""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping


def _choices(values: Any, *, upper: bool = False) -> list[str]:
    raw_values = [values] if isinstance(values, str) else list(values or [])
    result: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        value = str(raw).strip()
        value = value.upper() if upper else value
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def canonical_selection(
    profile: Any,
    strategies: Iterable[Any] | Any,
    symbols: Iterable[Any] | Any,
    timeframes: Iterable[Any] | Any,
    *,
    allowed_symbols: Iterable[str],
) -> dict[str, Any]:
    """Validate and order a global BacktestBot channel selection.

    ``signal_timeframes`` is a global filter in core.  Every selected strategy
    receives its intersection with that filter, exactly mirroring
    ``configuration.with_signal_overrides``.  The returned ordering is stable
    so browser selection order cannot alter a session identity or simultaneous
    signal processing order.
    """
    strategy_catalog = tuple(str(token) for token in profile.strategy_frames)
    requested_strategies = set(_choices(strategies))
    unknown_strategies = sorted(requested_strategies.difference(strategy_catalog))
    if unknown_strategies:
        raise ValueError(f"Unsupported strategies: {', '.join(unknown_strategies)}")
    selected_strategies = tuple(token for token in strategy_catalog if token in requested_strategies)
    if not selected_strategies:
        raise ValueError("Select at least one strategy")

    symbol_catalog = tuple(sorted(str(symbol) for symbol in allowed_symbols))
    requested_symbols = set(_choices(symbols))
    unknown_symbols = sorted(requested_symbols.difference(symbol_catalog))
    if unknown_symbols:
        raise ValueError(f"Unsupported symbols: {', '.join(unknown_symbols)}")
    selected_symbols = tuple(symbol for symbol in symbol_catalog if symbol in requested_symbols)
    if not selected_symbols:
        raise ValueError("Select at least one symbol")

    frame_catalog: list[str] = []
    for token in strategy_catalog:
        for frame in profile.strategy_frames[token]:
            normalized = str(frame).upper()
            if normalized not in frame_catalog:
                frame_catalog.append(normalized)
    requested_frames = set(_choices(timeframes, upper=True))
    unknown_frames = sorted(requested_frames.difference(frame_catalog))
    if unknown_frames:
        raise ValueError(f"Unsupported signal timeframes: {', '.join(unknown_frames)}")
    selected_frames = tuple(frame for frame in frame_catalog if frame in requested_frames)
    if not selected_frames:
        raise ValueError("Select at least one timeframe")

    frames_by_strategy: dict[str, tuple[str, ...]] = {}
    for token in selected_strategies:
        allowed = {str(frame).upper() for frame in profile.strategy_frames[token]}
        frames = tuple(frame for frame in selected_frames if frame in allowed)
        if not frames:
            raise ValueError(
                f"Strategy {token!r} has no timeframe after applying the selected global filter"
            )
        frames_by_strategy[token] = frames

    channels = tuple(
        {"strategy": token, "symbol": symbol, "timeframe": frame.lower()}
        for token in selected_strategies
        for symbol in selected_symbols
        for frame in frames_by_strategy[token]
    )
    return {
        "strategies": selected_strategies,
        "symbols": selected_symbols,
        "timeframes": selected_frames,
        "strategy_frames": frames_by_strategy,
        "channels": channels,
        "channel_count": len(channels),
    }


def available_timeframes(profile: Any, strategies: Iterable[Any] | Any) -> list[str]:
    """All frames supported by the selected strategies, in profile order."""
    requested = set(_choices(strategies))
    result: list[str] = []
    for token, frames in profile.strategy_frames.items():
        if token not in requested:
            continue
        for frame in frames:
            value = str(frame).lower()
            if value not in result:
                result.append(value)
    return result


def canonical_channels(
    profile: Any,
    rows: Iterable[Mapping[str, Any]] | None,
    *,
    allowed_symbols: Iterable[str],
    allowed_sources: Iterable[str] | None = None,
    default_source: str | None = None,
) -> tuple[dict[str, Any], ...]:
    """Validate explicit dashboard rows without expanding a Cartesian product.

    Row order is preserved because shared-account signal processing order is a
    real frozen input.  An exact strategy/symbol/timeframe duplicate is
    rejected even when its risk differs (and whatever its source).

    A row may carry ``source`` (the Redis profile feeding THAT channel).  Empty
    or equal to ``default_source`` means "the plan's source" and is dropped from
    the canonical row, so existing plan identities do not change.
    """
    allowed = {str(symbol) for symbol in allowed_symbols}
    sources = None if allowed_sources is None else {str(item) for item in allowed_sources}
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(rows or []):
        if not isinstance(raw, Mapping):
            raise ValueError(f"Channel row {index + 1} must be an object")
        strategy = str(raw.get("strategy") or "").strip().lower()
        symbol = str(raw.get("symbol") or "").strip()
        timeframe = str(raw.get("timeframe") or "").strip().upper()
        if strategy not in profile.strategy_frames:
            raise ValueError(f"Channel row {index + 1}: unsupported strategy {strategy!r}")
        if symbol not in allowed:
            raise ValueError(f"Channel row {index + 1}: unsupported symbol {symbol!r}")
        if timeframe not in profile.strategy_frames[strategy]:
            raise ValueError(
                f"Channel row {index + 1}: {timeframe!r} is not supported by {strategy}"
            )
        try:
            risk = float(raw.get("risk_percent"))
        except (TypeError, ValueError):
            raise ValueError(
                f"Channel row {index + 1}: risk_percent must be finite and greater than 0"
            ) from None
        if not math.isfinite(risk) or risk <= 0:
            raise ValueError(
                f"Channel row {index + 1}: risk_percent must be finite and greater than 0"
            )
        key = (strategy, symbol, timeframe)
        if key in seen:
            raise ValueError(
                f"Duplicate channel row: {strategy} / {symbol} / {timeframe}"
            )
        seen.add(key)
        source = str(raw.get("source") or "").strip()
        if source and sources is not None and source not in sources:
            raise ValueError(f"Channel row {index + 1}: unsupported signal source {source!r}")
        row = {
            "strategy": strategy,
            "symbol": symbol,
            "timeframe": timeframe.lower(),
            "risk_percent": risk,
        }
        if source and source != default_source:
            row["source"] = source
        result.append(row)
    if not result:
        raise ValueError("Add at least one channel row")
    return tuple(result)
