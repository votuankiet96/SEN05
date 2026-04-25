"""Interactive chart dashboard for the Combo strategy.

This file is intentionally strategy-specific. It reuses the Combo scanner and
chart builder instead of redefining signal rules, so visual research stays
aligned with backtest/optimization logic.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

try:
    from flask import Flask, jsonify, render_template_string, request
except ImportError:  # pragma: no cover - import guard for operator-friendly CLI
    print("[ERROR] Flask is not installed. Install it with: pip install flask")
    sys.exit(1)


_ROOT = Path(__file__).resolve().parents[3]
_CORE = _ROOT / "core_python"
for _path in (_ROOT, _CORE):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from modules.chart_builder import build_reversal_chart
from modules.db_connector import test_connection
from strategies.combo.config import (  # noqa: E402
    SCANNER_DEFAULTS,
    STRATEGY,
    SYMBOLS,
    TIMEFRAME,
    get_indicator_params,
    get_symbol_params,
    summary as strategy_summary,
)
from strategies.combo.logic import scan_signals_reversal  # noqa: E402
from strategies.combo.scanner import calc_reversal_stats, prepare_data  # noqa: E402


PORT = 8513
HOST = "127.0.0.1"
TF_DIRECT = ["M5", "M15", "M30", "M45", "H1", "H2", "H3", "H4", "D1", "W"]
TF_COMPUTED = ["M10", "M20", "M90", "H6", "H8"]
MAX_SIGNAL_ROWS = 80

APP_DEFAULT_SYMBOL = next(iter(SYMBOLS))
APP_DEFAULT_BARS = 300

app = Flask(__name__)


INDEX_HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Combo Chart</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    *, *::before, *::after { box-sizing: border-box; }
    :root {
      --bg: #0d1117;
      --panel: #161b22;
      --panel-2: #1f2630;
      --border: #30363d;
      --text: #e6edf3;
      --muted: #8b949e;
      --blue: #2f81f7;
      --green: #00c875;
      --red: #ff5c5c;
      --orange: #ffb347;
      --sidebar: 292px;
    }
    html, body {
      height: 100%;
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 13px/1.4 Arial, Helvetica, sans-serif;
      overflow: hidden;
    }
    #app { height: 100vh; display: flex; flex-direction: column; }
    header {
      height: 44px;
      display: flex;
      align-items: center;
      gap: 14px;
      padding: 0 14px;
      background: var(--panel);
      border-bottom: 1px solid var(--border);
      flex-shrink: 0;
    }
    #title {
      font-weight: 700;
      letter-spacing: .2px;
      white-space: nowrap;
    }
    #meta {
      color: var(--muted);
      font-family: Consolas, "Courier New", monospace;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    #status {
      margin-left: auto;
      color: var(--muted);
      font-family: Consolas, "Courier New", monospace;
      white-space: nowrap;
    }
    main { min-height: 0; flex: 1; display: flex; }
    aside {
      width: var(--sidebar);
      flex: 0 0 var(--sidebar);
      background: var(--panel);
      border-right: 1px solid var(--border);
      overflow-y: auto;
      padding: 12px;
    }
    .section {
      margin-bottom: 14px;
      padding-bottom: 12px;
      border-bottom: 1px solid rgba(48, 54, 61, .65);
    }
    .section:last-child { border-bottom: 0; }
    .label {
      display: block;
      margin: 0 0 6px;
      color: var(--muted);
      font-size: 10px;
      font-weight: 700;
      letter-spacing: .9px;
      text-transform: uppercase;
    }
    select, input {
      width: 100%;
      height: 30px;
      border: 1px solid var(--border);
      border-radius: 4px;
      background: var(--panel-2);
      color: var(--text);
      padding: 0 8px;
      outline: none;
    }
    select:focus, input:focus { border-color: var(--blue); }
    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }
    .field { margin-bottom: 8px; }
    .field span {
      display: block;
      margin-bottom: 3px;
      color: var(--muted);
      font-size: 11px;
    }
    .checks {
      display: grid;
      gap: 7px;
    }
    .check {
      display: flex;
      align-items: center;
      gap: 8px;
      color: var(--text);
    }
    .check input {
      width: 15px;
      height: 15px;
      accent-color: var(--blue);
    }
    button {
      width: 100%;
      height: 30px;
      border: 1px solid var(--border);
      border-radius: 4px;
      color: var(--text);
      background: var(--panel-2);
      cursor: pointer;
      font-weight: 600;
    }
    button:hover { border-color: var(--blue); }
    #reset-btn { color: #fff; background: var(--blue); border-color: var(--blue); }
    #content {
      min-width: 0;
      flex: 1;
      display: grid;
      grid-template-rows: minmax(360px, 1fr) 176px;
      overflow: hidden;
    }
    #chart-wrap {
      min-height: 0;
      position: relative;
      background: var(--bg);
    }
    #chart {
      width: 100%;
      height: 100%;
    }
    #loading {
      position: absolute;
      inset: 0;
      display: none;
      align-items: center;
      justify-content: center;
      background: rgba(13, 17, 23, .72);
      color: var(--muted);
      z-index: 3;
    }
    #loading.show { display: flex; }
    #bottom {
      display: grid;
      grid-template-columns: 330px minmax(0, 1fr);
      border-top: 1px solid var(--border);
      min-height: 0;
    }
    #stats {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 6px;
      padding: 10px;
      background: var(--panel);
      border-right: 1px solid var(--border);
      overflow: auto;
    }
    .stat {
      min-width: 0;
      padding: 7px 8px;
      background: var(--panel-2);
      border: 1px solid var(--border);
      border-radius: 4px;
    }
    .stat b {
      display: block;
      color: var(--muted);
      font-size: 10px;
      text-transform: uppercase;
    }
    .stat span {
      font-family: Consolas, "Courier New", monospace;
      font-size: 16px;
    }
    #signals-wrap { min-width: 0; overflow: auto; background: var(--panel); }
    table {
      width: 100%;
      border-collapse: collapse;
      font-family: Consolas, "Courier New", monospace;
      font-size: 12px;
    }
    th, td {
      padding: 6px 8px;
      border-bottom: 1px solid rgba(48, 54, 61, .7);
      text-align: right;
      white-space: nowrap;
    }
    th {
      position: sticky;
      top: 0;
      background: var(--panel-2);
      color: var(--muted);
      z-index: 2;
    }
    th:first-child, td:first-child, th:nth-child(2), td:nth-child(2) {
      text-align: left;
    }
    .buy { color: var(--green); }
    .sell { color: var(--red); }
    .rev { color: var(--orange); }
    .muted { color: var(--muted); }
    .error {
      padding: 18px;
      color: var(--red);
      font-family: Consolas, "Courier New", monospace;
      white-space: pre-wrap;
    }
    @media (max-width: 920px) {
      main { flex-direction: column; }
      aside {
        width: 100%;
        flex: 0 0 auto;
        max-height: 42vh;
        border-right: 0;
        border-bottom: 1px solid var(--border);
      }
      #content { grid-template-rows: minmax(320px, 1fr) 210px; }
      #bottom { grid-template-columns: 1fr; }
      #stats { grid-template-columns: repeat(3, 1fr); border-right: 0; }
    }
  </style>
</head>
<body>
<div id="app">
  <header>
    <div id="title">Combo Chart</div>
    <div id="meta"></div>
    <div id="status">booting</div>
  </header>
  <main>
    <aside>
      <div class="section">
        <label class="label" for="symbol">Symbol</label>
        <select id="symbol"></select>
      </div>

      <div class="section">
        <div class="grid-2">
          <div class="field">
            <span>Timeframe</span>
            <select id="tf"></select>
          </div>
          <div class="field">
            <span>Bars</span>
            <input id="bars" type="number" min="50" max="5000" step="50">
          </div>
        </div>
      </div>

      <div class="section">
        <label class="label">Strategy</label>
        <div class="grid-3">
          <div class="field">
            <span>x</span>
            <input id="x" type="number" step="0.1">
          </div>
          <div class="field">
            <span>KTP</span>
            <input id="ktp" type="number" min="0.1" max="20" step="0.05">
          </div>
          <div class="field">
            <span>Min RR</span>
            <input id="min_rr" type="number" min="0" max="10" step="0.05">
          </div>
        </div>
        <div class="grid-2">
          <div class="field">
            <span>Entry bars</span>
            <input id="entry_line_bars" type="number" min="1" max="200" step="1">
          </div>
          <div class="field">
            <span>UTC session</span>
            <input id="session_hours" type="text" placeholder="">
          </div>
        </div>
      </div>

      <div class="section">
        <label class="label">Indicators</label>
        <div class="grid-2">
          <div class="field">
            <span>MA</span>
            <input id="ma_period" type="number" min="2" max="500" step="1">
          </div>
          <div class="field">
            <span>ATR</span>
            <input id="atr_period" type="number" min="2" max="200" step="1">
          </div>
        </div>
        <div class="grid-3">
          <div class="field">
            <span>MACD fast</span>
            <input id="macd_fast" type="number" min="1" max="200" step="1">
          </div>
          <div class="field">
            <span>MACD slow</span>
            <input id="macd_slow" type="number" min="2" max="300" step="1">
          </div>
          <div class="field">
            <span>Signal</span>
            <input id="macd_signal" type="number" min="1" max="200" step="1">
          </div>
        </div>
      </div>

      <div class="section">
        <label class="label">View</label>
        <div class="checks">
          <label class="check"><input id="show_rejected" type="checkbox">Show rejected</label>
          <label class="check"><input id="show_ma" type="checkbox">Show MA</label>
          <label class="check"><input id="show_entry_lines" type="checkbox">Show entry/SL/TP</label>
        </div>
      </div>

      <div class="section">
        <button id="reset-btn">Use config defaults</button>
      </div>
    </aside>

    <section id="content">
      <div id="chart-wrap">
        <div id="chart"></div>
        <div id="loading">scanning...</div>
      </div>
      <div id="bottom">
        <div id="stats"></div>
        <div id="signals-wrap">
          <table>
            <thead>
              <tr>
                <th>Time</th><th>Dir</th><th>RR</th><th>Entry</th><th>SL</th><th>TP</th><th>ATR</th><th>Outcome</th>
              </tr>
            </thead>
            <tbody id="signals"></tbody>
          </table>
        </div>
      </div>
    </section>
  </main>
</div>

<script>
const els = {};
let config = null;
let controller = null;
let timer = null;

const ids = [
  "symbol", "tf", "bars", "x", "ktp", "min_rr", "entry_line_bars",
  "session_hours", "ma_period", "atr_period", "macd_fast", "macd_slow",
  "macd_signal", "show_rejected", "show_ma", "show_entry_lines"
];
ids.forEach(id => els[id] = document.getElementById(id));
els.status = document.getElementById("status");
els.meta = document.getElementById("meta");
els.loading = document.getElementById("loading");
els.stats = document.getElementById("stats");
els.signals = document.getElementById("signals");
els.reset = document.getElementById("reset-btn");

function opt(value, label) {
  const node = document.createElement("option");
  node.value = value;
  node.textContent = label || value;
  return node;
}

function boolParam(id) {
  return els[id].checked ? "1" : "0";
}

function fieldParam(id) {
  return encodeURIComponent(els[id].value);
}

function formatSession(hours) {
  return (hours || []).join(",");
}

function symbolDefaults(sym) {
  return config.symbols.find(s => s.symbol === sym) || config.symbols[0];
}

function applyDefaults(sym) {
  const defaults = symbolDefaults(sym);
  const ind = config.indicator_defaults;
  els.x.value = defaults.x;
  els.ktp.value = defaults.ktp;
  els.min_rr.value = ind.MIN_RR;
  els.ma_period.value = defaults.ma_period || ind.MA_PERIOD;
  els.atr_period.value = ind.ATR_PERIOD;
  els.macd_fast.value = ind.MACD_FAST;
  els.macd_slow.value = ind.MACD_SLOW;
  els.macd_signal.value = ind.MACD_SIGNAL;
  els.entry_line_bars.value = config.scanner_defaults.entry_line_bars;
  els.session_hours.value = formatSession(defaults.session_hours_utc);
  els.show_rejected.checked = !!config.scanner_defaults.show_rejected;
  els.show_ma.checked = !!config.scanner_defaults.show_ma;
  els.show_entry_lines.checked = !!config.scanner_defaults.show_entry_lines;
}

function queryString() {
  const parts = [
    `symbol=${fieldParam("symbol")}`,
    `tf=${fieldParam("tf")}`,
    `bars=${fieldParam("bars")}`,
    `x=${fieldParam("x")}`,
    `ktp=${fieldParam("ktp")}`,
    `min_rr=${fieldParam("min_rr")}`,
    `entry_line_bars=${fieldParam("entry_line_bars")}`,
    `session_hours=${fieldParam("session_hours")}`,
    `ma_period=${fieldParam("ma_period")}`,
    `atr_period=${fieldParam("atr_period")}`,
    `macd_fast=${fieldParam("macd_fast")}`,
    `macd_slow=${fieldParam("macd_slow")}`,
    `macd_signal=${fieldParam("macd_signal")}`,
    `show_rejected=${boolParam("show_rejected")}`,
    `show_ma=${boolParam("show_ma")}`,
    `show_entry_lines=${boolParam("show_entry_lines")}`
  ];
  return parts.join("&");
}

function renderStats(stats) {
  const entries = [
    ["Pass", stats.n_pass],
    ["Rejected", stats.n_rejected],
    ["Win %", `${stats.win_pct}%`],
    ["Avg RR", stats.avg_rr],
    ["TP", stats.n_tp],
    ["SL", stats.n_sl],
    ["Open", stats.n_open],
    ["Reversed", stats.n_reversed]
  ];
  els.stats.innerHTML = entries.map(([k, v]) =>
    `<div class="stat"><b>${k}</b><span>${v}</span></div>`
  ).join("");
}

function renderSignals(rows) {
  if (!rows || rows.length === 0) {
    els.signals.innerHTML = `<tr><td colspan="8" class="muted">No signal in this window</td></tr>`;
    return;
  }
  els.signals.innerHTML = rows.slice().reverse().map(r => {
    const dirClass = r.is_reversal ? "rev" : (r.direction === "BUY" ? "buy" : "sell");
    const dirText = r.is_reversal ? `${r.direction} REV` : r.direction;
    return `<tr>
      <td>${r.bar_time || ""}</td>
      <td class="${dirClass}">${dirText || ""}</td>
      <td>${num(r.rr, 2)}</td>
      <td>${num(r.entry, 4)}</td>
      <td>${num(r.sl, 4)}</td>
      <td>${num(r.tp, 4)}</td>
      <td>${num(r.atr, 4)}</td>
      <td>${r.outcome || ""}</td>
    </tr>`;
  }).join("");
}

function num(value, digits) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "";
  return Number(value).toFixed(digits);
}

async function scan() {
  if (!config) return;
  if (controller) controller.abort();
  controller = new AbortController();
  els.loading.classList.add("show");
  els.status.textContent = "scanning";

  try {
    const response = await fetch(`/api/scan?${queryString()}`, { signal: controller.signal });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);

    await Plotly.react("chart", payload.figure.data, payload.figure.layout, {
      responsive: true,
      displaylogo: false,
      modeBarButtonsToRemove: ["lasso2d", "select2d"]
    });
    renderStats(payload.stats);
    renderSignals(payload.signals);
    els.meta.textContent = payload.meta;
    els.status.textContent = `${payload.stats.n_pass} pass / ${payload.stats.n_total} raw`;
  } catch (err) {
    if (err.name === "AbortError") return;
    document.getElementById("chart").innerHTML = `<div class="error">${err.message}</div>`;
    els.status.textContent = "error";
  } finally {
    els.loading.classList.remove("show");
  }
}

function scheduleScan() {
  window.clearTimeout(timer);
  timer = window.setTimeout(scan, 260);
}

async function boot() {
  const response = await fetch("/api/config");
  config = await response.json();

  config.symbols.forEach(s => {
    els.symbol.appendChild(opt(s.symbol, `${s.symbol} - ${s.label}`));
  });
  els.symbol.value = config.default_symbol;

  const tfGroups = [
    ["Direct", config.timeframes.direct],
    ["Computed", config.timeframes.computed]
  ];
  tfGroups.forEach(([label, values]) => {
    const group = document.createElement("optgroup");
    group.label = label;
    values.forEach(tf => group.appendChild(opt(tf)));
    els.tf.appendChild(group);
  });
  els.tf.value = config.default_tf;
  els.bars.value = config.default_bars;
  applyDefaults(els.symbol.value);

  ids.forEach(id => {
    const eventName = els[id].type === "checkbox" || els[id].tagName === "SELECT" ? "change" : "input";
    els[id].addEventListener(eventName, () => {
      if (id === "symbol") applyDefaults(els.symbol.value);
      scheduleScan();
    });
  });
  els.reset.addEventListener("click", () => {
    applyDefaults(els.symbol.value);
    scheduleScan();
  });
  window.addEventListener("resize", () => Plotly.Plots.resize("chart"));
  scan();
}

boot().catch(err => {
  document.getElementById("chart").innerHTML = `<div class="error">${err.message}</div>`;
  els.status.textContent = "boot error";
});
</script>
</body>
</html>
"""


