"""Trang HTML của chart_simul: chọn strategy / symbol / timeframe và xem chart sống.

Mô tả:
    File lá: chỉ dựng chuỗi HTML tự chứa (thư viện Lightweight Charts nhúng sẵn,
    không cần internet). Không tính chỉ báo, không đọc Redis, không import file
    nào khác trong chart_simul. server.py phục vụ endpoint /api/view mà JS ở
    đây gọi.

    Giao diện (khung, màu, hai chart đồng bộ trục thời gian) sao chép từ trang
    dashboard của WE rồi sửa cho chart_simul -- cố ý KHÔNG đọc hay import file
    nào bên WE, vì wes và WE là hai hệ riêng. Phần nạp dữ liệu khác bản WE:
    mỗi nhịp chỉ hỏi server "có gì mới không" (một yêu cầu nhẹ), có thay đổi
    mới nhận lại khung nhìn mới và vẽ lại mà giữ nguyên vị trí đang xem.

    Quy ước trạng thái đối chiếu (xem verify.py): ok / pending / missed / extra /
    wrong_side / unchecked. "pending" chỉ là ĐANG CHỜ: trình duyệt tự đổi thành
    "LỠ" nếu sau `missAfterSeconds` giây (đếm từ lúc trang thấy lần đầu) wes vẫn
    chưa ghi tín hiệu.

render_page(...) -> str: một trang HTML tự chứa.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_VENDOR_JS_PATH = Path(__file__).resolve().parent / "vendor" / "lightweight-charts.js"

_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>wes chart_simul</title>
<style>
  html, body { margin: 0; padding: 0; min-height: 100%; }
  body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #e2e8f0; }
  #controls { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; padding: 10px 16px; border-bottom: 1px solid #1e293b; }
  #controls label { font-size: 13px; }
  #controls a { color: #93c5fd; font-size: 13px; text-decoration: none; margin-right: 6px; }
  #controls select, #controls input { background: #1e293b; color: #e2e8f0; border: 1px solid #334155; border-radius: 4px; padding: 4px 6px; font-size: 13px; }
  #controls input { width: 70px; }
  #controls button { background: #2563eb; color: #fff; border: none; border-radius: 4px; padding: 5px 12px; font-size: 13px; cursor: pointer; }
  .dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; background: #64748b; }
  .dot.ok { background: #22c55e; }
  .dot.warn { background: #eab308; }
  .dot.bad { background: #ef4444; }
  #statusbar { display: flex; flex-wrap: wrap; gap: 6px 18px; padding: 6px 16px; font-size: 12px; color: #94a3b8; border-bottom: 1px solid #1e293b; }
  #error { display: none; padding: 8px 16px; background: #7f1d1d; color: #fecaca; font-size: 13px; }
  #price-wrap { position: relative; }
  #legend { position: absolute; top: 6px; left: 10px; z-index: 2; pointer-events: none; font-size: 12px; color: #cbd5e1; }
  #price-chart { width: 100%; height: 460px; }
  #macd-panel { width: 100%; height: 150px; border-top: 1px solid #1e293b; }
  #summary { padding: 8px 16px; font-size: 13px; color: #cbd5e1; border-top: 1px solid #1e293b; }
  #summary b { font-weight: 600; }
  #signal-detail { display: none; margin: 0 16px 8px; padding: 7px 10px; font-size: 12px; line-height: 1.6; background: #111c33; border-left: 3px solid #64748b; border-radius: 4px; overflow-wrap: anywhere; }
  #signal-detail[data-side="BUY"] { border-left-color: #22c55e; }
  #signal-detail[data-side="SELL"] { border-left-color: #ef4444; }
  #signal-detail .head { font-weight: 600; }
  #signal-detail .warn { color: #fb923c; font-weight: 600; }
  #signals-wrap { padding: 0 16px 24px; overflow-x: auto; }
  #signals { border-collapse: collapse; width: 100%; font-size: 12px; }
  #signals th { text-align: left; color: #94a3b8; font-weight: 500; padding: 5px 8px; border-bottom: 1px solid #334155; position: sticky; top: 0; background: #0f172a; }
  #signals td { padding: 4px 8px; border-bottom: 1px solid #1e293b; vertical-align: top; }
  #signals tr.st-missed td { background: rgba(217, 70, 239, 0.12); }
  #signals tr.st-pending td { background: rgba(234, 179, 8, 0.10); }
  #signals tr.st-extra td, #signals tr.st-wrong_side td { background: rgba(251, 146, 60, 0.12); }
  .side-BUY { color: #22c55e; font-weight: 600; }
  .side-SELL { color: #ef4444; font-weight: 600; }
  .stt-ok { color: #22c55e; }
  .stt-unchecked { color: #94a3b8; }
  .stt-pending { color: #eab308; }
  .stt-missed { color: #d946ef; font-weight: 600; }
  .stt-extra, .stt-wrong_side { color: #fb923c; font-weight: 600; }
</style>
</head>
<body>
<div id="controls">
  <a href="/mtf">Multi-timeframe</a>
  <label>Strategy <select id="strategy"></select></label>
  <label>Symbol <select id="symbol"></select></label>
  <label>TF <select id="tf"></select></label>
  <label>Bars <input id="bars" type="number" step="50"></label>
  <label>Auto
    <select id="auto">
      <option value="500">0.5s</option>
      <option value="1000" selected>1s</option>
      <option value="2000">2s</option>
      <option value="5000">5s</option>
      <option value="0">Off</option>
    </select>
  </label>
  <button id="refresh">Refresh</button>
  <span id="live-dot" class="dot"></span>
</div>
<div id="statusbar">
  <span id="clock"></span>
  <span id="pair"></span>
  <span id="updated"></span>
</div>
<div id="error"></div>
<div id="price-wrap">
  <div id="legend"></div>
  <div id="price-chart"></div>
</div>
<div id="macd-panel"></div>
<div id="summary"></div>
<div id="signal-detail"></div>
<div id="signals-wrap">
  <table id="signals">
    <thead>
      <tr><th>Bar open (UTC)</th><th>Bar close (UTC)</th><th>Side</th><th>Order</th><th>Entry / Stop</th><th>ATR</th><th>Status</th><th>Reason</th></tr>
    </thead>
    <tbody></tbody>
  </table>
</div>
<script>
__VENDOR_JS__
</script>
<script>
(function () {
  "use strict";
  const CONFIG = __CONFIG_JSON__;
  const PRICE_HEIGHT = 460;
  const MACD_HEIGHT = 150;
  const COLORS = { buy: "#16a34a", sell: "#dc2626", pending: "#eab308", missed: "#d946ef", extra: "#fb923c" };
  const STATUS_TEXT = {
    ok: "OK", unchecked: "published (not checked)", pending: "waiting for wes…",
    missed: "MISSED", extra: "EXTRA", wrong_side: "WRONG SIDE",
  };
  const $ = (id) => document.getElementById(id);
  const el = {
    strategy: $("strategy"), symbol: $("symbol"), tf: $("tf"), bars: $("bars"), auto: $("auto"),
    refresh: $("refresh"), dot: $("live-dot"), clock: $("clock"), pair: $("pair"), updated: $("updated"),
    error: $("error"), legend: $("legend"), summary: $("summary"), detail: $("signal-detail"),
    tbody: document.querySelector("#signals tbody"),
  };

  let priceChart = null;
  let macdChart = null;
  let candleSeries = null;
  let macdSeries = null;
  const overlaySeries = new Map();
  let view = null;
  let viewKey = "";
  let token = null;
  let timer = null;
  let loading = false;
  let requestId = 0;
  let lastCandleTime = null;
  const pendingSince = new Map();
  let candlesByTime = new Map();
  let overlayValues = new Map();
  let macdByTime = new Map();
  let rowsByTime = new Map();

  // ---------------------------------------------------------------- tiện ích
  function showError(message) { el.error.textContent = message; el.error.style.display = "block"; }
  function clearError() { el.error.style.display = "none"; el.error.textContent = ""; }
  function setDot(kind) { el.dot.className = "dot " + kind; }

  function fmtNumber(value) {
    if (value === undefined || value === null || !Number.isFinite(Number(value))) return "-";
    return Number(value).toLocaleString("en-US", { maximumFractionDigits: 5 });
  }

  function fmtTime(unix) { return new Date(unix * 1000).toISOString().slice(0, 16).replace("T", " "); }

  function clampBars(value) {
    const n = Number.isFinite(value) ? Math.round(value) : CONFIG.defaultBars;
    return Math.min(CONFIG.barsMax, Math.max(CONFIG.barsMin, n));
  }

  // ----------------------------------------------------------- bộ chọn (controls)
  function setOptions(select, items, selected) {
    select.replaceChildren(...items.map(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      return option;
    }));
    select.value = selected;
  }

  function populateTimeframes(selected) {
    const info = CONFIG.strategies[el.strategy.value];
    const chosen = info.timeframes.includes(selected) ? selected : info.defaultTimeframe;
    setOptions(el.tf, info.timeframes.map((tf) => [tf, tf]), chosen);
  }

  function populateControls() {
    const query = new URLSearchParams(window.location.search);
    const strategy = CONFIG.strategies[query.get("strategy")] ? query.get("strategy") : CONFIG.defaultStrategy;
    const symbol = CONFIG.symbols.includes(query.get("symbol")) ? query.get("symbol") : CONFIG.defaultSymbol;
    setOptions(el.strategy, Object.entries(CONFIG.strategies).map(([key, info]) => [key, info.label]), strategy);
    setOptions(el.symbol, CONFIG.symbols.map((s) => [s, s]), symbol);
    populateTimeframes(query.get("tf"));
    el.bars.value = String(clampBars(parseInt(query.get("bars") || "", 10)));
  }

  function updateUrl() {
    const query = new URLSearchParams({
      strategy: el.strategy.value, symbol: el.symbol.value, tf: el.tf.value, bars: el.bars.value,
    });
    window.history.replaceState(null, "", "?" + query.toString());
  }

  // ------------------------------------------------------------------- chart
  function chartOptions(height) {
    return {
      height: height,
      layout: { background: { color: "#0f172a" }, textColor: "#e2e8f0" },
      grid: { vertLines: { color: "#1e293b" }, horzLines: { color: "#1e293b" } },
      timeScale: { timeVisible: true, secondsVisible: false, rightOffset: 6 },
    };
  }

  // Đồng bộ zoom/pan + crosshair giữa hai chart (giá và MACD). Mọi series đều có
  // đúng số điểm như nến nên chỉ số logic của hai chart luôn khớp nhau.
  function syncCharts() {
    const panes = [{ chart: priceChart, series: candleSeries }, { chart: macdChart, series: macdSeries }];
    let syncing = false;
    panes.forEach((pane, i) => {
      const other = panes[1 - i];
      pane.chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (!range || syncing || !view || !view.candles.length) return;
        syncing = true;
        try { other.chart.timeScale().setVisibleLogicalRange(range); } finally { syncing = false; }
      });
      pane.chart.subscribeCrosshairMove((param) => {
        if (!view || !view.candles.length) return;
        if (!param.time || !param.seriesData.size) { other.chart.clearCrosshairPosition(); return; }
        const point = param.seriesData.get(pane.series);
        const value = point && (point.value !== undefined ? point.value : point.close);
        if (value !== undefined) other.chart.setCrosshairPosition(value, param.time, other.series);
        else other.chart.clearCrosshairPosition();
      });
    });
  }

  function resizeCharts() {
    priceChart.resize($("price-chart").clientWidth, PRICE_HEIGHT);
    macdChart.resize($("macd-panel").clientWidth, MACD_HEIGHT);
  }

  function ensureCharts() {
    priceChart = LightweightCharts.createChart($("price-chart"), chartOptions(PRICE_HEIGHT));
    candleSeries = priceChart.addCandlestickSeries({
      upColor: "#16a34a", downColor: "#dc2626", borderVisible: false,
      wickUpColor: "#16a34a", wickDownColor: "#dc2626",
    });
    macdChart = LightweightCharts.createChart($("macd-panel"), chartOptions(MACD_HEIGHT));
    macdSeries = macdChart.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });
    syncCharts();
    priceChart.subscribeClick(showDetail);
    priceChart.subscribeCrosshairMove((param) => showLegend(param.time === undefined ? lastCandleTime : param.time));
    window.addEventListener("resize", resizeCharts);
  }

  function syncOverlays(list) {
    const wanted = new Set(list.map((o) => o.key));
    Array.from(overlaySeries.keys()).forEach((key) => {
      if (!wanted.has(key)) { priceChart.removeSeries(overlaySeries.get(key)); overlaySeries.delete(key); }
    });
    list.forEach((o) => {
      let series = overlaySeries.get(o.key);
      if (!series) {
        series = priceChart.addLineSeries({
          color: o.color, lineWidth: 2, title: o.label, priceLineVisible: false, lastValueVisible: false,
        });
        overlaySeries.set(o.key, series);
      }
      series.applyOptions({ color: o.color, title: o.label });
      series.setData(o.data);
    });
  }

  // ------------------------------------------------------------ trạng thái dấu
  function effectiveStatus(item) {
    if (item.status !== "pending") return item.status;
    const since = pendingSince.get(item.time);
    const late = since !== undefined && Date.now() - since >= CONFIG.missAfterSeconds * 1000;
    return late ? "missed" : "pending";
  }

  function trackPending(marks) {
    const now = Date.now();
    const current = new Set();
    marks.forEach((m) => {
      if (m.status !== "pending") return;
      current.add(m.time);
      if (!pendingSince.has(m.time)) pendingSince.set(m.time, now);
    });
    Array.from(pendingSince.keys()).forEach((time) => { if (!current.has(time)) pendingSince.delete(time); });
  }

  function markerFor(item) {
    const buy = item.side === "BUY";
    const status = effectiveStatus(item);
    const position = buy ? "belowBar" : "aboveBar";
    if (status === "pending") return { time: item.time, position, color: COLORS.pending, shape: "circle", text: "wait " + item.side };
    if (status === "missed") return { time: item.time, position, color: COLORS.missed, shape: "square", text: "MISSED " + item.side };
    if (status === "extra") return { time: item.time, position, color: COLORS.extra, shape: "circle", text: "EXTRA " + item.side };
    if (status === "wrong_side") return { time: item.time, position, color: COLORS.extra, shape: "square", text: "WRONG SIDE" };
    return {
      time: item.time, position, color: buy ? COLORS.buy : COLORS.sell,
      shape: buy ? "arrowUp" : "arrowDown", text: item.side,
    };
  }

  // ------------------------------------------------------- legend / chi tiết
  function rebuildIndexes() {
    candlesByTime = new Map(view.candles.map((c) => [String(c.time), c]));
    overlayValues = new Map(view.overlays.map((o) => [
      o.key, new Map(o.data.filter((p) => p.value !== undefined).map((p) => [String(p.time), p.value])),
    ]));
    const macd = view.panels.find((p) => p.key === "macd");
    macdByTime = new Map(macd ? macd.data.filter((p) => p.value !== undefined).map((p) => [String(p.time), p.value]) : []);
    rowsByTime = new Map(view.signals.map((r) => [String(r.time), r]));
    lastCandleTime = view.candles.length ? view.candles[view.candles.length - 1].time : null;
  }

  function showLegend(time) {
    const candle = time === null || time === undefined ? null : candlesByTime.get(String(time));
    if (!candle) { el.legend.textContent = ""; return; }
    const parts = [
      fmtTime(candle.time) + " UTC", "O " + fmtNumber(candle.open), "H " + fmtNumber(candle.high),
      "L " + fmtNumber(candle.low), "C " + fmtNumber(candle.close),
    ];
    view.overlays.forEach((o) => {
      const value = overlayValues.get(o.key).get(String(time));
      if (value !== undefined) parts.push(o.label + " " + fmtNumber(value));
    });
    const macd = macdByTime.get(String(time));
    if (macd !== undefined) parts.push("MACD " + fmtNumber(macd));
    el.legend.textContent = parts.join("  ·  ");
  }

  function hideDetail() {
    el.detail.style.display = "none";
    el.detail.replaceChildren();
    delete el.detail.dataset.side;
  }

  function detailLine(text, className) {
    const line = document.createElement("div");
    line.textContent = text;
    if (className) line.className = className;
    return line;
  }

  function showDetail(param) {
    const row = param.time === undefined ? undefined : rowsByTime.get(String(param.time));
    if (!row) { hideDetail(); return; }
    const status = effectiveStatus(row);
    const lines = [detailLine(
      row.side + " · " + STATUS_TEXT[status] + " · bar open " + row.bar + " UTC · close " + row.close + " UTC", "head")];
    const order = (row.orderType || "-") + (row.orderType === "STOP" ? " · stop " + fmtNumber(row.stop) : "");
    lines.push(detailLine("Order " + order + " · ATR " + fmtNumber(row.atr)));
    if (row.wes && row.chart) {
      lines.push(detailLine(
        "wes wrote: stop " + fmtNumber(row.wes.stop) + " · ATR " + fmtNumber(row.wes.atr) +
        "   |   chart computed: stop " + fmtNumber(row.chart.stop) + " · ATR " + fmtNumber(row.chart.atr)));
    }
    if (row.diff.length) lines.push(detailLine("VALUE MISMATCH: " + row.diff.join(", "), "warn"));
    if (row.inputs.length) {
      lines.push(detailLine("Rule inputs: " + row.inputs.map((i) => i.label + " " + fmtNumber(i.value)).join(" · ")));
    }
    if (row.reason) lines.push(detailLine("Reason: " + row.reason));
    el.detail.replaceChildren(...lines);
    el.detail.dataset.side = row.side;
    el.detail.style.display = "block";
  }

  // ------------------------------------------------------- bảng + tổng kết
  function cell(text, className) {
    const td = document.createElement("td");
    td.textContent = text;
    if (className) td.className = className;
    return td;
  }

  function rowElement(row) {
    const status = effectiveStatus(row);
    const tr = document.createElement("tr");
    tr.className = "st-" + status;
    const entry = row.orderType === "STOP" ? fmtNumber(row.stop) : (row.orderType ? "MARKET" : "-");
    let statusText = STATUS_TEXT[status];
    if (row.diff.length) statusText += " · mismatch: " + row.diff.join(",");
    [
      cell(row.bar), cell(row.close), cell(row.side, "side-" + row.side), cell(row.orderType || "-"),
      cell(entry), cell(fmtNumber(row.atr)), cell(statusText, "stt-" + status), cell(row.reason || ""),
    ].forEach((td) => tr.appendChild(td));
    return tr;
  }

  function renderTable() { el.tbody.replaceChildren(...view.signals.map(rowElement)); }

  function renderSummary() {
    const counts = { ok: 0, pending: 0, missed: 0, extra: 0, wrong_side: 0, unchecked: 0 };
    view.marks.forEach((m) => { counts[effectiveStatus(m)] += 1; });
    const meta = view.meta;
    const region = meta.checkFrom ? "Checked from " + meta.checkFrom + " UTC: " : "No wes signal yet, nothing checked. ";
    el.summary.textContent =
      region + "OK " + counts.ok + " · waiting " + counts.pending + " · MISSED " + counts.missed +
      " · EXTRA " + counts.extra + " · wrong side " + counts.wrong_side +
      " · unchecked " + counts.unchecked + " · value mismatch " + view.stats.fieldDiff;
  }

  // ----------------------------------------------------------- thanh trạng thái
  function renderClock(meta) {
    const clock = meta.clock || {};
    const tick = clock.tick === null || clock.tick === undefined ? "?" : clock.tick;
    el.clock.textContent = "DFS " + (clock.status || "?") + " · tick " + tick +
      " · sim time " + (clock.simTime || "?") + " UTC" + (clock.runId ? " · run " + clock.runId : "");
    setDot(clock.status === "running" ? "ok" : "warn");
    el.updated.textContent = "updated " + new Date().toLocaleTimeString("en-GB") + " (local)";
  }

  function renderPair(meta) {
    if (!meta.lastCandle) { el.pair.textContent = meta.message || "No candles for this pair in Redis yet."; return; }
    el.pair.textContent = meta.strategyLabel + " " + meta.symbol + " " + meta.tf + " · " + meta.paramsLabel +
      " · last bar " + meta.lastCandle + " · last signal " + meta.lastSignal;
  }

  // ------------------------------------------------------------ nạp & vẽ dữ liệu
  function hasPending() { return view.marks.some((m) => m.status === "pending"); }

  function applyView(payload, key) {
    const reset = key !== viewKey;
    const timeScale = priceChart.timeScale();
    const range = reset ? null : timeScale.getVisibleLogicalRange();
    const previousLast = view ? view.candles.length - 1 : -1;
    const atRight = reset || !range || range.to >= previousLast - 1;
    const previousTimes = reset || atRight ? null : timeScale.getVisibleRange();
    if (reset) { pendingSince.clear(); hideDetail(); }
    view = payload;
    viewKey = key;
    candleSeries.setData(payload.candles);
    syncOverlays(payload.overlays);
    const macd = payload.panels.find((p) => p.key === "macd");
    macdSeries.setData(macd ? macd.data : []);
    rebuildIndexes();
    trackPending(payload.marks);
    candleSeries.setMarkers(payload.marks.map(markerFor));
    renderTable();
    renderSummary();
    renderPair(payload.meta);
    token = {
      head: payload.meta.head || "", nsig: payload.meta.signalCount, pending: hasPending() ? 1 : 0,
      run: (payload.meta.clock || {}).runId || "",
    };
    if (reset) timeScale.fitContent();
    else if (atRight) timeScale.scrollToRealTime();
    else if (previousTimes) { try { timeScale.setVisibleRange(previousTimes); } catch (err) { /* khoảng cũ không còn */ } }
    showLegend(lastCandleTime);
  }

  async function load(force) {
    const id = ++requestId;
    loading = true;
    try {
      const key = [el.strategy.value, el.symbol.value, el.tf.value, el.bars.value].join("|");
      const params = new URLSearchParams({
        strategy: el.strategy.value, symbol: el.symbol.value, tf: el.tf.value, bars: el.bars.value,
      });
      if (!force && view && token && key === viewKey) {
        params.set("head", token.head);
        params.set("nsig", String(token.nsig));
        params.set("pending", String(token.pending));
        params.set("run", token.run);
      }
      const response = await fetch("/api/view?" + params.toString(), { cache: "no-store" });
      const payload = await response.json();
      if (id !== requestId) return;
      if (!response.ok) { showError(payload.error || ("HTTP " + response.status)); setDot("bad"); return; }
      clearError();
      renderClock(payload.meta);
      if (payload.changed === false) return;
      applyView(payload, key);
    } catch (err) {
      if (id === requestId) { showError("Lost connection to the chart_simul server: " + err); setDot("bad"); }
    } finally {
      if (id === requestId) loading = false;
    }
  }

  function reload() { updateUrl(); load(true); }

  function setAuto() {
    if (timer) { clearInterval(timer); timer = null; }
    const ms = parseInt(el.auto.value, 10);
    if (ms > 0) timer = setInterval(() => { if (!loading) load(false); }, ms);
  }

  // ------------------------------------------------------------------- khởi động
  window.addEventListener("error", (event) => showError("JS error: " + event.message));
  el.strategy.addEventListener("change", () => { populateTimeframes(el.tf.value); reload(); });
  el.symbol.addEventListener("change", reload);
  el.tf.addEventListener("change", reload);
  el.bars.addEventListener("change", () => { el.bars.value = String(clampBars(parseInt(el.bars.value, 10))); reload(); });
  el.refresh.addEventListener("click", reload);
  el.auto.addEventListener("change", setAuto);

  populateControls();
  ensureCharts();
  reload();
  setAuto();
})();
</script>
</body>
</html>
"""


