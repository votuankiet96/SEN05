"""Audit hằng ngày dạng chỉ đọc cho runtime và warehouse của DP Program."""

from __future__ import annotations

import argparse
import json
import shlex
import statistics
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from dp_program.configuration import load_config
from dp_program.engine.auth import auth_status
from dp_program.engine.runtime import service_status
from dp_program.engine.spool import pending_status
from dp_program.engine.sql_connector import (
    check_connection,
    get_connection,
    get_pair_states,
    select_pairs,
)
from dp_program.util.discord_report import _post_payload


_RISK_COLOR = {"OK": 0x2ECC71, "WATCH": 0xF1C40F, "HIGH": 0xE67E22, "CRITICAL": 0xE74C3C}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return str(value)


def _parse_log_line(line: str) -> dict[str, Any] | None:
    try:
        parts = shlex.split(line)
    except ValueError:
        parts = line.split()
    if len(parts) < 2:
        return None
    try:
        timestamp = datetime.fromisoformat(parts[0].replace("Z", "+00:00"))
    except ValueError:
        return None
    record: dict[str, Any] = {"ts": timestamp, "level": parts[1], "raw": line.rstrip("\n")}
    for token in parts[2:]:
        if "=" in token:
            key, value = token.split("=", 1)
            record[key] = value
    return record


def _read_log_records(log_dir: Path, name: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(log_dir.glob(f"{name}*"), key=lambda item: item.stat().st_mtime):
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            record = _parse_log_line(line)
            if record and start <= record["ts"] <= end:
                records.append(record)
    return sorted(records, key=lambda item: item["ts"])


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _stats(values: list[float | None]) -> dict[str, Any] | None:
    nums = sorted(value for value in values if value is not None)
    if not nums:
        return None
    p95_index = min(len(nums) - 1, round((len(nums) - 1) * 0.95))
    return {
        "count": len(nums),
        "avg": round(sum(nums) / len(nums), 3),
        "p50": round(statistics.median(nums), 3),
        "p95": round(nums[p95_index], 3),
        "max": round(max(nums), 3),
    }


def _bad_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        record for record in records
        if record.get("level") in {"WARNING", "ERROR", "CRITICAL"}
        or record.get("risk") not in {None, "NONE"}
    ]


def _live_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    cycles = [record for record in records if record.get("event") == "LIVE_CYCLE_COMPLETED"]
    fetches = [record for record in records if record.get("event") == "FETCH_BATCH_COMPLETED"]
    retries = [record for record in records if record.get("event") == "FETCH_BATCH_RETRY"]
    return {
        "cycle_count": len(cycles),
        "cycle_duration_seconds": _stats([_to_float(record.get("duration_seconds")) for record in cycles]),
        "fetch_seconds": _stats([_to_float(record.get("fetch_seconds")) for record in cycles]),
        "pipeline_seconds": _stats([_to_float(record.get("pipeline_seconds")) for record in cycles]),
        "fetch_batch_seconds": _stats([_to_float(record.get("duration_seconds")) for record in fetches]),
        "failed_total": sum(int(float(record.get("failed", 0))) for record in cycles),
        "deferred_total": sum(int(float(record.get("deferred", 0))) for record in cycles),
        "retry_count": len(retries),
        "last_cycle": cycles[-1] if cycles else None,
    }