def _to_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _to_int(value: str | None, default: int, min_value: int, max_value: int) -> int:
    try:
        parsed = int(float(value)) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(parsed, max_value))


def _to_float(value: str | None, default: float, min_value: float, max_value: float) -> float:
    try:
        parsed = float(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(parsed, max_value))


def _parse_session_hours(value: str | None, default: list[int]) -> list[int]:
    if value is None:
        return list(default)
    raw = str(value).strip()
    if raw == "":
        return []
    hours: set[int] = set()
    for part in raw.replace(";", ",").replace(" ", ",").split(","):
        if not part:
            continue
        hour = int(part)
        if hour < 0 or hour > 23:
            raise ValueError("session_hours must contain UTC hours in range 0..23")
        hours.add(hour)
    return sorted(hours)


def _symbol_defaults(symbol: str) -> dict[str, Any]:
    params = get_symbol_params(symbol)
    return {
        "symbol": symbol,
        "label": params["label"],
        "group": params.get("group", ""),
        "symbol_id": params["symbol_id"],
        "x": params["x"],
        "ktp": params["ktp"],
        "ma_period": params["ma_period"],
        "session_hours_utc": params.get("session_hours_utc", []),
        "spec_verified": params.get("spec_verified", False),
    }


def _runtime_request() -> tuple[str, str, int, dict[str, Any], dict[str, Any]]:
    symbol = request.args.get("symbol", APP_DEFAULT_SYMBOL).upper()
    if symbol not in SYMBOLS:
        raise ValueError(f"Unknown Combo symbol '{symbol}'. Available: {', '.join(SYMBOLS)}")

    defaults = _symbol_defaults(symbol)
    ind = get_indicator_params()
    scan_defaults = SCANNER_DEFAULTS

    tf = request.args.get("tf", TIMEFRAME).upper()
    if tf not in {*TF_DIRECT, *TF_COMPUTED}:
        raise ValueError(f"Unsupported timeframe '{tf}'")

    bars = _to_int(request.args.get("bars"), APP_DEFAULT_BARS, 50, 5000)
    x = _to_float(request.args.get("x"), float(defaults["x"]), 0.0, 1_000_000.0)

    session_hours = _parse_session_hours(
        request.args.get("session_hours"),
        list(defaults.get("session_hours_utc", [])),
    )

    params = {
        "MA_PERIOD": _to_int(
            request.args.get("ma_period"),
            int(defaults.get("ma_period") or ind["MA_PERIOD"]),
            2,
            500,
        ),
        "MACD_FAST": _to_int(request.args.get("macd_fast"), int(ind["MACD_FAST"]), 1, 200),
        "MACD_SLOW": _to_int(request.args.get("macd_slow"), int(ind["MACD_SLOW"]), 2, 300),
        "MACD_SIGNAL": _to_int(request.args.get("macd_signal"), int(ind["MACD_SIGNAL"]), 1, 200),
        "ATR_PERIOD": _to_int(request.args.get("atr_period"), int(ind["ATR_PERIOD"]), 2, 200),
        "KTP": _to_float(request.args.get("ktp"), float(defaults["ktp"]), 0.1, 20.0),
        "MIN_RR": _to_float(request.args.get("min_rr"), float(ind["MIN_RR"]), 0.0, 10.0),
        "ENTRY_LINE_BARS": _to_int(
            request.args.get("entry_line_bars"),
            int(scan_defaults["entry_line_bars"]),
            1,
            200,
        ),
        "SHOW_REJECTED": _to_bool(
            request.args.get("show_rejected"),
            bool(scan_defaults["show_rejected"]),
        ),
        "SHOW_MA": _to_bool(request.args.get("show_ma"), bool(scan_defaults["show_ma"])),
        "SHOW_ENTRY_LINES": _to_bool(
            request.args.get("show_entry_lines"),
            bool(scan_defaults["show_entry_lines"]),
        ),
    }

    cfg = {
        **SYMBOLS[symbol],
        "x": x,
        "session_hours_utc": session_hours,
    }
    return symbol, tf, bars, params, cfg


