"""Dashboard-owned names for the two frozen Redis signal sources.

The original profile is intentionally implicit in legacy core commands so
existing DB2 experiments remain compatible with their locked config.
"""
from __future__ import annotations

from typing import Any, Mapping

ORIGINAL = "sen05-signal-v1"
TREND_FILTERED = "sen05-signal-trend-v1"

OPTIONS = (
    {"value": ORIGINAL, "label": "Original (DB2)"},
    {"value": TREND_FILTERED, "label": "Trend-filtered (DB3)"},
)
LABELS = {item["value"]: item["label"] for item in OPTIONS}


def require_profile(value: Any) -> str:
    profile = str(value or "")
    if profile not in LABELS:
        raise ValueError(f"Unsupported signal source: {profile!r}")
    return profile


def profile_from_input(input_doc: Mapping[str, Any] | None) -> str:
    """Older dashboard plans without this field always used original DB2."""
    return require_profile((input_doc or {}).get("redis_profile") or ORIGINAL)


def label_from_input(input_doc: Mapping[str, Any] | None) -> str:
    return LABELS[profile_from_input(input_doc)]


def command_field(profile: str) -> dict[str, str]:
    """Pass DB3 explicitly; retain the original DB2 command shape/cache."""
    selected = require_profile(profile)
    return {"redis_profile": selected} if selected == TREND_FILTERED else {}


def identity_field(profile: str) -> dict[str, str]:
    """Keep old DB2 execution keys while always separating DB3 provenance."""
    return command_field(profile)
