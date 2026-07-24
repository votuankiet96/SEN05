"""Backward-compatible entry point for single-timeframe chart payloads.

New code should import from ``core_python.chart.payloads.registry``. This file
keeps the old ``build_chart_payload`` import path alive while dashboard layout
logic moves into strategy-specific modules.
"""

from __future__ import annotations

from core_python.chart.payloads.registry import build_chart_payload

__all__ = ["build_chart_payload"]
