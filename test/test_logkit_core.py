"""Schema, formatting, routing, and public-API tests for centralized logkit."""

from __future__ import annotations

import logging
import time
from unittest.mock import patch

from core_engine.util.logkit import (
    bind_context,
    cell,
    flush_logs,
    get_logger,
    kv,
    log_event,
    operation_line,
    set_context,
)
from core_engine.util.logkit.core import _component_level
from core_engine.util.logkit.formatter import OperatorFormatter, parse_operator_line


def _record(message, *, level: int = logging.INFO) -> logging.LogRecord:
    record = logging.LogRecord(
        "dp.live.test",
        level,
        __file__,
        1,
        message,
        (),
        None,
    )
    record.dp_stream = "live"
    record.dp_component = "test"
    record.dp_role = "live"
    record.dp_context = {"batch_id": 44, "correlation_id": "L-44"}
    record.dp_fields = {}
    return record


def test_operator_formatter_is_human_readable_and_machine_parseable():
    line = OperatorFormatter().format(
        _record(
            operation_line(
                "DATABASE",
                "Main data store updated",
                rows=58,
                result="ok",
                event_code="warehouse.fact.committed",
            )
        )
    )
    parsed = parse_operator_line(line)
    assert parsed is not None
    assert parsed["level"] == "INFO"
    assert parsed["area"] == "DATABASE"
    assert parsed["stage"] == "PROGRESS"
    assert parsed["message"] == "Main data store updated"
    assert parsed["result"] == "OK"
    assert parsed["reference"] == "L-44"
    assert parsed["event"] == "warehouse.fact.committed"
    assert parsed["rows"] == 58
    assert "\n" not in line


def test_multiline_and_pipe_input_stays_one_physical_line():
    line = OperatorFormatter().format(
        _record(operation_line("LIVE", "Batch report", "first\nsecond | third"))
    )
    assert "\n" not in line
    assert "first second / third" in line
    assert parse_operator_line(line) is not None


def test_percent_s_event_text_keeps_structured_fields():
    message = operation_line(
        "LIVE",
        "Live batch completed",
        batch_id=81,
        accepted_bars=45,
        fact_inserted=45,
        event_code="live.batch.completed",
    )
    record = _record("%s")
    record.args = (message,)

    parsed = parse_operator_line(OperatorFormatter().format(record))

    assert parsed is not None
    assert parsed["event"] == "live.batch.completed"
    assert parsed["accepted_bars"] == 45
    assert parsed["fact_inserted"] == 45


def test_formatter_redacts_credentials_but_keeps_safe_auth_state():
    record = _record(
        operation_line(
            "AUTH",
            "Credential check",
            "Bearer abc.def.secret",
            token="eyJ-real-secret",
            cookie="sessionid=secret",
            password="do-not-store",
            auth_state="authenticated",
        )
    )

    line = OperatorFormatter().format(record)
    parsed = parse_operator_line(line)

    assert parsed is not None
    assert "abc.def.secret" not in line
    assert "eyJ-real-secret" not in line
    assert "sessionid=secret" not in line
    assert "do-not-store" not in line
    assert parsed["token"] == "<redacted>"
    assert parsed["cookie"] == "<redacted>"
    assert parsed["password"] == "<redacted>"
    assert parsed["auth_state"] == "authenticated"


def test_context_is_bound_and_restored():
    with bind_context(batch_id=9) as context:
        assert context["correlation_id"] == 9
        with bind_context(symbol="US500") as nested:
            assert nested["batch_id"] == 9
            assert nested["symbol"] == "US500"
    with bind_context(run_id="H-1") as next_context:
        assert "batch_id" not in next_context
        assert next_context["correlation_id"] == "H-1"


def test_process_context_can_advance_to_the_next_batch():
    first = set_context(batch_id=100, correlation_id=100)
    second = set_context(batch_id=101, correlation_id=101)
    assert first["correlation_id"] == 100
    assert second["batch_id"] == 101
    assert second["correlation_id"] == 101


def test_get_logger_is_idempotent_and_does_not_duplicate_handlers():
    first = get_logger("test_idempotent", stream="system", console=False)
    second = get_logger("test_idempotent", stream="system", console=False)
    assert first is second
    assert len(first.handlers) == 1