def _backfill_runs(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for record in records:
        event = record.get("event")
        if event == "BACKFILL_SCHEDULED" or (
            event == "SERVICE_STARTED" and record.get("mode") == "backfill"
        ):
            if current:
                runs.append(current)
            slot = record.get("slot") if event == "BACKFILL_SCHEDULED" else "service_start"
            current = {
                "slot": slot,
                "start": record["ts"],
                "last_pair_ts": None,
                "pairs": 0,
                "fetches": 0,
                "affected": 0,
                "warnings": 0,
                "errors": 0,
            }
            continue
        if current is None:
            continue
        if event == "FETCH_BATCH_COMPLETED":
            current["fetches"] += 1
        elif event == "PAIR_COMPLETED":
            current["pairs"] += 1
            current["last_pair_ts"] = record["ts"]
            current["affected"] += int(float(record.get("affected", 0)))
        if record.get("level") == "WARNING":
            current["warnings"] += 1
        elif record.get("level") in {"ERROR", "CRITICAL"}:
            current["errors"] += 1
    if current:
        runs.append(current)
    cleaned: list[dict[str, Any]] = []
    for run in runs:
        end = run["last_pair_ts"] or run["start"]
        cleaned.append({
            "slot": run["slot"],
            "start": run["start"].isoformat(),
            "end": end.isoformat(),
            "duration_seconds": round((end - run["start"]).total_seconds(), 3),
            "pairs": run["pairs"],
            "fetches": run["fetches"],
            "affected": run["affected"],
            "warnings": run["warnings"],
            "errors": run["errors"],
        })
    return cleaned


def _backfill_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    runs = _backfill_runs(records)
    scheduled_runs = [run for run in runs if run["slot"] != "service_start"]
    service_start_runs = [run for run in runs if run["slot"] == "service_start"]
    retries = [record for record in records if record.get("event") == "FETCH_BATCH_RETRY"]
    return {
        "scheduled_run_count": len(scheduled_runs),
        "service_start_run_count": len(service_start_runs),
        "run_duration_seconds": _stats([run["duration_seconds"] for run in runs]),
        "pair_count_min": min((run["pairs"] for run in runs), default=None),
        "pair_count_max": max((run["pairs"] for run in runs), default=None),
        "retry_count": len(retries),
        "last_runs": runs[-6:],
    }


def _discord_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    discord = [record for record in records if record.get("component") == "discord"]
    return {
        "sent": sum(record.get("event") == "DISCORD_REPORT_SENT" for record in discord),
        "failed": sum(record.get("event") == "DISCORD_REPORT_FAILED" for record in discord),
        "by_report_event": Counter(record.get("report_event", "<none>") for record in discord).most_common(),
    }


def _sql_summary(config: dict[str, Any], start: datetime, now: datetime) -> dict[str, Any]:
    check = check_connection(config)
    connection = get_connection(config)
    try:
        cursor = connection.cursor()
        cursor.execute(
            "SELECT COUNT_BIG(*), MAX(BarTime), MAX(CreatedAt) FROM DWH.Fact_OHLCV"
        )
        row = cursor.fetchone()
        cursor.execute(
            "SELECT COUNT_BIG(*) FROM DWH.Fact_OHLCV WHERE CreatedAt >= ?",
            start.replace(tzinfo=None),
        )
        created_last_window = int(cursor.fetchone()[0])
    finally:
        connection.close()

    pairs = select_pairs(config, live=True)
    states = get_pair_states(config, pairs)
    now_naive = now.replace(tzinfo=None)
    live_m5: list[dict[str, Any]] = []
    for symbol, timeframe in pairs:
        if timeframe["code"] != "M5":
            continue
        latest = states[(int(symbol["symbol_id"]), timeframe["code"])]["latest"]
        age = None if latest is None else round((now_naive - latest).total_seconds() / 60, 1)
        live_m5.append({
            "symbol": f"{symbol['exchange']}:{symbol['symbol']}",
            "asset_type": symbol["asset_type"],
            "latest": None if latest is None else latest.isoformat(sep=" "),
            "age_minutes": age,
        })

    return {
        "check": check,
        "rows_total": int(row[0]),
        "max_bar_utc": None if row[1] is None else row[1].isoformat(sep=" "),
        "max_created_at_utc": None if row[2] is None else row[2].isoformat(sep=" "),
        "rows_created_window": created_last_window,
        "live_m5": sorted(live_m5, key=lambda item: item["symbol"]),
    }


def _risk_level(
    live_status: dict[str, Any],
    backfill_status: dict[str, Any],
    auth: dict[str, Any],
    spool: dict[str, Any],
    sql: dict[str, Any],
    live: dict[str, Any],
    backfill: dict[str, Any],
    bad_count: int,
) -> tuple[str, list[str]]:
    issues: list[str] = []
    critical = False
    high = False
    watch = False
    if not live_status.get("ok"):
        critical = True
        issues.append("Live service không OK")
    if not backfill_status.get("ok"):
        critical = True
        issues.append("Backfill service không OK")
    if not auth.get("ok"):
        critical = True
        issues.append("TradingView auth không OK")
    if not (sql.get("check") or {}).get("ok"):
        critical = True
        issues.append("SQL contract/check-sql không OK")
    if int(spool.get("corrupt") or 0):
        critical = True
        issues.append("Spool có file corrupt")
    if int(spool.get("pending") or 0) and float(spool.get("oldest_age_seconds") or 0) > 900:
        high = True
        issues.append("Spool pending quá 15 phút")
    if int(live.get("failed_total") or 0) or int(live.get("deferred_total") or 0):
        high = True
        issues.append("Live có failed/deferred trong 24h")
    if int((backfill_status.get("backfill_generation_deferred") or 0)):
        high = True
        issues.append("Backfill có deferred trong generation hiện tại")
    btc = next((item for item in sql.get("live_m5", []) if item["symbol"].endswith(":BTCUSD")), None)
    if btc and btc.get("age_minutes") is not None and float(btc["age_minutes"]) > 20:
        high = True
        issues.append("BTCUSD M5 stale quá 20 phút")
    if int(backfill.get("retry_count") or 0) or int(live.get("retry_count") or 0) or bad_count:
        watch = True
        issues.append("Có warning/error/retry trong log 24h cần đọc bối cảnh")
    if critical:
        return "CRITICAL", issues
    if high:
        return "HIGH", issues
    if watch:
        return "WATCH", issues
    return "OK", ["Không thấy dấu hiệu rủi ro vận hành rõ trong cửa sổ audit"]


def _markdown_report(data: dict[str, Any]) -> str:
    risk = data["risk"]
    lines = [
        f"# DP Program V3 daily audit — {data['captured_at_utc']}",
        "",
        f"- Cửa sổ kiểm tra: `{data['window_start_utc']}` → `{data['window_end_utc']}`",
        f"- Kết luận: `{risk}`",
        f"- Host: `{data['host']}`",
        f"- Repo: `{data['repo']}`",
        f"- Git HEAD: `{data['git_head']}`",
        "",
        "## Tác động vận hành",
        "",
    ]
    lines.extend(f"- {issue}" for issue in data["issues"])
    live_status = data["status"]["live"]
    backfill_status = data["status"]["backfill"]
    sql = data["sql"]
    live = data["logs"]["live"]
    backfill = data["logs"]["backfill"]
    lines.extend([
        "",
        "## Bằng chứng runtime",
        "",
        f"- Live: PID `{live_status.get('pid')}`, ok `{live_status.get('ok')}`, heartbeat `{live_status.get('heartbeat_at')}`, age `{live_status.get('heartbeat_age_seconds')}` giây",
        f"- Backfill: PID `{backfill_status.get('pid')}`, ok `{backfill_status.get('ok')}`, heartbeat `{backfill_status.get('heartbeat_at')}`, age `{backfill_status.get('heartbeat_age_seconds')}` giây",
        f"- Spool: pending `{data['spool'].get('pending')}`, corrupt `{data['spool'].get('corrupt')}`, oldest_age `{data['spool'].get('oldest_age_seconds')}`",
        "",
        "## SQL / warehouse",
        "",
        f"- Database: `{(sql['check'] or {}).get('database')}`",
        f"- Contract: `{(sql['check'] or {}).get('contract_version')}` / expected `{(sql['check'] or {}).get('expected_contract_version')}`",
        f"- Fact rows: `{sql.get('rows_total')}`",
        f"- Fact watermark: `{sql.get('max_bar_utc')}`",
        f"- Max CreatedAt: `{sql.get('max_created_at_utc')}`",
        f"- Rows CreatedAt trong cửa sổ: `{sql.get('rows_created_window')}`",
        f"- Bootstrap remaining pairs: `{(sql['check'] or {}).get('bootstrap_remaining_pairs')}`",
        "",
        "## Live 24h",
        "",
        f"- Cycles: `{live['cycle_count']}`",
        f"- Failed total: `{live['failed_total']}`",
        f"- Deferred total: `{live['deferred_total']}`",
        f"- Fetch retry: `{live['retry_count']}`",
        f"- Duration stats: `{json.dumps(live['cycle_duration_seconds'], ensure_ascii=False)}`",
        f"- Fetch stats: `{json.dumps(live['fetch_seconds'], ensure_ascii=False)}`",
        f"- Pipeline stats: `{json.dumps(live['pipeline_seconds'], ensure_ascii=False)}`",
        "",
        "## Backfill 24h",
        "",
        f"- Scheduled runs: `{backfill['scheduled_run_count']}`",
        f"- Service-start runs: `{backfill['service_start_run_count']}`",
        f"- Pair count min/max: `{backfill['pair_count_min']}` / `{backfill['pair_count_max']}`",
        f"- Fetch retry: `{backfill['retry_count']}`",
        f"- Run duration stats: `{json.dumps(backfill['run_duration_seconds'], ensure_ascii=False)}`",
        f"- Last backfill generation: `{backfill_status.get('last_backfill_generation')}`",
        "",
        "## Auth / Discord / warnings",
        "",
        f"- Auth: ok `{data['auth'].get('ok')}`, state `{data['auth'].get('state')}`, source `{data['auth'].get('source')}`, seconds_remaining `{data['auth'].get('seconds_remaining')}`",
        f"- Log warning/error/risk records: `{data['bad_count']}`",
        f"- Discord live: `{json.dumps(data['discord']['live'], ensure_ascii=False)}`",
        f"- Discord backfill: `{json.dumps(data['discord']['backfill'], ensure_ascii=False)}`",
        "",
        "## Live M5 freshness",
        "",
    ])
    for item in sql["live_m5"]:
        lines.append(f"- `{item['symbol']}` latest `{item['latest']}`, age `{item['age_minutes']}` phút")
    lines.extend([
        "",
        "## Recent warning/error/risk records",
        "",
    ])
    if data["recent_bad"]:
        for record in data["recent_bad"]:
            lines.append(
                f"- `{record['ts']}` {record['level']} {record.get('component')} "
                f"{record.get('event')} risk={record.get('risk')} reason={record.get('reason')}"
            )
    else:
        lines.append("- Không có warning/error/risk record trong cửa sổ.")
    lines.extend([
        "",
        "## Nguyên tắc xử lý",
        "",
        "- Job này chỉ đọc trạng thái/log/SQL và ghi báo cáo.",
        "- Không tự stop/start, không tự sửa code, không ghi dữ liệu trading.",
        "- Nếu có HIGH/CRITICAL, cần audit bằng chứng trước khi quyết định chỉnh sửa tối giản.",
        "",
    ])
    return "\n".join(lines)


def _send_discord_summary(config: dict[str, Any], report: dict[str, Any], report_path: Path) -> str:
    settings = config.get("discord") or {}
    webhook = str(settings.get("webhook_url") or "")
    if not bool(settings.get("enabled")) or not webhook:
        return "disabled"
    description = (
        f"Cửa sổ: `{report['window_start_utc']}` → `{report['window_end_utc']}`\n"
        f"Kết luận: `{report['risk']}`\n"
        f"Live PID `{report['status']['live'].get('pid')}`; "
        f"Backfill PID `{report['status']['backfill'].get('pid')}`\n"
        f"Watermark: `{report['sql'].get('max_bar_utc')}`\n"
        f"Report: `{report_path}`"
    )
    payload = {
        "username": "DP Program Audit",
        "embeds": [{
            "title": f"DP Program V3 daily audit — {report['risk']}",
            "description": description[:3900],
            "color": _RISK_COLOR.get(report["risk"], _RISK_COLOR["WATCH"]),
            "fields": [
                {"name": "Live", "value": f"{report['logs']['live']['cycle_count']} cycles · {report['logs']['live']['failed_total']} failed · {report['logs']['live']['deferred_total']} deferred", "inline": False},
                {"name": "Backfill", "value": f"{report['logs']['backfill']['scheduled_run_count']} runs · retry {report['logs']['backfill']['retry_count']}", "inline": False},
                {"name": "Warnings", "value": f"{report['bad_count']} warning/error/risk records", "inline": False},
                {"name": "Action", "value": "Read-only audit. Chỉ chỉnh sửa khi có bằng chứng lỗi/rủi ro rõ.", "inline": False},
            ],
            "footer": {"text": f"UTC · {report['captured_at_utc']}"},
        }],
    }
    _post_payload(webhook, payload, post=requests.post)
    return "sent"


def build_report(window_hours: int) -> dict[str, Any]:
    config = load_config()
    now = _utc_now()
    start = now - timedelta(hours=window_hours)
    repo = Path(__file__).resolve().parents[2]
    log_dir = Path(config["app"]["runtime_dir"]) / "logs"
    live_records = _read_log_records(log_dir, "dp_program_live.log", start, now)
    backfill_records = _read_log_records(log_dir, "dp_program_backfill.log", start, now)
    live_log = _live_summary(live_records)
    backfill_log = _backfill_summary(backfill_records)
    all_records = live_records + backfill_records
    bad = _bad_records(all_records)
    live_status = service_status(config, "live")
    backfill_status = service_status(config, "backfill")
    auth = auth_status(config)
    spool = pending_status(config)
    sql = _sql_summary(config, start, now)
    risk, issues = _risk_level(
        live_status, backfill_status, auth, spool, sql, live_log, backfill_log, len(bad)
    )
    recent_bad = []
    for record in sorted(bad, key=lambda item: item["ts"])[-20:]:
        recent_bad.append({
            "ts": record["ts"].isoformat(),
            "level": record.get("level"),
            "component": record.get("component"),
            "event": record.get("event"),
            "risk": record.get("risk"),
            "reason": record.get("reason") or record.get("error_type") or record.get("error") or record.get("action"),
        })
    return {
        "captured_at_utc": now.isoformat(),
        "window_start_utc": start.isoformat(),
        "window_end_utc": now.isoformat(),
        "host": __import__("socket").gethostname(),
        "repo": str(repo),
        "git_head": _git_head(repo),
        "risk": risk,
        "issues": issues,
        "status": {"live": live_status, "backfill": backfill_status},
        "auth": auth,
        "spool": spool,
        "sql": sql,
        "logs": {"live": live_log, "backfill": backfill_log},
        "discord": {
            "live": _discord_summary(live_records),
            "backfill": _discord_summary(backfill_records),
        },
        "bad_count": len(bad),
        "recent_bad": recent_bad,
    }


def _git_head(repo: Path) -> str:
    head = repo / ".git" / "HEAD"
    try:
        value = head.read_text(encoding="utf-8").strip()
        if value.startswith("ref: "):
            ref = repo / ".git" / value[5:]
            return ref.read_text(encoding="utf-8").strip()[:12]
        return value[:12]
    except OSError:
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a read-only 24h DP Program audit")
    parser.add_argument("--window-hours", type=int, default=24)
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--no-discord", action="store_true")
    parser.add_argument("--print-path", action="store_true")
    args = parser.parse_args(argv)

    report = build_report(max(1, int(args.window_hours)))
    config = load_config()
    output_dir = Path(args.output_dir) if args.output_dir else Path(config["app"]["runtime_dir"]) / "audit_reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromisoformat(report["captured_at_utc"]).strftime("%Y%m%dT%H%M%SZ")
    report_path = output_dir / f"dp_program_daily_audit_{stamp}.md"
    report_path.write_text(_markdown_report(report), encoding="utf-8")
    json_path = output_dir / f"dp_program_daily_audit_{stamp}.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
    discord_result = "skipped"
    if not args.no_discord:
        try:
            discord_result = _send_discord_summary(config, report, report_path)
        except Exception as exc:
            discord_result = f"failed:{type(exc).__name__}"
    print(json.dumps({
        "ok": True,
        "risk": report["risk"],
        "report_path": str(report_path),
        "json_path": str(json_path),
        "discord": discord_result,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
