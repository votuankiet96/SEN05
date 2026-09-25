"""Audit provider-observed historical gaps and safely repair reachable recent gaps.

Run from ``core_program`` so the canonical private configuration is selected::

    python -B research/historical_gap_backfill.py scan \
        --from-utc 2017-01-01 --to-utc 2024-12-31 \
        --symbol BTCUSD --timeframe M30

``scan`` is SQL-only. ``probe`` also reads TradingView but never writes. ``repair``
requires an explicit confirmation and still refuses any unproven, changed, old,
or transport-unreachable window. The production engine remains unmodified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
DEFAULT_CONFIG = PROJECT_ROOT / "config.yaml"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from dp_program.configuration import load_config  # noqa: E402
from dp_program.engine.pipeline import (  # noqa: E402
    fetch_and_store,
    validate_candles,
)
from dp_program.engine.sql_connector import (  # noqa: E402
    candle_signature,
    fetch_existing_candles,
    get_pair_states,
    pair_key,
    select_pairs,
)
from dp_program.engine.websocket import (  # noqa: E402
    FetchRequest,
    fetch_candles_batch,
    request_key,
)


UTC = timezone.utc
WRITE_CONFIRMATION = "APPLY_PROVIDER_OBSERVED_CANDLES"
LOGGER = logging.getLogger("historical_gap_backfill")


class HistoricalRepairError(RuntimeError):
    """A historical audit or repair safety condition failed."""


@dataclass(frozen=True)
class GapCandidate:
    """Khoảng thời gian SQL cần đối chiếu lại với dữ liệu nguồn."""

    kind: str
    start: datetime
    end: datetime


@dataclass(frozen=True)
class RepairWindow:
    """Cửa sổ nhỏ bao quanh một nhóm nến nguồn đang thiếu trong SQL."""

    start: datetime
    end: datetime
    missing_timestamps: tuple[datetime, ...]


def utc(value: datetime) -> datetime:
    """Normalize a datetime to aware UTC."""
    return (
        value.replace(tzinfo=UTC)
        if value.tzinfo is None
        else value.astimezone(UTC)
    )


def parse_boundary(value: str, *, inclusive_date_end: bool = False) -> datetime:
    """Parse ISO input; a date-only end value includes that complete UTC day."""
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid ISO date/time: {value}") from exc
    parsed = utc(parsed)
    if inclusive_date_end and len(text) == 10:
        parsed += timedelta(days=1)
    return parsed.replace(microsecond=0)


def detect_gap_candidates(
    timestamps: Iterable[datetime],
    start: datetime,
    end_exclusive: datetime,
    minutes: int,
) -> list[GapCandidate]:
    """Find SQL continuity candidates, including empty/head/tail coverage."""
    lower, upper = utc(start), utc(end_exclusive)
    if lower >= upper:
        raise ValueError("from-utc must be earlier than to-utc")
    interval = timedelta(minutes=int(minutes))
    threshold = interval * 2
    ordered = sorted({utc(item) for item in timestamps if lower <= utc(item) < upper})
    if not ordered:
        return [GapCandidate("empty", lower, upper)]
    gaps: list[GapCandidate] = []
    if ordered[0] - lower >= threshold:
        gaps.append(GapCandidate("leading", lower, ordered[0]))
    for previous, current in zip(ordered, ordered[1:]):
        if current - previous >= threshold:
            gaps.append(GapCandidate("internal", previous, current))
    if upper - ordered[-1] >= threshold:
        gaps.append(GapCandidate("trailing", ordered[-1], upper))
    return gaps


def required_latest_bars(
    oldest_required: datetime,
    now: datetime,
    minutes: int,
    overlap_bars: int,
) -> int:
    """Return a conservative latest-N request needed to reach a UTC cursor."""
    delta = max(0.0, (utc(now) - utc(oldest_required)).total_seconds())
    return max(1, math.ceil(delta / (int(minutes) * 60)) + int(overlap_bars))


def _warehouse_times(existing: dict[datetime, tuple[str | None, ...]]) -> list[datetime]:
    return [utc(timestamp) for timestamp in existing]


def _provider_map(
    candles: list[dict[str, Any]],
    start: datetime,
    end_exclusive: datetime,
) -> dict[datetime, dict[str, Any]]:
    lower, upper = utc(start), utc(end_exclusive)
    return {
        utc(candle["timestamp"]): candle
        for candle in candles
        if lower <= utc(candle["timestamp"]) < upper
    }


def compare_provider_to_fact(
    provider: dict[datetime, dict[str, Any]],
    existing: dict[datetime, tuple[str | None, ...]],
) -> tuple[list[datetime], list[datetime]]:
    """Return provider-observed missing timestamps and value mismatches."""
    fact = {utc(timestamp): signature for timestamp, signature in existing.items()}
    missing: list[datetime] = []
    changed: list[datetime] = []
    for timestamp, candle in sorted(provider.items()):
        if timestamp not in fact:
            missing.append(timestamp)
        elif fact[timestamp] != candle_signature(candle):
            changed.append(timestamp)
    return missing, changed


def boundary_anchors_missing(
    candidates: list[GapCandidate], provider_times: set[datetime]
) -> list[dict[str, str]]:
    """Require existing SQL boundary candles to also exist at the provider."""
    missing: list[dict[str, str]] = []
    for candidate in candidates:
        required: list[tuple[str, datetime]] = []
        if candidate.kind in {"internal", "trailing"}:
            required.append(("left", candidate.start))
        if candidate.kind in {"internal", "leading"}:
            required.append(("right", candidate.end))
        for side, value in required:
            stamp = utc(value)
            if stamp not in provider_times:
                missing.append(
                    {
                        "kind": candidate.kind,
                        "side": side,
                        "timestamp": stamp.isoformat(),
                    }
                )
    return missing


def build_repair_windows(
    provider_times: Sequence[datetime], missing_times: Sequence[datetime]
) -> list[RepairWindow]:
    """Cluster adjacent missing provider bars and include one anchor per edge."""
    ordered = sorted({utc(item) for item in provider_times})
    missing = sorted({utc(item) for item in missing_times})
    if not missing:
        return []
    positions = {timestamp: index for index, timestamp in enumerate(ordered)}
    if any(timestamp not in positions for timestamp in missing):
        raise HistoricalRepairError("missing timestamp is absent from provider sequence")
    groups: list[list[datetime]] = []
    for timestamp in missing:
        if not groups or positions[timestamp] > positions[groups[-1][-1]] + 1:
            groups.append([timestamp])
        else:
            groups[-1].append(timestamp)
    windows: list[RepairWindow] = []
    for group in groups:
        first, last = positions[group[0]], positions[group[-1]]
        left, right = max(0, first - 1), min(len(ordered) - 1, last + 1)
        windows.append(RepairWindow(ordered[left], ordered[right], tuple(group)))
    return windows


def plan_id(
    pair_name: str,
    start: datetime,
    end_exclusive: datetime,
    missing: Sequence[datetime],
    changed: Sequence[datetime],
) -> str:
    """Create a stable, secret-free identifier for an observed repair plan."""
    payload = {
        "pair": pair_name,
        "from": utc(start).isoformat(),
        "to_exclusive": utc(end_exclusive).isoformat(),
        "missing": [utc(item).isoformat() for item in missing],
        "changed": [utc(item).isoformat() for item in changed],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


def _sample_candidates(candidates: Sequence[GapCandidate], limit: int) -> list[dict[str, Any]]:
    newest = sorted(candidates, key=lambda item: item.start, reverse=True)[:limit]
    return [
        {
            **asdict(item),
            "start": utc(item.start).isoformat(),
            "end": utc(item.end).isoformat(),
            "gap_seconds": int((utc(item.end) - utc(item.start)).total_seconds()),
        }
        for item in newest
    ]


def scan_pair(
    config: dict[str, Any],
    pair: tuple[dict[str, Any], dict[str, Any]],
    start: datetime,
    end_exclusive: datetime,
    sample_limit: int,
) -> tuple[dict[str, Any], dict[datetime, tuple[str | None, ...]], list[GapCandidate]]:
    """Read one Fact range and describe SQL continuity candidates."""
    symbol, timeframe = pair
    existing = fetch_existing_candles(
        config,
        int(symbol["symbol_id"]),
        str(timeframe["code"]),
        start,
        end_exclusive - timedelta(seconds=1),
    )
    candidates = detect_gap_candidates(
        _warehouse_times(existing), start, end_exclusive, int(timeframe["minutes"])
    )
    result = {
        "pair": pair_key(pair),
        "symbol": symbol["symbol"],
        "timeframe": timeframe["code"],
        "range_start_utc": utc(start).isoformat(),
        "range_end_exclusive_utc": utc(end_exclusive).isoformat(),
        "fact_rows": len(existing),
        "first_fact_bar_utc": (
            min(_warehouse_times(existing)).isoformat() if existing else None
        ),
        "last_fact_bar_utc": (
            max(_warehouse_times(existing)).isoformat() if existing else None
        ),
        "sql_gap_candidates": len(candidates),
        "candidate_kinds": {
            kind: sum(item.kind == kind for item in candidates)
            for kind in ("empty", "leading", "internal", "trailing")
        },
        "candidate_sample_newest": _sample_candidates(candidates, sample_limit),
        "status": "CANDIDATES_FOUND" if candidates else "SQL_SEQUENCE_CLEAN",
        "writes": 0,
    }
    return result, existing, candidates


def probe_pair(
    config: dict[str, Any],
    pair: tuple[dict[str, Any], dict[str, Any]],
    start: datetime,
    end_exclusive: datetime,
    existing: dict[datetime, tuple[str | None, ...]],
    candidates: list[GapCandidate],
    *,
    now: datetime,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[datetime], list[datetime]]:
    """Fetch a proven provider window and compare it with Fact without writing."""
    symbol, timeframe = pair
    if not candidates:
        return {"status": "SQL_SEQUENCE_CLEAN", "writes": 0}, [], [], []
    oldest_required = min(item.start for item in candidates)
    bars = required_latest_bars(
        oldest_required,
        now,
        int(timeframe["minutes"]),
        int(config["backfill"]["overlap_bars"]),
    )
    cap = int(config["backfill"]["max_bars_per_request"])
    if bars > cap:
        return {
            "status": "UNREACHABLE_WITH_CURRENT_TRANSPORT",
            "required_latest_bars": bars,
            "transport_cap_bars": cap,
            "oldest_required_utc": utc(oldest_required).isoformat(),
            "writes": 0,
        }, [], [], []
    request = FetchRequest(symbol, timeframe, bars, cap, oldest_required)
    fetched = fetch_candles_batch(config, [request])[request_key(request)]
    valid = validate_candles(
        fetched.candles,
        timeframe,
        closed_only=bool(config["live"]["closed_candles_only"]),
        now=now,
    )
    if not valid or utc(valid[0]["timestamp"]) > utc(oldest_required):
        return {
            "status": "INCOMPLETE_PROVIDER_COVERAGE",
            "oldest_required_utc": utc(oldest_required).isoformat(),
            "provider_earliest_utc": (
                utc(valid[0]["timestamp"]).isoformat() if valid else None
            ),
            "writes": 0,
        }, valid, [], []
    provider = _provider_map(valid, start, end_exclusive)
    anchors = boundary_anchors_missing(candidates, set(provider))
    if anchors:
        return {
            "status": "BOUNDARY_ANCHOR_MISMATCH",
            "boundary_anchors_missing": anchors[:20],
            "writes": 0,
        }, valid, [], []
    missing, changed = compare_provider_to_fact(provider, existing)
    status = (
        "CHANGED_EXISTING_ROWS_BLOCKED"
        if changed
        else "CONFIRMED_MISSING"
        if missing
        else "PROVIDER_OBSERVED_COMPLETE"
    )
    return {
        "status": status,
        "required_latest_bars": bars,
        "requested_bars": fetched.requested_bars,
        "extension_rounds": fetched.extension_rounds,
        "provider_rows_in_range": len(provider),
        "confirmed_missing_rows": len(missing),
        "changed_existing_rows": len(changed),
        "confirmed_missing_sample": [item.isoformat() for item in missing[:20]],
        "changed_sample": [item.isoformat() for item in changed[:20]],
        "plan_id": plan_id(pair_key(pair), start, end_exclusive, missing, changed),
        "writes": 0,
    }, valid, missing, changed


def apply_repair(
    config: dict[str, Any],
    pair: tuple[dict[str, Any], dict[str, Any]],
    provider_candles: list[dict[str, Any]],
    missing: list[datetime],
    changed: list[datetime],
    *,
    now: datetime,
) -> dict[str, Any]:
    """Apply only a recent, provider-proven, missing-only plan via the pipeline."""
    if changed:
        return {"status": "CHANGED_EXISTING_ROWS_BLOCKED", "writes": 0}
    if not missing:
        return {"status": "PROVIDER_OBSERVED_COMPLETE", "writes": 0}
    horizon = utc(now) - timedelta(days=int(config["backfill"]["lookback_days"]))
    windows = build_repair_windows(
        [utc(item["timestamp"]) for item in provider_candles], missing
    )
    if any(window.start < horizon for window in windows):
        return {
            "status": "HISTORICAL_WRITE_SCOPE_BLOCKED",
            "reason": (
                "canonical loader has FromTime but no bounded ToTime; old repair "
                "could reprocess unrelated persistent staging rows"
            ),
            "safe_horizon_utc": horizon.isoformat(),
            "writes": 0,
        }
    symbol, timeframe = pair
    affected = inserted = updated = 0
    for window in windows:
        result = fetch_and_store(
            config,
            symbol,
            timeframe,
            workflow="historical_research",
            bars=len(provider_candles),
            window_start=window.start,
            window_end=window.end,
            require_coverage=True,
            required_cursor=window.end,
            provider_candles=provider_candles,
            now=now,
        )
        affected += int(result["affected"])
        inserted += int(result["fact_inserted"])
        updated += int(result["fact_updated"])
    verify_start, verify_end = min(missing), max(missing)
    verified = fetch_existing_candles(
        config,
        int(symbol["symbol_id"]),
        str(timeframe["code"]),
        verify_start,
        verify_end,
    )
    verified_map = {utc(timestamp): signature for timestamp, signature in verified.items()}
    provider_map = {
        utc(candle["timestamp"]): candle_signature(candle)
        for candle in provider_candles
        if utc(candle["timestamp"]) in set(missing)
    }
    invalid = sorted(
        timestamp
        for timestamp in missing
        if verified_map.get(timestamp) != provider_map.get(timestamp)
    )
    if invalid:
        raise HistoricalRepairError(
            f"post-write verification failed for {len(invalid)} provider candles"
        )
    return {
        "status": "REPAIRED",
        "repair_windows": len(windows),
        "fact_inserted": inserted,
        "fact_updated": updated,
        "affected": affected,
        "verified_missing_after": 0,
        "writes": len(windows),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit and repair provider-observed historical OHLCV gaps."
    )
    parser.add_argument("action", choices=("scan", "probe", "repair"))
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help="config.yaml path; defaults to the canonical core_program file.",
    )
    parser.add_argument("--from-utc", required=True, help="Inclusive ISO UTC boundary.")
    parser.add_argument(
        "--to-utc",
        required=True,
        help="Inclusive when YYYY-MM-DD; otherwise an exclusive ISO UTC boundary.",
    )
    parser.add_argument("--symbol", help="Config-selected symbol, e.g. BTCUSD.")
    parser.add_argument("--timeframe", help="Config-selected timeframe, e.g. M30.")
    parser.add_argument(
        "--all-config-pairs",
        action="store_true",
        help="Allow network action across every pair selected by config.yaml.",
    )
    parser.add_argument(
        "--confirm-write",
        default="",
        help=f"Repair requires the exact value {WRITE_CONFIRMATION}.",
    )
    parser.add_argument("--sample-limit", type=int, default=20)
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    return parser


def run(argv: Sequence[str] | None = None) -> dict[str, Any]:
    """Execute the standalone research workflow and return a JSON-safe report."""
    args = _parser().parse_args(argv)
    if args.sample_limit < 1 or args.delay_seconds < 0:
        raise ValueError("sample-limit must be positive and delay-seconds non-negative")
    start = parse_boundary(args.from_utc)
    end_exclusive = parse_boundary(args.to_utc, inclusive_date_end=True)
    if start >= end_exclusive:
        raise ValueError("from-utc must be earlier than to-utc")
    if args.action in {"probe", "repair"}:
        filtered = bool(args.symbol and args.timeframe)
        if not filtered and not args.all_config_pairs:
            raise HistoricalRepairError(
                "network actions require both --symbol and --timeframe, or --all-config-pairs"
            )
    if args.action == "repair" and args.confirm_write != WRITE_CONFIRMATION:
        raise HistoricalRepairError(
            f"repair requires --confirm-write {WRITE_CONFIRMATION}"
        )

    config = load_config(args.config)
    pairs = select_pairs(
        config,
        live=True,
        symbol_filter=args.symbol,
        timeframe_filter=args.timeframe,
    )
    states = get_pair_states(config, pairs)
    captured = datetime.now(UTC).replace(microsecond=0)
    results: list[dict[str, Any]] = []
    total_writes = 0
    provider_failures = 0
    for index, pair in enumerate(pairs):
        base, existing, candidates = scan_pair(
            config, pair, start, end_exclusive, args.sample_limit
        )
        state = states[(int(pair[0]["symbol_id"]), str(pair[1]["code"]))]
        base["pair_earliest_fact_utc"] = (
            utc(state["earliest"]).isoformat() if state["earliest"] else None
        )
        base["pair_latest_fact_utc"] = (
            utc(state["latest"]).isoformat() if state["latest"] else None
        )
        if args.action != "scan":
            try:
                probe, provider, missing, changed = probe_pair(
                    config,
                    pair,
                    start,
                    end_exclusive,
                    existing,
                    candidates,
                    now=captured,
                )
                base.update(probe)
                if args.action == "repair" and probe["status"] == "CONFIRMED_MISSING":
                    applied = apply_repair(
                        config,
                        pair,
                        provider,
                        missing,
                        changed,
                        now=captured,
                    )
                    base.update(applied)
                    total_writes += int(applied.get("writes", 0))
            except Exception as exc:
                provider_failures += 1
                base.update(
                    {
                        "status": "FAILED",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "writes": 0,
                    }
                )
                if provider_failures >= 3:
                    base["circuit_open"] = True
                    results.append(base)
                    break
        results.append(base)
        if args.action != "scan" and index + 1 < len(pairs) and args.delay_seconds:
            time.sleep(args.delay_seconds)

    repair_success = {"SQL_SEQUENCE_CLEAN", "PROVIDER_OBSERVED_COMPLETE", "REPAIRED"}
    workflow_ok = not any(item["status"] == "FAILED" for item in results)
    if args.action == "repair":
        workflow_ok = workflow_ok and all(
            item["status"] in repair_success for item in results
        )
    return {
        "ok": workflow_ok,
        "action": args.action,
        "captured_at_utc": captured.isoformat(),
        "config_path": str(Path(config["app"]["config_path"])),
        "scope": "config.yaml live symbol/timeframe selection",
        "range_start_utc": start.isoformat(),
        "range_end_exclusive_utc": end_exclusive.isoformat(),
        "pairs_selected": len(pairs),
        "pairs_processed": len(results),
        "writes": total_writes,
        "results": results,
    }


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        report = run(argv)
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0 if report["ok"] else 1
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "status": "FAILED",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "writes": 0,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