def _signals_for_json(signals: pd.DataFrame) -> list[dict[str, Any]]:
    if signals.empty:
        return []
    view = signals.tail(MAX_SIGNAL_ROWS).copy()
    if "bar_time" in view.columns:
        view["bar_time"] = pd.to_datetime(view["bar_time"]).dt.strftime("%Y-%m-%d %H:%M")
    return json.loads(view.to_json(orient="records"))


def _meta(symbol: str, tf: str, bars: int, params: dict[str, Any], cfg: dict[str, Any]) -> str:
    session = cfg.get("session_hours_utc") or "all"
    return (
        f"{symbol} {tf} | bars={bars} | x={cfg['x']} | "
        f"KTP={params['KTP']} | minRR={params['MIN_RR']} | "
        f"MA={params['MA_PERIOD']} | MACD={params['MACD_FAST']}/"
        f"{params['MACD_SLOW']}/{params['MACD_SIGNAL']} | "
        f"ATR={params['ATR_PERIOD']} | session={session}"
    )


@app.route("/")
def index():
    return render_template_string(INDEX_HTML)


@app.route("/api/config")
def api_config():
    return jsonify(
        {
            "strategy": {
                "name": STRATEGY["name"],
                "version": STRATEGY["version"],
                "summary": strategy_summary(),
            },
            "symbols": [_symbol_defaults(symbol) for symbol in SYMBOLS],
            "indicator_defaults": get_indicator_params(),
            "scanner_defaults": SCANNER_DEFAULTS,
            "timeframes": {"direct": TF_DIRECT, "computed": TF_COMPUTED},
            "default_symbol": APP_DEFAULT_SYMBOL,
            "default_tf": TIMEFRAME,
            "default_bars": APP_DEFAULT_BARS,
        }
    )


