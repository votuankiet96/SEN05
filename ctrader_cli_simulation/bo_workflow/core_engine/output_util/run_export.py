"""Đọc file của MỘT lượt chạy (experiment_dir/runs/<id>__.../ + experiment_dir/inputs/) và trả về
`payload` thuần (dict) cho kho SQL master — KHÔNG đụng SQL (master_db.py nhận payload và ghi).

Một đường trích xuất duy nhất: nạp tay, nạp bù và nạp tự động sau mỗi lượt đều đi qua `run_payload`
nên không thể lệch nhau. File trên đĩa là nguồn sự thật; payload dựng lại được bất cứ lúc nào.

payload = {
    "bot_build":       cột bảng bot_build (kèm file .algo và mã nguồn .cs nếu có),
    "signal_snapshot": cột bảng signal_snapshot (CSV tín hiệu đã chụp, nén gzip),
    "run":             cột bảng backtest_run (trỏ bot_build/signal_snapshot bằng hash, chưa có id SQL),
    "trades":          list dòng bảng trade,
    "daily_equity":    list dòng bảng daily_equity,
}
"""
from __future__ import annotations

import gzip
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .. import signal_trans
from ..configuration import DEFAULT_REDIS_PROFILE, load_redis_profile, read_algo_manifest
from ..models import daily_equity_rows
from . import evidence

PRAGUE = "Europe/Prague"   # luật lỗ ngày FTMO tính theo ngày giờ Prague


def _utc_iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds")


def _atr_multiple(level_name: str | None) -> float | None:
    """'Fib1618' → 1.618 (tên mức Fib = 1000 × hệ số ×ATR; xem SL_FIB_LEVELS/TP_FIB_LEVELS)."""
    if not level_name or not level_name.startswith("Fib"):
        return None
    return int(level_name[3:]) / 1000


def _resolved_parameters(manifest: dict[str, Any], raw: dict[str, str]) -> dict[str, str]:
    """Tham số enum lưu trong spec dưới dạng SỐ THỨ TỰ ("4"); đổi sang TÊN mức ("Fib1618") theo
    chính manifest của bản build đã chạy (nguồn đáng tin: enum đổi thang thì số thứ tự đổi nghĩa)."""
    by_name = {p["PropertyName"]: p for p in manifest["Types"][0]["Parameters"]}
    out: dict[str, str] = {}
    for name, value in raw.items():
        enum_values = (by_name.get(name) or {}).get("EnumValues")
        if enum_values:
            inverse = {index: label for label, index in enum_values.items()}
            out[name] = inverse.get(int(value), value)
        else:
            out[name] = value
    return out


def _channel_and_key(experiment: dict[str, Any], spec: dict[str, Any]) -> tuple[str, str | None]:
    """Nhãn nguồn Redis và tên key của list tín hiệu. Nhãn = `label` khai trong config.yaml cho profile ('original' RAW /
    'knn' / 'breakout'), KHÔNG suy từ tiền tố; lượt có các kênh lấy từ nhiều nguồn khác nhau thì nhãn là 'mixed'. Nguồn
    mặc định chỉ được ghi ở experiment.json (`redis_profile`; thiếu = mặc định); kênh đè nguồn thì `source` nằm trong
    spec["channels"]."""
    default_id = str((experiment.get("config") or {}).get("redis_profile") or DEFAULT_REDIS_PROFILE)
    source_ids = sorted({str(item.get("source") or default_id) for item in spec.get("channels") or []} or {default_id})
    try:
        profiles = [load_redis_profile(source_id) for source_id in source_ids]
    except KeyError:
        # Profile đã bỏ khỏi config.yaml (vd 'sen05-signal-trend-v1' của db1 trước 23/10/2026): giữ nhãn cũ.
        return ("trend" if "trend" in default_id else "original"), None
    labels = {profile.label for profile in profiles}
    channel = labels.pop() if len(labels) == 1 else "mixed"
    key_prefix = profiles[0].key_prefix if len(profiles) == 1 else None
    group = signal_trans.SYMBOL_GROUPS.get(spec["symbol"])
    # `strategy` của BacktestBot là token Redis ("combo"); lượt nhiều chiến lược ("combo+ma_cross") và lượt cũ (tên
    # profile "combomulti"...) không có đúng một key Redis nên key để trống.
    strategy = spec["strategy"]
    token = strategy if strategy in signal_trans.STRATEGY_COLUMNS else None
    redis_key = (
        signal_trans.signal_list_key(group, spec["timeframe"], token, key_prefix=key_prefix)
        if group and token and key_prefix
        else None
    )
    return channel, redis_key


