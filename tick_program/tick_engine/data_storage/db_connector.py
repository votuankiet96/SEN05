"""SQL Server connection layer with retry."""

from __future__ import annotations

import logging
import time

import pyodbc

from tick_engine.env_safety import redact_operator_secrets
from tick_engine.env_utils import env_int
from tick_engine.settings import build_conn_str

logger = logging.getLogger(__name__)

_DB_RETRY_COUNT = env_int("DB_RETRY_COUNT", 3)
_DB_RETRY_DELAY = env_int("DB_RETRY_DELAY_SEC", 5)


def get_connection() -> pyodbc.Connection:
    """Return an active pyodbc connection with bounded retry."""
    conn_str = build_conn_str()
    last_err: Exception = RuntimeError("unreachable")
    for attempt in range(1, _DB_RETRY_COUNT + 1):
        try:
            return pyodbc.connect(conn_str, timeout=30)
        except pyodbc.Error as exc:
            last_err = exc
            logger.warning(
                "DB connect attempt %d/%d failed: %s",
                attempt,
                _DB_RETRY_COUNT,
                redact_operator_secrets(exc),
            )
            if attempt < _DB_RETRY_COUNT:
                time.sleep(_DB_RETRY_DELAY)
    logger.error(
        "Cannot connect to SQL Server after %d attempts: %s",
        _DB_RETRY_COUNT,
        redact_operator_secrets(last_err),
    )
    raise last_err