def _read_vendor_js() -> str:
    """Nội dung thư viện Lightweight Charts đã vendor sẵn trong chart_simul."""
    return _VENDOR_JS_PATH.read_text(encoding="utf-8")


def render_page(
    *,
    strategies: dict[str, dict[str, Any]],
    symbols: list[str],
    default_strategy: str,
    default_symbol: str,
    default_bars: int,
    bars_min: int,
    bars_max: int,
    miss_after_seconds: int,
) -> str:
    """Dựng trang chart_simul -- HTML tự chứa, JS bên trong tự gọi /api/view.

    `strategies` (key -> label / timeframes / defaultTimeframe) và `symbols` lấy
    từ cấu hình wes; các giá trị còn lại nhúng làm `CONFIG` cho JS phía client
    (giới hạn ô Bars và ngưỡng chuyển "chờ" thành "LỠ" cùng nguồn với server).
    """
    config = {
        "strategies": strategies,
        "symbols": symbols,
        "defaultStrategy": default_strategy,
        "defaultSymbol": default_symbol,
        "defaultBars": default_bars,
        "barsMin": bars_min,
        "barsMax": bars_max,
        "missAfterSeconds": miss_after_seconds,
    }
    html = _TEMPLATE.replace("__VENDOR_JS__", _read_vendor_js())
    return html.replace("__CONFIG_JSON__", json.dumps(config))