def _selection_labels(experiment_dir: Path, spec: dict[str, Any]) -> dict[str, Any]:
    """Nhãn của một lượt KIỂM walk-forward: luật nào chọn ra tham số này, chọn theo tiêu chí nào, và run_key của lượt HỌC
    đã được chọn. Pipeline ghi `selection.json` TRƯỚC khi chạy pha kiểm và gắn `tags.rule` vào spec của lượt kiểm, nên ghép
    bằng (cửa sổ, luật, symbol, khung). Lượt không phải lượt kiểm (hoặc không còn lượt học) → nhãn rỗng."""
    rule = (spec.get("tags") or {}).get("rule")
    selection_path = experiment_dir / "selection.json"
    empty = {"selection_rule": None, "selection_objective": None, "train_run_key": None}
    if not rule or not selection_path.is_file():
        return empty
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    decision = next(
        (
            item for item in selection.get("decisions") or []
            if item["rule"] == rule and item["test_window"] == spec.get("window")
            and item["symbol"] == spec["symbol"] and item["timeframe"] == spec["timeframe"]
        ),
        None,
    )
    if decision is None:
        return empty
    train_run_key = None
    if decision.get("train_run_id"):
        for run_dir in (experiment_dir / "runs").glob(f"{decision['train_run_id']}__*"):
            train_run_key = json.loads((run_dir / "execution.json").read_text(encoding="utf-8"))["param_hash"]
    return {"selection_rule": rule, "selection_objective": selection.get("objective"), "train_run_key": train_run_key}