def test_later_caller_can_enable_prefix_normalization_on_shared_logger():
    from core_engine.settings import SYSTEM_LOG

    name = f"late-prefix-{time.time_ns()}"
    first = get_logger(name, stream="system", console=False)
    second = get_logger(
        name,
        stream="system",
        console=False,
        normalize_prefixes=True,
    )
    marker = f"prefix-{time.time_ns()}"
    second.info("[AUTH] %s", marker)
    assert first is second
    assert flush_logs(3)
    match = next(
        parse_operator_line(line)
        for line in reversed(SYSTEM_LOG.read_text(encoding="utf-8").splitlines())
        if marker in line
    )
    assert match is not None
    assert match["area"] == "AUTH"


def test_warning_reaches_source_and_alerts_logs():
    from core_engine.settings import ALERTS_LOG, SYSTEM_LOG

    logger = get_logger("test_warning_route", stream="system", console=False)
    marker = f"warning-route-{time.time_ns()}"
    logger.warning(operation_line("SYSTEM", marker, result="monitoring"))
    assert flush_logs(3)
    assert marker in SYSTEM_LOG.read_text(encoding="utf-8")
    assert marker in ALERTS_LOG.read_text(encoding="utf-8")


def test_info_does_not_reach_alerts_log():
    from core_engine.settings import ALERTS_LOG, LIVE_LOG

    logger = get_logger("test_info_route", stream="live", console=False)
    marker = f"info-route-{time.time_ns()}"
    logger.info(operation_line("LIVE", marker, result="ok"))
    assert flush_logs(3)
    assert marker in LIVE_LOG.read_text(encoding="utf-8")
    alerts = ALERTS_LOG.read_text(encoding="utf-8") if ALERTS_LOG.exists() else ""
    assert marker not in alerts


def test_log_event_adds_stable_event_code_and_fields():
    from core_engine.settings import SYSTEM_LOG

    logger = get_logger("test_event_api", stream="system", console=False)
    marker = f"event-api-{time.time_ns()}"
    log_event(
        logger,
        logging.INFO,
        "system.test.completed",
        marker,
        area="SYSTEM",
        stage="COMPLETE",
        result="OK",
        rows=12,
    )
    assert flush_logs(3)
    matches = [
        parse_operator_line(line)
        for line in SYSTEM_LOG.read_text(encoding="utf-8").splitlines()
        if marker in line
    ]
    assert matches and matches[-1]
    assert matches[-1]["event"] == "system.test.completed"
    assert matches[-1]["rows"] == 12


def test_warehouse_event_is_structured_and_traceable():
    from core_engine.settings import SYSTEM_LOG
    from core_engine.shared.warehouse.operation_log import _warehouse_log

    marker = f"Symbol-{time.time_ns()}"
    _warehouse_log(
        logging.INFO,
        source="live_fetching",
        target=marker,
        action="fact_save",
        result="ok",
        batch_id=77,
        fact_inserted=9,
    )
    assert flush_logs(3)
    match = next(
        parse_operator_line(line)
        for line in reversed(SYSTEM_LOG.read_text(encoding="utf-8").splitlines())
        if marker in line
    )
    assert match is not None
    assert match["event"] == "warehouse.fact_save"
    assert match["area"] == "DATABASE"
    assert match["batch_id"] == 77
    assert match["fact_inserted"] == 9


def test_component_level_uses_global_and_component_override():
    with patch("core_engine.util.logkit.core.LOGGING") as settings:
        settings.level = "INFO"
        with patch("core_engine.util.logkit.core.env_str", return_value=""):
            assert _component_level("live_fetching") == logging.INFO
        with patch(
            "core_engine.util.logkit.core.env_str",
            side_effect=lambda name: "DEBUG" if name == "LOG_LEVEL_LIVE_FETCHING" else "",
        ):
            assert _component_level("live_fetching") == logging.DEBUG


def test_table_helpers_remain_consistent_for_operator_views():
    assert cell("EURUSD", 10) == "EURUSD    "
    assert cell("42", 6, align="right") == "    42"
    assert cell("a_very_long_symbol_name", 10).endswith("...")
    assert kv("Mode", "gap") == "  Mode             : gap"
