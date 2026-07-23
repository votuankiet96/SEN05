"""Backend engine package for the SEN05 data provider.

Only crash bootstrap is allowed at package import time. Ordinary file routing,
formatting, retention, and notifications are owned by ``util.logkit``.
"""

from __future__ import annotations

from core_engine.util.logkit.bootstrap import install_crash_capture

PROCESS_ROLE = install_crash_capture()

__all__ = ["PROCESS_ROLE"]