def run_payload(experiment_dir: str | Path, run_dir: str | Path, *, timezone_name: str = PRAGUE) -> dict[str, Any]:
    experiment_dir, run_dir = Path(experiment_dir), Path(run_dir)
    execution = json.loads((run_dir / "execution.json").read_text(encoding="utf-8"))
    experiment = json.loads((experiment_dir / "experiment.json").read_text(encoding="utf-8"))
    spec = execution["spec"]
    inputs = execution["inputs"]
    inputs_dir = experiment_dir / "inputs"

    # --- bot_build: .algo và mã nguồn .cs nếu lượt này có chụp -------------------------------
    algo_path = inputs_dir / f"{inputs['algo_sha256']}.algo"
    manifest = read_algo_manifest(algo_path)
    source_sha = inputs.get("source_sha256")
    source_path = inputs_dir / f"{source_sha}.cs" if source_sha else None
    has_source = source_path is not None and source_path.is_file()
    bot_build = {
        "strategy": spec["strategy"],
        "algo_sha256": inputs["algo_sha256"],
        "algo_file": algo_path.read_bytes(),
        "build_time_utc": manifest.get("Timestamp"),
        "source_sha256": source_sha if has_source else None,
        # đọc bytes rồi decode (không để Python đổi kiểu xuống dòng) → bản lưu trùng TỪNG BYTE với file .cs
        "source_code": source_path.read_bytes().decode("utf-8") if has_source else None,
        "source_modified_utc": _utc_iso(source_path.stat().st_mtime) if has_source else None,
    }

    # --- signal_snapshot: CSV tín hiệu đã chụp lúc chạy ------------------------------------------------------
    csv_path = inputs_dir / f"{inputs['signal_sha256']}.csv"
    csv_lines = csv_path.read_text(encoding="utf-8").splitlines()
    data_lines = [line for line in csv_lines[1:] if line.strip()]
    channel, redis_key = _channel_and_key(experiment, spec)
    signal_snapshot = {
        "channel": channel,
        "strategy": spec["strategy"],
        "symbol": spec["symbol"],
        "timeframe": spec["timeframe"],
        "redis_key": redis_key,
        "content_sha256": inputs["signal_sha256"],
        "last_bar_time": data_lines[-1].split(",")[0] if data_lines else None,
        "rows_compressed": gzip.compress(csv_path.read_bytes()),
        "captured_at_utc": _utc_iso(csv_path.stat().st_mtime),
    }

    # --- run + trades + daily_equity từ report (không có nếu lượt lỗi) --------------------------------------
    report_path = run_dir / "report.json.gz"
    report = evidence.read_report(report_path) if report_path.is_file() else None
    events_path = run_dir / "events.json.gz"
    events = evidence.read_report(events_path) if events_path.is_file() else None
    metrics = evidence.metrics(report) if report else {}
    environment = evidence.environment(report) if report else {}
    symbol_spec_json, symbol_spec_sha = evidence.symbol_spec_and_hash(environment) if report else (None, None)
    params = _resolved_parameters(manifest, spec["params"])
    flags = execution.get("validity_flags") or {}
    run = {
        "run_key": execution["param_hash"],
        "batch_name": experiment["name"],
        "method": experiment.get("method"),
        **_selection_labels(experiment_dir, spec),
        "window_name": spec.get("window"),
        "zone": spec.get("zone"),
        "strategy": spec["strategy"],
        "symbol": spec["symbol"],
        "timeframe": spec["timeframe"],
        "start_date": spec["start"],
        "end_date_requested": spec["end"],
        "last_data_time": evidence.last_data_time(report) if report else None,
        "initial_balance": spec["balance"],
        "ksl_atr_multiple": _atr_multiple(params.get("KslLevel")),
        "ktp_atr_multiple": _atr_multiple(params.get("KtpLevel")),
        "risk_percent": float(spec["params"]["RiskPercent"]) if "RiskPercent" in spec["params"] else None,
        "leg_count": int(spec["params"].get("LegCount", 1)),
        "parameters_json": json.dumps(params, sort_keys=True),
        "signal_window_sha256": signal_trans.signal_window_sha256(csv_path, date.fromisoformat(spec["end"])),
        "engine_profile": spec["engine_profile"],
        "engine_json": json.dumps({
            key: spec.get(key)
            for key in ("data_mode", "precise_conversion", "commission", "commission_type", "commission_auto", "spread")
        }, sort_keys=True),
        "cli_version": (execution.get("cli") or {}).get("version"),
        "symbol_spec_json": symbol_spec_json,
        "symbol_spec_sha256": symbol_spec_sha,
        "status": execution["status"],   # KHÔNG lưu: chỉ để master_db.save_run lọc lượt hoàn tất
        "margin_rejections": flags.get("margin_rejections"),
        "net_profit": metrics.get("net_profit"),
        "profit_factor": metrics.get("profit_factor"),
        "total_trades": metrics.get("total_trades"),
        "win_rate": metrics.get("win_rate"),
        "max_equity_drawdown_percent": metrics.get("max_equity_drawdown_pct"),
        "bot_counters_json": json.dumps(execution.get("bot_summary") or {}, sort_keys=True),
        "started_utc": (execution.get("timing") or {}).get("started_utc"),
        "ended_utc": (execution.get("timing") or {}).get("ended_utc"),
    }
    return {
        "bot_build": bot_build,
        "signal_snapshot": signal_snapshot,
        "run": run,
        "trades": evidence.trades(report, events) if report else [],
        "daily_equity": (
            daily_equity_rows(report["equity"]["points"], timezone_name, float(spec["balance"])) if report else []
        ),
    }