@app.route("/api/scan")
def api_scan():
    try:
        symbol, tf, bars, params, cfg = _runtime_request()
        df_scan = prepare_data(symbol, bars, params, tf)
        signals = scan_signals_reversal(df_scan, cfg, params)
        stats = calc_reversal_stats(signals)

        fig = build_reversal_chart(df_scan, signals, cfg, symbol, params)
        fig.update_layout(autosize=True, height=None)

        return jsonify(
            {
                "figure": json.loads(fig.to_json()),
                "stats": stats,
                "signals": _signals_for_json(signals),
                "meta": _meta(symbol, tf, bars, params, cfg),
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Combo strategy chart dashboard.")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--default-symbol", default=APP_DEFAULT_SYMBOL)
    parser.add_argument("--default-bars", type=int, default=APP_DEFAULT_BARS)
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def main() -> int:
    global APP_DEFAULT_BARS, APP_DEFAULT_SYMBOL

    args = _parse_args()
    default_symbol = args.default_symbol.upper()
    if default_symbol not in SYMBOLS:
        print(f"[WARN] Unknown default symbol '{default_symbol}', fallback to {APP_DEFAULT_SYMBOL}")
    else:
        APP_DEFAULT_SYMBOL = default_symbol
    APP_DEFAULT_BARS = max(50, min(int(args.default_bars), 5000))

    print("\n" + "=" * 60)
    print("  AUTO TRADING - COMBO CHART")
    print("  Backend  : Flask")
    print("  Frontend : Plotly")
    print(f"  Port     : {args.port}")
    print("=" * 60)

    print("\n[Checking SQL Server connection...]")
    if not test_connection():
        print("  Database : WARN - scan API will show the DB error until connection is fixed.")
    else:
        print("  Database : OK")
    print(f"\nReady: http://{args.host}:{args.port}\n")

    app.run(debug=args.debug, host=args.host, port=args.port, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
