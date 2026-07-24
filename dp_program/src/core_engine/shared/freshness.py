"""Shared OHLCV freshness thresholds.

The same rule is used when historical gap repair classifies adjacent bars and
when the live engine decides whether a watermark is stale.  Keeping the
arithmetic here prevents those two decisions from drifting apart.
"""

from __future__ import annotations


def stale_after_minutes(timeframe_minutes: int, overnight_minutes: int) -> int:
    """Return the maximum normal interval before OHLCV data is stale."""

    tf_minutes = max(1, int(timeframe_minutes))
    overnight = max(0, int(overnight_minutes))
    return max(tf_minutes * 3, overnight + tf_minutes)
