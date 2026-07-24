"""Structured operation-log helpers shared by writer/reader/maintenance.

Every warehouse write, read, and maintenance function logs through
`_warehouse_log(...)` so operators see a consistent
`WAREHOUSE | source | target | action | ... | result=...` line regardless
of which of the three modules produced it.
"""

from __future__ import annotations

from core_engine.settings import TF_STAGING
from core_engine.util.logkit import get_logger, log_event

logger = get_logger(
    "data_warehouse",
    console=False,
)


def _tf_from_staging_table(staging_table: str | None) -> str | None:
    if not staging_table:
        return None
    table = str(staging_table).lower()
    for tf_code, configured in TF_STAGING.items():
        if table == str(configured).lower():
            return tf_code
    return None


def _target_label(
    *,
    symbol: str | None = None,
    symbol_id: int | None = None,
    tf_code: str | None = None,
    staging_table: str | None = None,
) -> str:
    tf = tf_code or _tf_from_staging_table(staging_table) or "-"
    if symbol:
        return f"{symbol} {tf}"
    if symbol_id is not None:
        return f"SymbolID={symbol_id} {tf}"
    return f"scope {tf}"


def _warehouse_log(
    level: int,
    *,
    source: str,
    target: str,
    action: str,
    result: str,
    **fields,
) -> None:
    payload = {
        str(key): value
        for key, value in fields.items()
        if value is not None and value != ""
    }
    log_event(
        logger,
        level,
        f"warehouse.{action}",
        f"{action.replace('_', ' ').title()} for {target}",
        area="DATABASE",
        stage="FAILED" if str(result).lower() == "failed" else "COMPLETE",
        result=result,
        source=source,
        target=target,
        **payload,
    )
