from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

JOB_SCHEMA = "bo-backtest-api-job/v2"
RESULT_SCHEMA = "bo-backtest-api-result/v1"


def _utc_text(value: datetime | str) -> str:
    if isinstance(value, str):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class BacktestApiJob:
    job_id: str
    robot_name: str
    symbol: str
    timeframe: str
    start_utc: datetime | str
    end_utc: datetime | str
    balance: float
    data_mode: str
    precise_conversion: bool
    commission_auto: bool
    parameters: dict[str, Any] = field(default_factory=dict)
    algo_path: str | None = None
    data_file: str | None = None
    commission: float | None = None
    commission_type: str | None = None
    spread: float | None = None
    schema: str = JOB_SCHEMA

    def to_json_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema"] = self.schema
        payload["jobId"] = payload.pop("job_id")
        payload["robotName"] = payload.pop("robot_name")
        payload["algoPath"] = payload.pop("algo_path")
        payload["startUtc"] = _utc_text(payload.pop("start_utc"))
        payload["endUtc"] = _utc_text(payload.pop("end_utc"))
        payload["dataMode"] = payload.pop("data_mode")
        payload["dataFile"] = payload.pop("data_file")
        payload["preciseConversion"] = payload.pop("precise_conversion")
        payload["commissionType"] = payload.pop("commission_type")
        payload["commissionAuto"] = payload.pop("commission_auto")
        return payload


@dataclass(frozen=True)
class BacktestApiResult:
    job_id: str
    status: str
    json_report: str | None
    backtesting_error: str | None = None
    html_report: str | None = None
    error: str | None = None
    wall_seconds: float | None = None
    precise_conversion_requested: bool = False
    precise_conversion_applied: bool = False
    commission_auto_requested: bool = False
    commission_auto_applied: bool = False
    capabilities: dict[str, Any] = field(default_factory=dict)
    schema: str = RESULT_SCHEMA

    @classmethod
    def from_json_dict(cls, payload: dict[str, Any]) -> "BacktestApiResult":
        schema = str(payload.get("schema", ""))
        if schema != RESULT_SCHEMA:
            raise ValueError(f"Unsupported result schema: {schema!r}")
        return cls(
            schema=schema,
            job_id=str(payload.get("jobId", "")),
            status=str(payload.get("status", "")),
            backtesting_error=payload.get("backtestingError"),
            json_report=payload.get("jsonReport"),
            html_report=payload.get("htmlReport"),
            error=payload.get("error"),
            wall_seconds=payload.get("wallSeconds"),
            precise_conversion_requested=bool(payload.get("preciseConversionRequested", False)),
            precise_conversion_applied=bool(payload.get("preciseConversionApplied", False)),
            commission_auto_requested=bool(payload.get("commissionAutoRequested", False)),
            commission_auto_applied=bool(payload.get("commissionAutoApplied", False)),
            capabilities=dict(payload.get("capabilities") or {}),
        )
