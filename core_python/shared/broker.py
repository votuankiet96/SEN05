"""Broker execution helpers shared by strategy engines.

The strategy decides where it wants to trade. This module keeps broker mechanics
such as lot-step rounding and broker-spec audits out of signal logic.
"""

from __future__ import annotations

from math import floor
from typing import Any


DEFAULT_REQUIRED_SPEC_FIELDS = (
    "contract_value",
    "point_size",
    "spread_pts",
    "slippage_pts",
    "commission_per_lot",
    "swap_long_per_lot_per_day",
    "swap_short_per_lot_per_day",
    "min_lot_size",
    "max_lot_size",
    "lot_step",
)


def round_lot_size(
    raw_lot: float,
    *,
    min_lot: float = 0.01,
    max_lot: float = 100.0,
    lot_step: float = 0.01,
    mode: str = "floor",
) -> float:
    """Clamp and round a raw lot size to broker-accepted volume increments."""
    if raw_lot <= 0 or max_lot <= 0:
        return 0.0

    min_lot = max(float(min_lot), 0.0)
    max_lot = max(float(max_lot), min_lot)
    lot_step = float(lot_step or min_lot or 0.01)
    if lot_step <= 0:
        lot_step = min_lot or 0.01

    clamped = min(max(float(raw_lot), min_lot), max_lot)
    units = clamped / lot_step
    if mode == "nearest":
        rounded = round(units) * lot_step
    else:
        rounded = floor(units + 1e-12) * lot_step

    rounded = min(max(rounded, min_lot), max_lot)
    decimals = max(0, min(8, len(str(lot_step).split(".")[-1]) if "." in str(lot_step) else 0))
    return round(rounded, decimals)


def merge_broker_profile(
    symbol_config: dict[str, Any],
    profile: dict[str, Any] | None = None,
    symbol_key: str | None = None,
) -> dict[str, Any]:
    """Apply broker profile defaults and per-symbol overrides to a symbol config."""
    if not profile:
        return dict(symbol_config)

    defaults = profile.get("defaults", {})
    symbol_overrides = profile.get("symbols", {}).get(symbol_key or "", {})
    return {
        **symbol_config,
        **defaults,
        **symbol_overrides,
        "broker_profile": profile.get("name", ""),
        "broker_label": profile.get("label", ""),
        "broker_platform": profile.get("platform", ""),
    }


def audit_symbol_specs(
    symbols: dict[str, dict[str, Any]],
    *,
    required_fields: tuple[str, ...] = DEFAULT_REQUIRED_SPEC_FIELDS,
) -> dict[str, Any]:
    """Return warnings/errors for incomplete broker specifications."""
    warnings: list[str] = []
    errors: list[str] = []

    for sym_key, spec in symbols.items():
        for field in required_fields:
            if field not in spec:
                warnings.append(f"{sym_key}: missing {field}.")

        min_lot = spec.get("min_lot_size")
        max_lot = spec.get("max_lot_size")
        lot_step = spec.get("lot_step")
        if min_lot is not None and max_lot is not None and float(min_lot) > float(max_lot):
            errors.append(f"{sym_key}: min_lot_size > max_lot_size.")
        if lot_step is not None and float(lot_step) <= 0:
            errors.append(f"{sym_key}: lot_step must be > 0.")

        if not spec.get("spec_verified", False):
            warnings.append(f"{sym_key}: broker spec is not marked verified.")

    return {
        "warnings": warnings,
        "errors": errors,
        "ok": not errors,
    }
