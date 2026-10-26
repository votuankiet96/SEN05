"""Dashboard-owned names for the three fixed Redis signal sources (WE/OG8, 2026-10-23).

RAW (db0) is intentionally implicit in legacy core commands so existing db0 experiments remain compatible with
their locked config. KNN (db1) and Breakout (db2) are always passed explicitly. A channel of a multi-channel job
may override the job's source (`source` on the channel row).
"""
from __future__ import annotations

from typing import Any, Mapping

ORIGINAL = "sen05-signal-v1"
KNN = "sen05-signal-knn-v1"
BREAKOUT = "sen05-signal-breakout-v1"
# db1 profile before 2026-10-23 (prefix L_PastSignal_Trend, content switched KNN <-> breakout): removed from
# config.yaml. Only lets old dashboard plans/sessions still show a label; it can no longer be selected.
LEGACY_TREND = "sen05-signal-trend-v1"

OPTIONS = (
    {"value": ORIGINAL, "label": "RAW (db0)"},
    {"value": KNN, "label": "KNN (db1)"},
    {"value": BREAKOUT, "label": "Breakout (db2)"},
)
LABELS = {item["value"]: item["label"] for item in OPTIONS}
LEGACY_LABELS = {LEGACY_TREND: "Trend (legacy db1)"}


def require_profile(value: Any) -> str:
    profile = str(value or "")
    if profile not in LABELS:
        raise ValueError(f"Unsupported signal source: {profile!r}")
    return profile


def profile_from_input(input_doc: Mapping[str, Any] | None) -> str:
    """Older dashboard plans without this field always used original db0; plans of the retired trend profile keep it."""
    profile = str((input_doc or {}).get("redis_profile") or ORIGINAL)
    return profile if profile in LEGACY_LABELS else require_profile(profile)


def label_from_input(input_doc: Mapping[str, Any] | None) -> str:
    profile = profile_from_input(input_doc)
    return LABELS.get(profile) or LEGACY_LABELS[profile]


def command_field(profile: str) -> dict[str, str]:
    """Pass KNN/Breakout explicitly; retain the original db0 command shape/cache."""
    selected = require_profile(profile)
    return {"redis_profile": selected} if selected != ORIGINAL else {}


def identity_field(profile: str) -> dict[str, str]:
    """Keep old db0 execution keys while always separating KNN/Breakout provenance."""
    return command_field(profile)
