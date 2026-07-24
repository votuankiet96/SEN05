"""Lightweight helpers for the single-run notebook dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path
import re
from typing import Any

import pandas as pd

from backtest_optimize.contracts import MarketSpec
from backtest_optimize.execution.entry import ENTRY_REGISTRY
from backtest_optimize.execution.exits import EXIT_REGISTRY
from backtest_optimize.execution.sl_calculator import SL_REGISTRY
from backtest_optimize.execution.tp_calculator import (
    CBOT_V0_KTP_LEVELS,
    DEFAULT_KTP_LEVEL,
    KTP_FIB_LEVELS,
    TP_REGISTRY,
)


SIGNAL_FILENAME_RE = re.compile(
    r"^(?P<strategy>[^_]+)_(?P<symbol>[^_]+)_(?P<timeframe>[^_]+)_"
    r"(?P<start>\d{8})_(?P<end>\d{8})_signals\.csv$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SignalSelection:
    choice: str
    path: Path
    strategy: str
    symbol: str
    timeframe: str
    start_date: str | None = None
    end_date: str | None = None


def discover_signal_catalog(raw_signals_root: Path, strategy: str = "combo") -> pd.DataFrame:
    """Return a compact catalog of signal CSV files for a strategy folder."""
    folder = Path(raw_signals_root) / strategy
    rows: list[dict[str, Any]] = []
    if not folder.exists():
        return pd.DataFrame(
            columns=["choice", "strategy", "symbol", "timeframe", "start_date", "end_date", "path"]
        )

    for path in sorted(folder.glob("*_signals.csv")):
        meta = _parse_signal_filename(path.name)
        rows.append(
            {
                "choice": path.name,
                "strategy": meta.get("strategy") or strategy,
                "symbol": meta.get("symbol"),
                "timeframe": meta.get("timeframe"),
                "start_date": meta.get("start_date"),
                "end_date": meta.get("end_date"),
                "path": path,
            }
        )

    return pd.DataFrame(rows)


def select_signal(
    catalog: pd.DataFrame,
    *,
    choice: str = "auto",
    symbol: str | None = None,
    timeframe: str | None = None,
) -> SignalSelection:
    """Pick one signal file from a catalog by filename or symbol/timeframe."""
    if catalog.empty:
        raise ValueError("No signal files found for the selected strategy folder.")

    frame = catalog.copy()
    if choice and choice != "auto":
        matched = frame[frame["choice"].astype(str).eq(str(choice))]
        if matched.empty:
            raise ValueError(f"Signal choice not found: {choice}")
        row = matched.iloc[0]
    else:
        matched = frame
        if symbol:
            matched = matched[matched["symbol"].astype(str).str.upper().eq(str(symbol).upper())]
        if timeframe:
            matched = matched[matched["timeframe"].astype(str).str.upper().eq(str(timeframe).upper())]
        row = (matched if not matched.empty else frame).iloc[0]

    return SignalSelection(
        choice=str(row["choice"]),
        path=Path(row["path"]),
        strategy=str(row["strategy"]),
        symbol=str(row["symbol"]).upper(),
        timeframe=str(row["timeframe"]).upper(),
        start_date=_optional_str(row.get("start_date")),
        end_date=_optional_str(row.get("end_date")),
    )


def component_catalog_frame() -> pd.DataFrame:
    """Return execution components currently available to notebook controls."""
    rows = []
    for component_type, names in (
        ("entry", ENTRY_REGISTRY),
        ("stoploss", SL_REGISTRY),
        ("takeprofit", TP_REGISTRY),
        ("exit", EXIT_REGISTRY),
    ):
        rows.extend((component_type, name) for name in sorted(names))
    rows.extend(("order", name) for name in ("market", "stop_order"))
    rows.extend(("sizing", name) for name in ("fixed_risk_percent",))
    return pd.DataFrame(rows, columns=["component", "name"])


def discover_run_artifacts(
    output_dir: str | Path,
    *,
    pattern: str = "*.csv",
) -> pd.DataFrame:
    """Return newest-first output files for notebook parent-run selection."""
    folder = Path(output_dir)
    rows = []
    for path in sorted(folder.glob(pattern), key=lambda item: item.stat().st_mtime, reverse=True):
        rows.append(
            {
                "name": path.name,
                "path": path,
                "modified_at": pd.Timestamp(path.stat().st_mtime, unit="s"),
                "size_bytes": path.stat().st_size,
            }
        )
    return pd.DataFrame(rows, columns=["name", "path", "modified_at", "size_bytes"])


def build_execution_config(
    *,
    engine: str,
    account_size: float,
    risk_per_cluster: float,
    ambiguity_policy: Any,
    entry_model: str = "next_open",
    entry_params: dict[str, Any] | None = None,
    sl_method: str = "atr_multiple",
    sl_params: dict[str, Any] | None = None,
    tp_method: str = "risk_multiple",
    tp_params: dict[str, Any] | None = None,
    order_model: str = "market",
    order_params: dict[str, Any] | None = None,
    exit_model: str = "none",
    exit_params: dict[str, Any] | None = None,
    leg_weights: list[float] | None = None,
    position_policy: dict[str, str] | None = None,
    profile_name: str = "custom",
) -> dict[str, Any]:
    """Build one strategy-neutral execution config from dashboard controls."""
    engine = str(engine).strip().lower()
    entry_params = dict(entry_params or {})
    sl_params = dict(sl_params or {})
    tp_params = dict(tp_params or {})
    order_params = dict(order_params or {})
    exit_params = dict(exit_params or {})
    policy = position_policy or {
        "same_direction": "skip",
        "opposite_direction": "reverse",
        "reverse_exit_model": "next_open",
    }
    metadata = {
        "engine_profile": profile_name,
        "entry_model": entry_model,
        "sl_profile": sl_method,
        "tp_profile": tp_method,
        "order_model": order_model,
        "exit_model": exit_model,
        "same_direction_policy": policy["same_direction"],
        "opposite_direction_policy": policy["opposite_direction"],
        "reverse_exit_model": policy["reverse_exit_model"],
    }
    for key in ("x_offset", "x_buffer"):
        if key in entry_params or key in sl_params:
            metadata[key] = entry_params.get(key, sl_params.get(key))
    for key in ("ksl_level", "ktp_level", "r_multiples", "exit_mode"):
        if key in sl_params or key in tp_params:
            metadata[key] = sl_params.get(key, tp_params.get(key))
    if "cancel_after_bars" in order_params:
        metadata["cancel_after_bars"] = order_params["cancel_after_bars"]
    if tp_method == "fib_atr_ctrader_v0" and "ktp_level" in tp_params:
        metadata["ktp"] = CBOT_V0_KTP_LEVELS.get(int(tp_params["ktp_level"]))
    elif tp_method == "combo_fib_atr" and "ktp_level" in tp_params:
        metadata["ktp"] = KTP_FIB_LEVELS.get(int(tp_params["ktp_level"]))
    common = {
        "engine": engine,
        "account_size": float(account_size),
        "risk_per_cluster": float(risk_per_cluster),
        "sl_method": sl_method,
        "sl_params": sl_params,
        "tp_method": tp_method,
        "tp_params": tp_params,
        "leg_weights": leg_weights,
        "ambiguity_policy": ambiguity_policy,
        "position_policy": dict(policy),
        "config": metadata,
    }
    if engine == "single":
        if entry_model != "next_open" or order_model != "market":
            raise ValueError("single engine supports entry_model='next_open' and order_model='market'.")
        common["management"] = exit_params
        return common
    if engine != "component":
        raise ValueError("engine must be 'single' or 'component'.")
    common.update(
        {
            "entry_model": entry_model,
            "entry_params": entry_params,
            "order_model": order_model,
            "order_params": order_params,
            "exit_model": exit_model,
            "exit_params": exit_params,
        }
    )
    return common


def build_run_config(
    *,
    account_size: float,
    risk_per_cluster: float,
    x_buffer: float = 10.0,
    engine_profile: str = "combo_research",
    tp_profile: str = "combo_fib_atr",
    ktp_level: int = DEFAULT_KTP_LEVEL,
    ksl_level: int = 2,
    cancel_after_bars: int = 3,
    r_multiples: list[float] | None = None,
    ambiguity_policy: Any,
    sl_move_rule: str = "none",
) -> dict[str, Any]:
    """Build the run_single config from dashboard controls."""
    r_multiples = list(r_multiples or [1.0, 2.0, 3.0])
    engine_profile = str(engine_profile)

    if engine_profile == "combo_ctrader_v0":
        if ktp_level not in CBOT_V0_KTP_LEVELS:
            raise ValueError("ktp_level must be between 1 and 12 for combo_ctrader_v0.")
        return {
            "engine": "component",
            "account_size": float(account_size),
            "risk_per_cluster": float(risk_per_cluster),
            "entry_model": "stop_breakout_signal_bar",
            "entry_params": {"x_offset": float(x_buffer)},
            "sl_method": "signal_bar_atr_buffer",
            "sl_params": {"ksl_level": int(ksl_level)},
            "tp_method": "fib_atr_ctrader_v0",
            "tp_params": {"ktp_level": int(ktp_level), "exit_mode": tp_profile},
            "order_model": "stop_order",
            "order_params": {"cancel_after_bars": int(cancel_after_bars)},
            "exit_model": "two_legs_sma20" if str(tp_profile) == "two_legs" else "fixed_only",
            "exit_params": {"sma_period": 20},
            "ambiguity_policy": ambiguity_policy,
            "position_policy": {
                "same_direction": "skip",
                "opposite_direction": "reverse",
                "reverse_exit_model": "next_open",
            },
            "config": {
                "engine_profile": engine_profile,
                "entry_model": "stop_breakout_signal_bar",
                "sl_profile": "signal_bar_atr_buffer",
                "tp_profile": "fib_atr_ctrader_v0",
                "order_model": "stop_order",
                "same_direction_policy": "skip",
                "opposite_direction_policy": "reverse",
                "reverse_exit_model": "next_open",
                "x_offset": float(x_buffer),
                "ksl_level": int(ksl_level),
                "ktp_level": int(ktp_level),
                "ktp": CBOT_V0_KTP_LEVELS.get(int(ktp_level)),
                "cancel_after_bars": int(cancel_after_bars),
                "exit_mode": tp_profile,
            },
        }

    if tp_profile == "combo_fib_atr":
        if ktp_level not in KTP_FIB_LEVELS:
            raise ValueError("ktp_level must be between 1 and 6.")
        tp_method = "combo_fib_atr"
        tp_params: dict[str, Any] = {"ktp_level": int(ktp_level)}
    elif tp_profile == "risk_multiple":
        tp_method = "risk_multiple"
        tp_params = {"r_multiples": r_multiples}
    else:
        raise ValueError(f"Unsupported tp_profile: {tp_profile}")

    return {
        "account_size": float(account_size),
        "risk_per_cluster": float(risk_per_cluster),
        "sl_method": "combo_signal_bar",
        "sl_params": {"x_buffer": float(x_buffer)},
        "tp_method": tp_method,
        "tp_params": tp_params,
        "ambiguity_policy": ambiguity_policy,
        "management": {"sl_move_rule": str(sl_move_rule)},
        "position_policy": {
            "same_direction": "skip",
            "opposite_direction": "reverse",
            "reverse_exit_model": "next_open",
        },
        "config": {
            "engine_profile": engine_profile,
            "entry_model": "next_open",
            "sl_profile": "combo_signal_bar",
            "tp_profile": tp_profile,
            "same_direction_policy": "skip",
            "opposite_direction_policy": "reverse",
            "reverse_exit_model": "next_open",
            "x_buffer": float(x_buffer),
            "ktp_level": int(ktp_level),
            "ktp": KTP_FIB_LEVELS.get(int(ktp_level)),
            "r_multiples": r_multiples,
        },
    }


def control_panel_frame(
    *,
    selection: SignalSelection,
    run_config: dict[str, Any],
    market_spec: MarketSpec,
    warmup_bars: int,
) -> pd.DataFrame:
    """Return the selected dashboard controls as a readable table."""
    metadata = run_config.get("config", {})
    rows = [
        ("Signal", "strategy", selection.strategy),
        ("Signal", "file", selection.choice),
        ("Signal", "symbol/timeframe", f"{selection.symbol} {selection.timeframe}"),
        ("Signal", "signal range", f"{selection.start_date or '?'} -> {selection.end_date or '?'}"),
        ("Data", "warmup bars", warmup_bars),
        ("Execution", "engine", run_config.get("engine", "single")),
        ("Execution", "entry model", metadata.get("entry_model", "next_open")),
        ("Execution", "SL method", run_config.get("sl_method")),
        ("Execution", "x_offset", metadata.get("x_offset", metadata.get("x_buffer"))),
        ("Execution", "KSL level", metadata.get("ksl_level")),
        ("Execution", "TP profile", metadata.get("tp_profile")),
        ("Execution", "exit mode", metadata.get("exit_mode")),
        ("Execution", "order model", run_config.get("order_model", "market")),
        ("Execution", "cancel after bars", metadata.get("cancel_after_bars")),
        ("Execution", "exit model", run_config.get("exit_model", metadata.get("exit_model", "none"))),
        ("Execution", "same direction", metadata.get("same_direction_policy")),
        ("Execution", "opposite direction", metadata.get("opposite_direction_policy")),
        ("Execution", "KTP", _ktp_label(metadata)),
        ("Execution", "R multiples", metadata.get("r_multiples")),
        ("Execution", "ambiguity", getattr(run_config.get("ambiguity_policy"), "value", run_config.get("ambiguity_policy"))),
        ("Execution", "SL move rule", run_config.get("management", {}).get("sl_move_rule")),
        ("Risk", "account size", run_config.get("account_size")),
        ("Risk", "risk per cluster", run_config.get("risk_per_cluster")),
        ("Market", "pip size/value", f"{market_spec.pip_size} / {market_spec.pip_value_per_lot}"),
        ("Market", "commission/spread/slippage", _cost_label(market_spec)),
    ]
    return pd.DataFrame(rows, columns=["section", "field", "value"])


def run_context_frame(
    *,
    signals: pd.DataFrame,
    bars: pd.DataFrame,
    selection: SignalSelection,
    run_config: dict[str, Any],
) -> pd.DataFrame:
    """Return compact runtime context after data has loaded."""
    rows = [
        ("Signal rows", f"{len(signals):,}", "Rows loaded from the selected raw signal file."),
        ("Signal period", _range_label(signals.get("bartime")), "Signal bartime min/max."),
        ("OHLCV bars", f"{len(bars):,}", "Bars loaded from core data for simulation."),
        ("OHLCV period", _range_label(bars.get("bartime")), "Market data min/max."),
        ("Selected file", selection.choice, "Current signal source."),
        ("Entry model", run_config.get("config", {}).get("entry_model", "next_open"), "Entry component selected for this run."),
    ]
    return pd.DataFrame(rows, columns=["metric", "value", "meaning"])


def summary_report_frame(summary: dict[str, Any]) -> pd.DataFrame:
    """Return a readable, sectioned summary report."""
    specs = [
        ("Data Health", "signal_count", "Total signal rows considered."),
        ("Data Health", "accepted_count", "Signals accepted into simulation."),
        ("Data Health", "reversed_count", "Clusters closed because an opposite signal arrived."),
        ("Data Health", "skipped_count", "Signals truly skipped, such as same-direction exposure or execution errors."),
        ("Data Health", "skip_rate", "Skipped signals / total signals."),
        ("Data Health", "same_direction_skip_count", "Signals skipped because same-direction exposure already exists."),
        ("Data Health", "execution_error_count", "Signals skipped due to calculation/runtime errors."),
        ("Data Health", "open_at_end_count", "Clusters still open at the end of data."),
        ("Performance", "expectancy_r", "Average R per closed eligible cluster."),
        ("Performance", "std_r", "Dispersion of R outcomes."),
        ("Performance", "sqn", "System Quality Number. Interpret only with enough trades."),
        ("Performance", "sl_hit_rate", "Accepted clusters with at least one SL leg hit."),
        ("Performance", "tp1_hit_rate", "Eligible clusters that hit TP1."),
        ("Performance", "tp2_hit_rate", "Eligible clusters that hit TP2."),
        ("Performance", "tp3_hit_rate", "Eligible clusters that hit TP3."),
        ("Path", "mean_bar_mae_r", "Average adverse excursion in R."),
        ("Path", "mean_bar_mfe_r", "Average favorable excursion in R."),
        ("Path", "mean_holding_bars", "Average holding time in bars."),
        ("Assumptions", "ambiguity_rate", "Accepted clusters with ambiguous bars."),
        ("Assumptions", "total_cost_r", "Total modeled cost in R."),
    ]
    rows = []
    for section, key, meaning in specs:
        if key in summary:
            rows.append((section, key, _format_value(summary.get(key)), meaning))
    return pd.DataFrame(rows, columns=["section", "metric", "value", "meaning"])


def warning_notes_frame(summary: dict[str, Any], run_config: dict[str, Any], market_spec: MarketSpec) -> pd.DataFrame:
    """Return cautious notes that make the report harder to over-read."""
    notes: list[tuple[str, str]] = []

    if summary.get("total_cost_r", 0.0) == 0.0 and (
        market_spec.commission_per_lot_per_side == 0
        and market_spec.spread_buffer_pips == 0
        and market_spec.slippage_buffer_pips == 0
    ):
        notes.append(("Caution", "Costs are zero, so results are optimistic before real trading costs."))
    if _as_float(summary.get("skip_rate")) is not None and float(summary["skip_rate"]) >= 0.30:
        notes.append(("Caution", "Skip rate is high; inspect same-direction skips and execution errors."))
    if _as_float(summary.get("reversed_count")):
        notes.append(("Info", "Opposite-direction signals close the active cluster and start a new one."))
    if summary.get("open_at_end_count", 0):
        notes.append(("Info", "Open-at-end clusters are excluded from R expectancy metrics."))
    if _as_float(summary.get("ambiguity_rate")):
        notes.append(("Caution", "Some bars are ambiguous; current policy controls TP/SL ordering."))
    if run_config.get("tp_method") == "combo_fib_atr":
        notes.append(("Info", "TP is Combo Fibonacci ATR; ktp_level maps directly to cTrader."))
    if run_config.get("tp_method") == "fib_atr_ctrader_v0":
        notes.append(("Info", "Preset uses cBot Combo V0 KTP/KSL tables with OHLC stop-order fill rules."))
    if run_config.get("tp_method") == "risk_multiple":
        notes.append(("Info", "TP is R-multiple mode; use this for A/B testing against Combo TP."))

    if not notes:
        notes.append(("Info", "No major caution flags from summary/config."))
    return pd.DataFrame(notes, columns=["level", "note"])


def dashboard_cards_html(summary: dict[str, Any], run_config: dict[str, Any], market_spec: MarketSpec) -> str:
    """Return compact run cards for the notebook top section."""
    del run_config, market_spec
    signal_count = _as_float(summary.get("signal_count")) or 0
    accepted = _as_float(summary.get("accepted_count")) or 0
    skipped = _as_float(summary.get("skipped_count")) or 0
    reversed_count = _as_float(summary.get("reversed_count")) or 0
    reversed_win_rate = _as_float(summary.get("reversed_win_rate"))
    reversed_avg_r = _as_float(summary.get("reversed_avg_r"))
    expectancy = _as_float(summary.get("expectancy_r"))
    tp1_hit = _as_float(summary.get("tp1_hit_rate"))
    sl_hit = _as_float(summary.get("sl_hit_rate"))
    mean_mae = _as_float(summary.get("mean_bar_mae_r"))
    mean_mfe = _as_float(summary.get("mean_bar_mfe_r"))

    cards = [
        (
            "Signals",
            f"{int(accepted):,} / {int(signal_count):,}",
            f"Skipped {_format_percent(skipped / signal_count if signal_count else None)} | Reversed {int(reversed_count):,}",
            "neutral",
        ),
        (
            "Reverse",
            f"{int(reversed_count):,}",
            f"Win {_format_percent(reversed_win_rate)} | Avg {_format_r(reversed_avg_r)}",
            _result_tone(reversed_avg_r),
        ),
        (
            "Expectancy",
            _format_r(expectancy),
            "Average R per eligible cluster",
            _result_tone(expectancy),
        ),
        (
            "TP1 / SL",
            f"{_format_percent(tp1_hit)} / {_format_percent(sl_hit)}",
            "Cluster hit rates",
            "neutral",
        ),
        (
            "Path",
            f"MAE {_format_r(mean_mae)} | MFE {_format_r(mean_mfe)}",
            "Average path excursion",
            "neutral",
        ),
    ]
    card_html = "\n".join(
        (
            f'<div class="bt-run-card {escape(tone)}">'
            f'<div class="bt-run-label">{escape(label)}</div>'
            f'<div class="bt-run-value">{escape(value)}</div>'
            f'<div class="bt-run-caption">{escape(caption)}</div>'
            "</div>"
        )
        for label, value, caption, tone in cards
    )
    return f"""
