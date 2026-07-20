"""Standardized logging infrastructure (loggers, handlers, formatters) for DP Program.

`get_logger(component, log_file, ...)` is the single entry point components
should use; see `core_engine.logkit.factory` for the full level policy.
"""

from core_engine.logkit.factory import get_logger

__all__ = ["get_logger"]