<style>
.bt-run-cards {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px;
  margin: 8px 0 14px 0;
}}
.bt-run-card {{
  background: #f9fafb;
  border: 1px solid #d1d5db;
  border-left: 4px solid #6b7280;
  border-radius: 6px;
  padding: 10px 12px;
  color: #111827;
}}
.bt-run-card.positive {{ border-left-color: #16a34a; }}
.bt-run-card.negative {{ border-left-color: #dc2626; }}
.bt-run-label {{
  color: #4b5563;
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
}}
.bt-run-value {{
  color: #111827;
  font-size: 20px;
  font-weight: 800;
  line-height: 1.25;
  margin-top: 4px;
}}
.bt-run-caption {{
  color: #4b5563;
  font-size: 12px;
  margin-top: 4px;
}}
</style>
<div class="bt-run-cards">
{card_html}
</div>
"""


def dashboard_interpretation_frame(
    summary: dict[str, Any],
    run_config: dict[str, Any],
    market_spec: MarketSpec,
) -> pd.DataFrame:
    """Return plain-language readings for the current run."""
    rows: list[tuple[str, str, str]] = []
    expectancy = _as_float(summary.get("expectancy_r"))
    skip_rate = _as_float(summary.get("skip_rate"))
    reversed_count = int(_as_float(summary.get("reversed_count")) or 0)
    reversed_rate = _as_float(summary.get("reversed_rate"))
    reversed_win_rate = _as_float(summary.get("reversed_win_rate"))
    reversed_avg_r = _as_float(summary.get("reversed_avg_r"))
    reversed_profit_count = int(_as_float(summary.get("reversed_profit_count")) or 0)
    reversed_loss_count = int(_as_float(summary.get("reversed_loss_count")) or 0)
    ambiguity_rate = _as_float(summary.get("ambiguity_rate"))
    open_at_end = int(_as_float(summary.get("open_at_end_count")) or 0)
    tp1_hit = _as_float(summary.get("tp1_hit_rate"))
    sl_hit = _as_float(summary.get("sl_hit_rate"))

    if expectancy is None:
        rows.append(("Result", "Not enough eligible clusters", "Expectancy R is not available yet."))
    elif expectancy > 0:
        rows.append(("Result", "Positive expectancy", f"Eligible clusters average {_format_r(expectancy)}."))
    elif expectancy < 0:
        rows.append(("Result", "Negative expectancy", f"Eligible clusters average {_format_r(expectancy)}."))
    else:
        rows.append(("Result", "Flat expectancy", "Eligible clusters average 0.000R."))

    rows.append(
        (
            "Execution",
            "TP/SL profile",
            f"TP1 hit {_format_percent(tp1_hit)}; SL hit {_format_percent(sl_hit)}.",
        )
    )
    if reversed_count:
        rows.append(
            (
                "Execution",
                "Reverse on opposite signal",
                f"{reversed_count:,} cluster(s) closed by opposite signal; "
                f"profit/loss {reversed_profit_count:,}/{reversed_loss_count:,}; "
                f"win {_format_percent(reversed_win_rate)}; avg {_format_r(reversed_avg_r)}; "
                f"rate {_format_percent(reversed_rate)}.",
            )
        )
    if skip_rate is not None and skip_rate >= 0.30:
        rows.append(("Data", "High skip rate", f"{_format_percent(skip_rate)} of signals are skipped."))
    else:
        rows.append(("Data", "Skip rate", f"{_format_percent(skip_rate)} of signals are skipped."))
    if open_at_end:
        rows.append(("Data", "Open at end", f"{open_at_end:,} cluster(s) are excluded from R metrics."))
    if ambiguity_rate:
        rows.append(
            (
                "Assumption",
                "Ambiguous bars exist",
                f"{_format_percent(ambiguity_rate)} of accepted clusters have ambiguity.",
            )
        )
    if (
        market_spec.commission_per_lot_per_side == 0
        and market_spec.spread_buffer_pips == 0
        and market_spec.slippage_buffer_pips == 0
    ):
        rows.append(("Cost", "Zero modeled costs", "Treat the result as optimistic until trading costs are configured."))
    rows.append(
        (
            "Config",
            "Execution rule",
            f"Entry={run_config.get('config', {}).get('entry_model', 'next_open')}; "
            f"SL={run_config.get('sl_method')}; TP={run_config.get('tp_method')}; "
            f"Opposite={run_config.get('position_policy', {}).get('opposite_direction', 'skip')}.",
        )
    )
    return pd.DataFrame(rows, columns=["section", "reading", "detail"])


def dashboard_metric_frame(summary: dict[str, Any]) -> pd.DataFrame:
    """Return the main metrics with clearer labels and readings."""
    specs = [
        ("Sample", "Total signals", "signal_count", "All signal rows considered by this run.", "int"),
        ("Sample", "Accepted clusters", "accepted_count", "Signals converted into simulated clusters.", "int"),
        ("Sample", "Filled clusters", "filled_count", "Orders that became simulated positions.", "int"),
        ("Sample", "Fill rate", "fill_rate", "Filled positions / accepted non-skipped signals.", "pct"),
        ("Sample", "Pending expired", "pending_expired_count", "Pending orders that expired before fill.", "int"),
        ("Sample", "Pending expired rate", "pending_expired_rate", "Expired pending orders / accepted signals.", "pct"),
        ("Sample", "Pending cancelled", "pending_cancelled_count", "Pending orders cancelled by lifecycle rules.", "int"),
        ("Sample", "Pending unfilled", "pending_unfilled_count", "Pending orders still unfilled at data end.", "int"),
        ("Sample", "Reversed clusters", "reversed_count", "Clusters closed by an opposite-direction signal.", "int"),
        ("Sample", "Reversed rate", "reversed_rate", "Reversed clusters / accepted clusters.", "pct"),
        ("Sample", "Reversed profit", "reversed_profit_count", "Reversed clusters closed with r_result > 0.", "int"),
        ("Sample", "Reversed loss", "reversed_loss_count", "Reversed clusters closed with r_result < 0.", "int"),
        ("Sample", "Reversed flat", "reversed_flat_count", "Reversed clusters closed around flat R.", "int"),
        ("Sample", "Reversed win rate", "reversed_win_rate", "Profitable reversed clusters / reversed clusters.", "pct"),
        ("Sample", "Reversed avg R", "reversed_avg_r", "Average R of reversed clusters only.", "r"),
        ("Sample", "Skipped signals", "skipped_count", "Signals not simulated as clusters.", "int"),
        ("Sample", "Skip rate", "skip_rate", "Skipped / total signals.", "pct"),
        ("Sample", "Same-direction skips", "same_direction_skip_count", "Signals skipped because same-direction exposure exists.", "int"),
        ("Sample", "Execution errors", "execution_error_count", "Signals skipped due to calculation/runtime errors.", "int"),
        ("Sample", "Open at end", "open_at_end_count", "Open clusters excluded from expectancy.", "int"),
        ("Performance", "Expectancy R", "expectancy_r", "Average net R per eligible cluster.", "r"),
        ("Performance", "Std R", "std_r", "Dispersion of cluster R outcomes.", "r"),
        ("Performance", "SQN", "sqn", "System Quality Number; useful only with enough trades.", "num"),
        ("Performance", "SL hit rate", "sl_hit_rate", "Accepted clusters with at least one SL leg hit.", "pct"),
        ("Performance", "TP1 hit rate", "tp1_hit_rate", "Eligible clusters that hit TP1.", "pct"),
        ("Path", "Mean MAE R", "mean_bar_mae_r", "Average adverse excursion.", "r"),
        ("Path", "Mean MFE R", "mean_bar_mfe_r", "Average favorable excursion.", "r"),
        ("Path", "Mean holding bars", "mean_holding_bars", "Average bars from entry to exit.", "num"),
        ("Assumption", "Ambiguity rate", "ambiguity_rate", "Accepted clusters with ambiguous TP/SL bars.", "pct"),
        ("Cost", "Total cost R", "total_cost_r", "Modeled costs converted to R.", "r"),
    ]
    rows = []
    for section, label, key, reading, value_type in specs:
        if key in summary:
            rows.append((section, label, _metric_value(summary.get(key), value_type), reading))
    return pd.DataFrame(rows, columns=["section", "metric", "value", "read as"])


def status_breakdown_frame(clusters: pd.DataFrame) -> pd.DataFrame:
    """Return status counts and rates for cluster review."""
    if clusters.empty or "status" not in clusters.columns:
        return pd.DataFrame(columns=["status", "count", "rate", "meaning"])
    total = len(clusters)
    rows = []
    for status, count in clusters["status"].value_counts(dropna=False).sort_index().items():
        status_text = str(status)
        rows.append(
            (
                _status_label(status_text),
                int(count),
                _format_percent(count / total if total else None),
                _status_meaning(status_text),
            )
        )
    return pd.DataFrame(rows, columns=["status", "count", "rate", "meaning"])


def cluster_view_frame(clusters: pd.DataFrame, *, limit: int = 50) -> pd.DataFrame:
    """Backward-compatible alias for the clearer cluster review frame."""
    return cluster_review_frame(clusters, limit=limit)


def cluster_review_frame(clusters: pd.DataFrame, *, limit: int = 80, mode: str = "first") -> pd.DataFrame:
    """Return a readable cluster table for notebook review."""
    columns = [
        "#",
        "Signal Time",
        "Side",
        "Status",
        "Entry Time",
        "Entry",
        "SL",
        "TP Hit",
        "Exit Time",
        "Result R",
        "MAE R",
        "MFE R",
        "Bars",
        "Ambig",
        "Exit",
        "Note",
    ]
    if clusters.empty:
        return pd.DataFrame(columns=columns)

    source = _select_cluster_rows(clusters, mode).head(limit).copy()
    empty = pd.Series(index=source.index, dtype=object)
    out = pd.DataFrame(index=source.index)
    out["#"] = source.index + 1
    out["Signal Time"] = source.get("signal_bartime", empty).map(_time_label)
    out["Side"] = source.get("direction", empty).map(_optional_str).fillna("")
    out["Status"] = source.get("status", empty).map(_status_label)
    out["Entry Time"] = source.get("entry_time", empty).map(_time_label)
    out["Entry"] = pd.to_numeric(source.get("entry_price", empty), errors="coerce")
    out["SL"] = pd.to_numeric(source.get("sl_price", empty), errors="coerce")
    out["TP Hit"] = source.get("tp_hits", empty).map(_tp_hit_label)
    out["Exit Time"] = source.get("exit_time", empty).map(_time_label)
    out["Result R"] = pd.to_numeric(source.get("r_result", empty), errors="coerce")
    out["MAE R"] = pd.to_numeric(source.get("bar_mae_r", empty), errors="coerce")
    out["MFE R"] = pd.to_numeric(source.get("bar_mfe_r", empty), errors="coerce")
    out["Bars"] = pd.to_numeric(source.get("holding_bars", empty), errors="coerce")
    out["Ambig"] = pd.to_numeric(source.get("ambiguous_bars", empty), errors="coerce")
    out["Exit"] = source.get("exit_reasons", empty).map(_exit_reason_label)
    out["Note"] = source.apply(_cluster_note, axis=1)
    return out.loc[:, columns]


def style_report(frame: pd.DataFrame):
    """Apply a quiet report style to a DataFrame."""
    return frame.style.hide(axis="index").set_table_styles(
        [
            {"selector": "table", "props": [("border-collapse", "collapse"), ("width", "100%")]},
            {
                "selector": "th",
                "props": [
                    ("background-color", "#111827"),
                    ("color", "#f9fafb"),
                    ("font-weight", "700"),
                    ("padding", "7px 8px"),
                ],
            },
            {
                "selector": "td",
                "props": [
                    ("padding", "6px 8px"),
                    ("vertical-align", "top"),
                    ("color", "#111827"),
                    ("background-color", "#f9fafb"),
                    ("border-bottom", "1px solid #e5e7eb"),
                ],
            },
        ]
    )


def style_dashboard_frame(frame: pd.DataFrame):
    """Apply the shared readable notebook table style."""
    return style_report(frame)


def style_cluster_view(frame: pd.DataFrame):
    """Backward-compatible alias for the clearer cluster review style."""
    return style_cluster_review(frame)


def cluster_review_html(frame: pd.DataFrame) -> str:
    """Return cluster review HTML wrapped in a horizontal scroll container."""
    return (
        '<div class="bt-cluster-review-wrap" '
        'style="width:100%; overflow-x:auto; border:1px solid #d1d5db; border-radius:6px;">'
        f"{style_cluster_review(frame).to_html()}"
        "</div>"
    )


def style_cluster_review(frame: pd.DataFrame):
    """Apply high-contrast, fixed-width styling to the cluster review table."""
    formatters = {
        "Entry": "{:,.2f}",
        "SL": "{:,.2f}",
        "Result R": "{:+.3f}",
        "MAE R": "{:.2f}",
        "MFE R": "{:.2f}",
        "Bars": "{:.0f}",
        "Ambig": "{:.0f}",
    }
    active_formatters = {key: value for key, value in formatters.items() if key in frame.columns}
    return (
        frame.style.hide(axis="index")
        .format(active_formatters, na_rep="-")
        .apply(_cluster_review_row_style, axis=1)
        .set_table_styles(
            [
                {
                    "selector": "table",
                    "props": [
                        ("border-collapse", "collapse"),
                        ("min-width", "1320px"),
                        ("width", "max-content"),
                        ("font-size", "12px"),
                        ("line-height", "1.35"),
                    ],
                },
                {
                    "selector": "th",
                    "props": [
                        ("background-color", "#111827"),
                        ("color", "#f9fafb"),
                        ("font-weight", "700"),
                        ("padding", "8px 9px"),
                        ("border-bottom", "1px solid #374151"),
                        ("white-space", "nowrap"),
                        ("text-align", "left"),
                    ],
                },
                {
                    "selector": "td",
                    "props": [
                        ("padding", "7px 9px"),
                        ("border-bottom", "1px solid #e5e7eb"),
                        ("white-space", "nowrap"),
                        ("color", "#111827"),
                    ],
                },
                {"selector": "td:nth-child(1)", "props": [("width", "42px"), ("text-align", "right")]},
                {"selector": "td:nth-child(2)", "props": [("width", "138px"), ("font-family", "Consolas, monospace")]},
                {"selector": "td:nth-child(4)", "props": [("font-weight", "700")]},
                {"selector": "td:nth-child(5)", "props": [("width", "138px"), ("font-family", "Consolas, monospace")]},
                {"selector": "td:nth-child(6)", "props": [("text-align", "right"), ("font-family", "Consolas, monospace")]},
                {"selector": "td:nth-child(7)", "props": [("text-align", "right"), ("font-family", "Consolas, monospace")]},
                {"selector": "td:nth-child(9)", "props": [("width", "138px"), ("font-family", "Consolas, monospace")]},
                {"selector": "td:nth-child(10)", "props": [("text-align", "right"), ("font-weight", "700")]},
                {"selector": "td:nth-child(11)", "props": [("text-align", "right")]},
                {"selector": "td:nth-child(12)", "props": [("text-align", "right")]},
                {"selector": "td:nth-child(13)", "props": [("text-align", "right")]},
                {"selector": "td:nth-child(14)", "props": [("text-align", "right")]},
                {"selector": "td:nth-child(15)", "props": [("min-width", "110px")]},
                {"selector": "td:nth-child(16)", "props": [("min-width", "190px")]},
            ]
        )
    )


def _cluster_row_style(row: pd.Series) -> list[str]:
    return _cluster_review_row_style(row)


def _cluster_review_row_style(row: pd.Series) -> list[str]:
    status = str(row.get("status", "")).lower()
    if not status:
        status = str(row.get("Status", "")).lower()
    r_value = _as_float(row.get("r_result"))
    if r_value is None:
        r_value = _as_float(row.get("Result R"))

    background = "#f9fafb"
    if "skipped" in status:
        background = "#f3f4f6"
    elif "open" in status:
        background = "#fff7ed"
    elif "reversed" in status:
        background = "#dbeafe"
    elif status == "ambiguous":
        background = "#ffedd5"
    elif r_value is not None and r_value > 0:
        background = "#dcfce7"
    elif r_value is not None and r_value < 0:
        background = "#fee2e2"
    style = f"background-color: {background}; color: #111827;"
    return [style for _ in row]


def _select_cluster_rows(clusters: pd.DataFrame, mode: str) -> pd.DataFrame:
    mode = str(mode or "first").strip().lower()
    frame = clusters.copy()
    if mode == "worst" and "r_result" in frame.columns:
        return frame.sort_values("r_result", ascending=True, na_position="last")
    if mode == "best" and "r_result" in frame.columns:
        return frame.sort_values("r_result", ascending=False, na_position="last")
    if mode == "skipped" and "status" in frame.columns:
        return frame[frame["status"].astype(str).str.lower().eq("skipped")]
    if mode == "closed" and "status" in frame.columns:
        return frame[frame["status"].astype(str).str.lower().eq("closed")]
    if mode == "reversed" and "status" in frame.columns:
        return frame[frame["status"].astype(str).str.lower().eq("reversed")]
    if mode == "open" and "status" in frame.columns:
        return frame[frame["status"].astype(str).str.lower().eq("open_at_end")]
    return frame


def _cluster_note(row: pd.Series) -> str:
    status = str(row.get("status", "")).lower()
    skip_reason = _optional_str(row.get("skip_reason"))
    if status == "skipped":
        return f"Skipped: {skip_reason or 'n/a'}"
    if status == "open_at_end":
        return "Open at end; excluded from expectancy"
    if status == "ambiguous":
        return "Ambiguous TP/SL order"
    if status == "reversed":
        return "Closed by opposite signal"
    r_value = _as_float(row.get("r_result"))
    if r_value is None:
        return ""
    if r_value > 0:
        return "Profit cluster"
    if r_value < 0:
        return "Loss cluster"
    return "Flat / no R movement"


def _tp_hit_label(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or "-"
    if not isinstance(value, (list, tuple, set)) and pd.isna(value):
        return "-"
    try:
        values = list(value)
    except TypeError:
        return str(value)
    if not values:
        return "-"
    return ", ".join(f"TP{item}" for item in values)


def _exit_reason_label(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, str):
        return value or "-"
    if not isinstance(value, (list, tuple, set)) and pd.isna(value):
        return "-"
    try:
        values = list(value)
    except TypeError:
        return str(value)
    if not values:
        return "-"
    labels = {
        "tp": "TP",
        "sl": "SL",
        "end_of_data": "End",
        "ambiguous": "Ambiguous",
        "reverse_signal": "Reverse",
    }
    return ", ".join(labels.get(str(item), str(item)) for item in values)


def _time_label(value: Any) -> str:
    if value is None or pd.isna(value):
        return "-"
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        return "-"
    return ts.strftime("%Y-%m-%d %H:%M")


def _status_label(value: Any) -> str:
    raw = str(value or "").strip().lower()
    labels = {
        "closed": "CLOSED",
        "skipped": "SKIPPED",
        "open_at_end": "OPEN END",
        "ambiguous": "AMBIGUOUS",
        "accepted": "ACCEPTED",
        "reversed": "REVERSED",
    }
    return labels.get(raw, raw.upper() if raw else "-")


def _status_meaning(value: Any) -> str:
    raw = str(value or "").strip().lower()
    meanings = {
        "closed": "All legs closed before end of data.",
        "skipped": "Signal was not simulated, such as same-direction exposure or execution error.",
        "open_at_end": "Still open at end of available bars; excluded from expectancy.",
        "ambiguous": "A bar touched TP and SL; ambiguity policy affected the result.",
        "accepted": "Signal entered simulation.",
        "reversed": "Cluster was closed by an opposite-direction signal.",
    }
    return meanings.get(raw, "Status returned by the execution engine.")


def _metric_value(value: Any, value_type: str) -> str:
    if value_type == "pct":
        return _format_percent(_as_float(value))
    if value_type == "r":
        return _format_r(_as_float(value))
    if value_type == "int":
        number = _as_float(value)
        return "n/a" if number is None else f"{int(number):,}"
    if value_type == "num":
        number = _as_float(value)
        return "n/a" if number is None else f"{number:,.3f}"
    return _format_value(value)


def _format_percent(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value) * 100:.1f}%"


def _format_r(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value):+.3f}R"


def _result_tone(value: float | None) -> str:
    if value is None or pd.isna(value) or value == 0:
        return "neutral"
    return "positive" if value > 0 else "negative"


def _parse_signal_filename(name: str) -> dict[str, str | None]:
    match = SIGNAL_FILENAME_RE.match(name)
    if not match:
        return {"strategy": None, "symbol": None, "timeframe": None, "start_date": None, "end_date": None}
    data = match.groupdict()
    return {
        "strategy": data["strategy"],
        "symbol": data["symbol"].upper(),
        "timeframe": data["timeframe"].upper(),
        "start_date": _date_label(data["start"]),
        "end_date": _date_label(data["end"]),
    }


def _date_label(value: str) -> str:
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}"


def _optional_str(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    return str(value)


def _range_label(values: pd.Series | None) -> str:
    if values is None or len(values) == 0:
        return "n/a"
    parsed = pd.to_datetime(values, errors="coerce").dropna()
    if parsed.empty:
        return "n/a"
    return f"{parsed.min()} -> {parsed.max()}"


def _format_value(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        if abs(value) < 1:
            return f"{value:.4f}"
        return f"{value:,.3f}"
    return str(value)


def _as_float(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _ktp_label(metadata: dict[str, Any]) -> str:
    level = metadata.get("ktp_level")
    ktp = metadata.get("ktp")
    if level is None or ktp is None:
        return "n/a"
    return f"L{level} = {ktp}"


def _cost_label(spec: MarketSpec) -> str:
    return (
        f"{spec.commission_per_lot_per_side} / "
        f"{spec.spread_buffer_pips} / {spec.slippage_buffer_pips}"
    )
