"""
Trang HTML dashboard sống: chọn strategy/symbol/timeframe/bars trên trình
duyệt, JS tự fetch /api/scan mỗi lần đổi lựa chọn hoặc theo auto-refresh.

Giống renderer.py nhưng cho trang sống thay vì snapshot tĩnh — không tính
chỉ báo/tín hiệu, không đọc DataFrame, không gọi SQL, không import
payload.py/cli.py/server.py (lá trong cây phụ thuộc). Đọc
vendor/lightweight-charts.js độc lập với renderer.py (trùng lặp có chủ đích
~3 dòng để 2 file lá không phụ thuộc lẫn nhau). server.py là nơi duy nhất
phục vụ endpoint /api/scan mà JS ở đây gọi tới.

render_dashboard_html(...) -> str: 1 trang HTML tự chứa.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_VENDOR_JS_PATH = Path(__file__).resolve().parent / "vendor" / "lightweight-charts.js"

_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>core_python dashboard</title>
<style>
  html, body { margin: 0; padding: 0; height: 100%; }
  body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #e2e8f0; }
  #controls { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; padding: 10px 16px; border-bottom: 1px solid #1e293b; }
  #controls select, #controls input { background: #1e293b; color: #e2e8f0; border: 1px solid #334155; border-radius: 4px; padding: 4px 6px; font-size: 13px; }
  #from-time, #to-time { width: 145px; }
  #controls button { background: #2563eb; color: #fff; border: none; border-radius: 4px; padding: 5px 12px; font-size: 13px; cursor: pointer; }
  #controls button#export { background: #16a34a; }
  #stats { font-size: 13px; color: #94a3b8; margin-left: auto; }
  #params { display: flex; flex-wrap: wrap; gap: 10px; padding: 6px 16px; border-bottom: 1px solid #1e293b; }
  #params label { display: flex; flex-direction: column; gap: 2px; font-size: 11px; color: #94a3b8; }
  #params input { background: #1e293b; color: #e2e8f0; border: 1px solid #334155; border-radius: 4px; padding: 3px 5px; font-size: 12px; width: 90px; }
  #error { display: none; padding: 8px 16px; background: #7f1d1d; color: #fecaca; font-size: 13px; }
  #trend-wrap { display: none; border-bottom: 1px solid #1e293b; }
  #trend-header { padding: 7px 16px 0; font-size: 12px; color: #94a3b8; }
  #trend-chart { width: 100%; height: 240px; }
  #price-wrap { position: relative; }
  #price-chart { width: 100%; height: 360px; }
  #signal-detail {
    display: none; position: absolute; top: 8px; left: 12px; right: 70px; z-index: 2; pointer-events: none;
    font-size: 12px; line-height: 1.55; color: #e2e8f0; background: rgba(15, 23, 42, 0.9);
    border-left: 3px solid #64748b; padding: 6px 9px; border-radius: 4px; overflow-wrap: anywhere;
  }
  #signal-detail[data-side="BUY"] { border-left-color: #22c55e; }
  #signal-detail[data-side="SELL"] { border-left-color: #ef4444; }
  #macd-panel { width: 100%; height: 140px; border-top: 1px solid #1e293b; }
  @media (max-width: 700px) {
    #signal-detail { right: 12px; font-size: 11px; }
  }
</style>
</head>
<body>
<div id="controls">
  <label>Strategy <select id="strategy"></select></label>
  <label>Symbol <select id="symbol"></select></label>
  <label>TF <select id="tf"></select></label>
  <label>Bars <input id="bars" type="number" step="50" style="width:70px"></label>
  <label>From <input id="from-time" type="text" placeholder="2026-01-01 00:00"></label>
  <label>To <input id="to-time" type="text" placeholder="2026-01-02 00:00"></label>
  <label>Trend
    <select id="trend-mode">
      <option value="no_trend">No Trend</option>
      <option value="trend_filter">Trend Filter</option>
    </select>
  </label>
  <label id="trend-tf-control">Trend TF <select id="trend-tf"></select></label>
  <button id="refresh">Refresh</button>
  <button id="export">Export CSV</button>
  <label>Auto
    <select id="auto">
      <option value="0">Off</option>
      <option value="10000">10s</option>
      <option value="30000">30s</option>
      <option value="60000">60s</option>
    </select>
  </label>
  <span id="stats"></span>
</div>
<div id="params"></div>
<div id="error"></div>
<div id="trend-wrap">
  <div id="trend-header"><span id="trend-title"></span></div>
  <div id="trend-chart"></div>
</div>
<div id="price-wrap">
  <div id="price-chart"></div>
  <div id="signal-detail"></div>
</div>
<div id="macd-panel"></div>
<script>
__VENDOR_JS__
</script>
<script>
(function () {
  const CONFIG = __CONFIG_JSON__;
  const HIDDEN_PARAM_KEYS = new Set(["TREND_FILTER_ENABLED", "TREND_TYPE", "TREND_TF", "ENTRY_TF"]);
  const el = {
    strategy: document.getElementById("strategy"),
    symbol: document.getElementById("symbol"),
    tf: document.getElementById("tf"),
    bars: document.getElementById("bars"),
    fromTime: document.getElementById("from-time"),
    toTime: document.getElementById("to-time"),
    trendMode: document.getElementById("trend-mode"),
    trendTf: document.getElementById("trend-tf"),
    trendTfControl: document.getElementById("trend-tf-control"),
    refresh: document.getElementById("refresh"),
    export: document.getElementById("export"),
    auto: document.getElementById("auto"),
    stats: document.getElementById("stats"),
    params: document.getElementById("params"),
    error: document.getElementById("error"),
    trendWrap: document.getElementById("trend-wrap"),
    trendTitle: document.getElementById("trend-title"),
    signalDetail: document.getElementById("signal-detail"),
  };

  let priceChart, macdChart, trendChart, candleSeries, macdSeries, trendCandleSeries;
  let overlaySeries = [];
  let trendOverlaySeries = [];
  let entryBaseMarkers = [];
  let macdActive = false;
  let autoTimer = null;
  let chartData = null;
  let loadingHistory = false;
  let scanVersion = 0;
  let syncingTimeRange = false;
  let timeSyncPaused = false;
  let timeSyncResumeTimer = null;
  let trendRangeStartTime = null;
  let trendBaseMarkers = [];
  let entryRangeMarkers = [];
  let candlesByTime = new Map();
  let signalsByTime = new Map();

  function addOption(select, value, label) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    select.appendChild(opt);
  }
  // Chỉ hiện đúng nhóm TF strategy quy ước: supportedTimeframes (giới hạn
  // cứng) nếu có, không thì recommendedTimeframes (quy ước riêng, vd Combo
  // không giới hạn cứng nhưng vẫn có nhóm TF khuyến nghị) — không rơi về
  // toàn bộ TF hệ thống trừ khi strategy không khai gì cả.
  function populateTimeframes() {
    const spec = CONFIG.strategies[el.strategy.value];
    const allowed = spec.supportedTimeframes.length
      ? spec.supportedTimeframes
      : spec.recommendedTimeframes.length
      ? spec.recommendedTimeframes
      : CONFIG.timeframes;
    el.tf.innerHTML = "";
    allowed.forEach((tf) => addOption(el.tf, tf, tf));
    el.tf.value = spec.defaultTimeframe || allowed[0];
  }

  function populateTrendControls() {
    const spec = CONFIG.strategies[el.strategy.value];
    const allowed = spec.trendTimeframes.length ? spec.trendTimeframes : CONFIG.timeframes;
    const keep = allowed.includes(el.trendTf.value) ? el.trendTf.value : null;
    el.trendTf.innerHTML = "";
    allowed.forEach((tf) => addOption(el.trendTf, tf, tf));
    el.trendTf.value = keep || spec.defaultTrendTf || allowed[0] || "";
    updateTrendControls();
  }

  function updateTrendControls() {
    const enabled = el.trendMode.value === "trend_filter";
    el.trendTf.disabled = !enabled;
    el.trendTfControl.style.display = enabled ? "" : "none";
  }

  // Chỉ hiện đúng nhóm symbol strategy đã tune buffer X riêng (tunedSymbols)
  // — không hiện cả 37 symbol hệ thống nếu strategy chỉ tune cho 1 nhóm nhỏ.
  function populateSymbols() {
    const spec = CONFIG.strategies[el.strategy.value];
    const allowed = spec.tunedSymbols.length ? spec.tunedSymbols : CONFIG.symbols;
    const keep = allowed.includes(el.symbol.value) ? el.symbol.value : null;
    el.symbol.innerHTML = "";
    allowed.forEach((s) => addOption(el.symbol, s, s));
    el.symbol.value = keep || (allowed.includes(CONFIG.defaultSymbol) ? CONFIG.defaultSymbol : allowed[0]);
  }

  function populateControls() {
    Object.keys(CONFIG.strategies).forEach((key) => addOption(el.strategy, key, CONFIG.strategies[key].label));
    el.strategy.value = CONFIG.defaultStrategy;
    el.bars.min = CONFIG.barsMin;
    el.bars.max = CONFIG.barsMax;
    el.bars.value = CONFIG.defaultBars;
    populateSymbols();
    populateTimeframes();
    populateTrendControls();
    renderParamFields();
  }

  function fmtDefault(value) {
    if (value === null || value === undefined) return "auto";
    if (Array.isArray(value)) return value.join(",");
    return String(value);
  }

  // Dựng field nhập tham số riêng chiến lược (MA_PERIOD/X/FAST_MA/...) theo
  // paramFields của strategy đang chọn — để trống = dùng default (server tự
  // resolve, JS không cần biết ý nghĩa từng field).
  function renderParamFields() {
    const spec = CONFIG.strategies[el.strategy.value];
    el.params.innerHTML = "";
    spec.paramFields.forEach((field) => {
      if (HIDDEN_PARAM_KEYS.has(field.key)) return;
      const label = document.createElement("label");
      const input = document.createElement("input");
      input.dataset.key = field.key;
      input.type = field.type === "number" ? "number" : "text";
      if (field.type === "number") {
        if (field.min !== undefined) input.min = field.min;
        if (field.max !== undefined) input.max = field.max;
        if (field.step !== undefined) input.step = field.step;
      }
      input.placeholder = fmtDefault(spec.defaultParams[field.key]);
      input.addEventListener("change", scan);
      label.textContent = field.label;
      label.appendChild(input);
      el.params.appendChild(label);
    });
  }

  function collectOverrides() {
    const overrides = {};
    el.params.querySelectorAll("input").forEach((input) => {
      if (input.value.trim() !== "") overrides[input.dataset.key] = input.value.trim();
    });
    return overrides;
  }

  function currentRequestParams() {
    const spec = CONFIG.strategies[el.strategy.value];
    const params = new URLSearchParams({
      strategy: el.strategy.value,
      symbol: el.symbol.value,
      tf: el.tf.value,
      bars: el.bars.value || CONFIG.defaultBars,
      trend_mode: el.trendMode.value,
      ...collectOverrides(),
    });
    if (el.trendMode.value === "trend_filter") {
      params.set("trend_type", spec.defaultTrendType || "knn");
      params.set("trend_tf", el.trendTf.value);
    }
    if (el.fromTime.value.trim() || el.toTime.value.trim()) {
      params.set("from_time", el.fromTime.value.trim());
      params.set("to_time", el.toTime.value.trim());
    }
    return params;
  }

  function exportCsv() {
    window.location.href = "/api/export?" + currentRequestParams().toString();
  }

  function chartOptions(height) {
    return {
      height: height,
      layout: { background: { color: "#0f172a" }, textColor: "#e2e8f0" },
      grid: { vertLines: { color: "#1e293b" }, horzLines: { color: "#1e293b" } },
      timeScale: { timeVisible: true, secondsVisible: false },
    };
  }

  function ensureCharts() {
    if (priceChart) return;
    priceChart = LightweightCharts.createChart(document.getElementById("price-chart"), chartOptions(360));
    candleSeries = priceChart.addCandlestickSeries({
      upColor: "#16a34a", downColor: "#dc2626", borderVisible: false,
      wickUpColor: "#16a34a", wickDownColor: "#dc2626",
    });
    macdChart = LightweightCharts.createChart(document.getElementById("macd-panel"), chartOptions(140));
    macdSeries = macdChart.addHistogramSeries({});
    window.addEventListener("resize", resizeCharts);
    syncCharts([
      { chart: priceChart, series: candleSeries },
      { chart: macdChart, series: macdSeries, active: () => macdActive },
    ]);
    priceChart.subscribeClick(showSignalDetail);
    priceChart.timeScale().subscribeVisibleLogicalRangeChange(maybeLoadHistory);
    priceChart.timeScale().subscribeVisibleTimeRangeChange(syncEntryToTrendTimeRange);
  }

  function ensureTrendChart() {
    if (trendChart) return;
    trendChart = LightweightCharts.createChart(document.getElementById("trend-chart"), chartOptions(240));
    trendCandleSeries = trendChart.addCandlestickSeries({
      upColor: "#16a34a", downColor: "#dc2626", borderVisible: false,
      wickUpColor: "#16a34a", wickDownColor: "#dc2626",
    });
    trendChart.timeScale().subscribeVisibleTimeRangeChange(syncTrendToEntryTimeRange);
    trendChart.subscribeClick(selectTrendRangePoint);
  }

  function fmtPrice(value) {
    if (value === undefined || value === null || !Number.isFinite(Number(value))) return "-";
    return Number(value).toLocaleString("en-US", { maximumFractionDigits: 5 });
  }

  function hideSignalDetail() {
    el.signalDetail.style.display = "none";
    el.signalDetail.textContent = "";
    delete el.signalDetail.dataset.side;
  }

  function timeframeSeconds(tf) {
    const match = String(tf || "").trim().toUpperCase().match(/^([MHDW])(\\d+)?$/);
    if (!match) return 0;
    const amount = parseInt(match[2] || "1", 10);
    if (!Number.isFinite(amount) || amount <= 0) return 0;
    if (match[1] === "M") return amount * 60;
    if (match[1] === "H") return amount * 60 * 60;
    if (match[1] === "D") return amount * 24 * 60 * 60;
    if (match[1] === "W") return amount * 7 * 24 * 60 * 60;
    return 0;
  }

  function normalizeTimeRange(range) {
    if (!range) return null;
    const from = Number(range.from);
    const to = Number(range.to);
    if (!Number.isFinite(from) || !Number.isFinite(to) || to <= from) return null;
    return { from, to };
  }

  function sameTimeRange(left, right) {
    const a = normalizeTimeRange(left);
    const b = normalizeTimeRange(right);
    if (!a || !b) return false;
    return Math.abs(a.from - b.from) < 1 && Math.abs(a.to - b.to) < 1;
  }

  function currentTrendSeconds() {
    return timeframeSeconds(chartData && chartData.meta ? chartData.meta.trendTf : el.trendTf.value);
  }

  function trendParentRange(range) {
    const visible = normalizeTimeRange(range);
    const seconds = currentTrendSeconds();
    if (!visible || !seconds) return visible;
    const from = Math.floor(visible.from / seconds) * seconds;
    const to = Math.ceil((visible.to + 1) / seconds) * seconds;
    return { from, to: to > from ? to : from + seconds };
  }

  function trendSelectionRange(startTime, endTime) {
    const seconds = currentTrendSeconds();
    const start = Number(startTime);
    const end = Number(endTime);
    if (!Number.isFinite(start) || !Number.isFinite(end) || !seconds) return null;
    const from = Math.min(start, end);
    const to = Math.max(start, end) + seconds;
    return { from, to: to > from ? to : from + seconds };
  }

  function canSyncTrendTimeRange() {
    return Boolean(
      !timeSyncPaused &&
      !syncingTimeRange &&
      chartData &&
      chartData.trendChart &&
      priceChart &&
      trendChart &&
      el.trendWrap.style.display !== "none"
    );
  }

  function setSyncedVisibleTimeRange(chart, range) {
    const target = normalizeTimeRange(range);
    if (!target) return;
    if (sameTimeRange(chart.timeScale().getVisibleRange(), target)) return;

    syncingTimeRange = true;
    try {
      chart.timeScale().setVisibleRange(target);
    } finally {
      window.requestAnimationFrame(() => { syncingTimeRange = false; });
    }
  }

  function pauseTrendTimeSync() {
    timeSyncPaused = true;
    if (timeSyncResumeTimer) window.clearTimeout(timeSyncResumeTimer);
    timeSyncResumeTimer = window.setTimeout(() => {
      timeSyncPaused = false;
      timeSyncResumeTimer = null;
    }, 0);
  }

  function syncTrendToEntryTimeRange(range) {
    if (!canSyncTrendTimeRange()) return;
    setSyncedVisibleTimeRange(priceChart, range);
  }

  function syncEntryToTrendTimeRange(range) {
    if (!canSyncTrendTimeRange()) return;
    setSyncedVisibleTimeRange(trendChart, trendParentRange(range));
  }

  function markerSort(left, right) {
    const diff = Number(left.time) - Number(right.time);
    return diff || String(left.text || "").localeCompare(String(right.text || ""));
  }

  function setEntryMarkers(extraMarkers) {
    if (!candleSeries) return;
    entryRangeMarkers = extraMarkers;
    candleSeries.setMarkers([...entryBaseMarkers, ...entryRangeMarkers].sort(markerSort));
  }

  function setTrendMarkers(extraMarkers) {
    if (!trendCandleSeries) return;
    trendCandleSeries.setMarkers([...trendBaseMarkers, ...extraMarkers].sort(markerSort));
  }

  function trendRangeMarker(time, label) {
    return {
      time: Number(time),
      position: "inBar",
      color: "#eab308",
      shape: "circle",
      text: label,
    };
  }

  function entryRangeMarker(time, label) {
    return {
      time: Number(time),
      position: "inBar",
      color: "#eab308",
      shape: "circle",
      text: label,
    };
  }

  function firstEntryCandleAtOrAfter(time) {
    const target = Number(time);
    if (!chartData || !Number.isFinite(target)) return null;
    const candle = chartData.candles.find((row) => Number(row.time) >= target);
    return candle ? Number(candle.time) : null;
  }

  function lastEntryCandleBefore(time) {
    const target = Number(time);
    if (!chartData || !Number.isFinite(target)) return null;
    for (let i = chartData.candles.length - 1; i >= 0; i -= 1) {
      const candleTime = Number(chartData.candles[i].time);
      if (candleTime < target) return candleTime;
    }
    return null;
  }

  function entryMarkersForTrendSelection(startTime, endTime) {
    const range = endTime === null
      ? { from: Number(startTime), to: Number(startTime) + currentTrendSeconds() }
      : trendSelectionRange(startTime, endTime);
    const start = range ? firstEntryCandleAtOrAfter(range.from) : null;
    if (start === null) return [];
    if (endTime === null) return [entryRangeMarker(start, "A")];

    const end = lastEntryCandleBefore(range.to);
    return end === null
      ? [entryRangeMarker(start, "A")]
      : [entryRangeMarker(start, "A"), entryRangeMarker(end, "B")];
  }

  function selectTrendRangePoint(param) {
    if (!canSyncTrendTimeRange() || param.time === undefined) return;
    const selectedTime = Number(param.time);
    if (!Number.isFinite(selectedTime)) return;

    if (trendRangeStartTime === null) {
      trendRangeStartTime = selectedTime;
      setTrendMarkers([trendRangeMarker(selectedTime, "A")]);
      setEntryMarkers(entryMarkersForTrendSelection(selectedTime, null));
      return;
    }

    const range = trendSelectionRange(trendRangeStartTime, selectedTime);
    setTrendMarkers([
      trendRangeMarker(trendRangeStartTime, "A"),
      trendRangeMarker(selectedTime, "B"),
    ]);
    setEntryMarkers(entryMarkersForTrendSelection(trendRangeStartTime, selectedTime));
    trendRangeStartTime = null;
    setSyncedVisibleTimeRange(priceChart, range);
  }

  function rebuildClickIndex() {
    candlesByTime = new Map(chartData.candles.map((row) => [String(row.time), row]));
    signalsByTime = new Map(chartData.signals.map((row) => [String(row.time), row]));
  }

  // Candle thường không có phản hồi. Chỉ candle BUY/SELL mới mở thông tin
  // OHLC và level tham chiếu tương ứng với đúng timestamp đã click.
  function showSignalDetail(param) {
    const key = param.time === undefined ? "" : String(param.time);
    const candle = candlesByTime.get(key);
    const signal = signalsByTime.get(key);
    if (!candle || !signal) {
      hideSignalDetail();
      return;
    }
    el.signalDetail.dataset.side = signal.side;
    el.signalDetail.textContent =
      signal.side + " · " + signal.bartime + " UTC" +
      "  |  O " + fmtPrice(candle.open) + "  H " + fmtPrice(candle.high) +
      "  L " + fmtPrice(candle.low) + "  C " + fmtPrice(candle.close) +
      "  |  Entry " + fmtPrice(signal.entry) + "  SL " + fmtPrice(signal.sl) +
      "  TP " + fmtPrice(signal.tp) + "  R:R " + fmtPrice(signal.rr) +
      (signal.reason ? "  |  " + signal.reason : "");
    el.signalDetail.style.display = "block";
  }

  // Đồng bộ zoom/pan + crosshair giữa các chart (API chuẩn Lightweight Charts).
  // Pane có active() = false (vd panel MACD bị ẩn, không có dữ liệu) được bỏ
  // qua: ép một chart rỗng đổi vùng nhìn/crosshair làm Lightweight Charts ném
  // "Value is null" ở mọi lần kéo, và làm renderChart() dừng giữa chừng.
  function paneActive(pane) {
    return !pane.active || pane.active();
  }

  function syncCharts(panes) {
    panes.forEach(({ chart }, i) => {
      const others = panes.filter((_, j) => j !== i);
      chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (!range || !paneActive(panes[i])) return;
        others.forEach((o) => {
          if (paneActive(o)) o.chart.timeScale().setVisibleLogicalRange(range);
        });
      });
      chart.subscribeCrosshairMove((param) => {
        if (!paneActive(panes[i])) return;
        others.forEach((o) => {
          if (!paneActive(o)) return;
          if (!param.time || !param.seriesData.size) {
            o.chart.clearCrosshairPosition();
            return;
          }
          const point = param.seriesData.get(panes[i].series);
          const price = point && (point.value !== undefined ? point.value : point.close);
          if (price !== undefined) o.chart.setCrosshairPosition(price, param.time, o.series);
          else o.chart.clearCrosshairPosition();
        });
      });
    });
  }

  function resizeCharts() {
    priceChart.resize(document.getElementById("price-chart").clientWidth, 360);
    macdChart.resize(document.getElementById("macd-panel").clientWidth, 140);
    if (trendChart && el.trendWrap.style.display !== "none") {
      trendChart.resize(document.getElementById("trend-chart").clientWidth, 240);
    }
  }

  function clearDynamicSeries() {
    overlaySeries.forEach((s) => priceChart.removeSeries(s));
    overlaySeries = [];
  }

  function clearTrendSeries() {
    if (!trendChart) return;
    trendOverlaySeries.forEach((s) => trendChart.removeSeries(s));
    trendOverlaySeries = [];
  }

  function renderTrendChart(trendPayload) {
    if (!trendPayload) {
      el.trendWrap.style.display = "none";
      trendRangeStartTime = null;
      trendBaseMarkers = [];
      entryRangeMarkers = [];
      if (trendCandleSeries) {
        trendCandleSeries.setData([]);
        trendCandleSeries.setMarkers([]);
      }
      clearTrendSeries();
      return;
    }

    el.trendWrap.style.display = "block";
    el.trendTitle.textContent = trendPayload.label;
    ensureTrendChart();
    clearTrendSeries();
    trendCandleSeries.setData(trendPayload.candles);
    trendRangeStartTime = null;
    trendBaseMarkers = trendPayload.markers || [];
    entryRangeMarkers = [];
    setTrendMarkers([]);
    trendPayload.overlays.forEach((o) => {
      const series = trendChart.addLineSeries({ color: o.color, lineWidth: 2, title: o.label });
      series.setData(o.data);
      trendOverlaySeries.push(series);
    });
    trendChart.timeScale().fitContent();
  }

  function renderChart(payload, visibleRange) {
    ensureCharts();
    pauseTrendTimeSync();
    renderTrendChart(payload.trendChart);
    clearDynamicSeries();
    candleSeries.setData(payload.candles);
    entryBaseMarkers = payload.markers || [];
    setEntryMarkers([]);

    payload.overlays.forEach((o) => {
      const series = priceChart.addLineSeries({ color: o.color, lineWidth: 2, title: o.label });
      series.setData(o.data);
      overlaySeries.push(series);
    });

    const macdPanel = payload.panels.find((p) => p.key === "macd");
    macdActive = false;
    if (macdPanel) {
      document.getElementById("macd-panel").style.display = "block";
      macdSeries.setData(macdPanel.data);
    } else {
      macdSeries.setData([]);
      document.getElementById("macd-panel").style.display = "none";
    }
    macdActive = Boolean(macdPanel);

    rebuildClickIndex();
    if (visibleRange) {
      priceChart.timeScale().setVisibleLogicalRange(visibleRange);
    } else {
      priceChart.timeScale().fitContent();
      if (macdPanel) macdChart.timeScale().fitContent();
    }
  }

  function mergeByTime(current, older) {
    const rows = new Map(older.map((row) => [String(row.time), row]));
    current.forEach((row) => rows.set(String(row.time), row));
    return Array.from(rows.values()).sort((a, b) => Number(a.time) - Number(b.time));
  }

  function mergeSeries(current, older) {
    return current.map((series) => {
      const previous = older.find((item) => item.key === series.key);
      return { ...series, data: mergeByTime(series.data, previous ? previous.data : []) };
    });
  }

  function mergeChartPayload(current, older) {
    if (!current) return older;
    if (!older) return current;
    return {
      ...current,
      candles: mergeByTime(current.candles, older.candles),
      overlays: mergeSeries(current.overlays, older.overlays),
      panels: mergeSeries(current.panels || [], older.panels || []),
      markers: mergeByTime(current.markers || [], older.markers || []),
    };
  }

  function mergeHistory(current, older) {
    return {
      ...current,
      trendChart: mergeChartPayload(current.trendChart, older.trendChart),
      candles: mergeByTime(current.candles, older.candles),
      overlays: mergeSeries(current.overlays, older.overlays),
      panels: mergeSeries(current.panels, older.panels),
      markers: mergeByTime(current.markers, older.markers),
      signals: mergeByTime(current.signals, older.signals),
      meta: { ...current.meta, hasMore: older.meta.hasMore },
    };
  }

  function renderStats(rows, meta, candleCount, stats) {
    const buy = rows.filter((row) => row.side === "BUY").length;
    const sell = rows.filter((row) => row.side === "SELL").length;
    const last = rows.length ? rows[rows.length - 1].side : "-";
    const trendText = meta.trendMode === "trend_filter"
      ? " · trend " + meta.trendTf + " " + String(meta.trendType || "").toUpperCase()
      : " · no trend";
    const timeText = meta.timeMode === "range"
      ? " · " + meta.fromTime + " -> " + meta.toTime
      : "";
    // So sánh raw vs đã lọc trend (chốt 2026-09-25) -- chỉ có ý nghĩa khi
    // BẬT trend filter (No Trend thì raw luôn == filtered, hiện ra chỉ gây
    // nhiễu). Kiểm typeof để an toàn lùi nếu payload cũ chưa có rawTotal.
    let compareText = "";
    if (meta.trendMode === "trend_filter" && stats && typeof stats.rawTotal === "number" && stats.rawTotal > 0) {
      const pct = (rows.length / stats.rawTotal * 100).toFixed(1);
      compareText = " · raw " + stats.rawTotal + " -> filtered " + rows.length + " (" + pct + "%)";
    }
    el.stats.textContent =
      meta.strategyLabel + " · " + meta.symbol + " · " + meta.tf + trendText + timeText + " · " + candleCount + " bars · " +
      rows.length + " signals (" + buy + " BUY / " + sell + " SELL)" + compareText + " · last: " + last;
  }

  function showError(message) {
    el.error.textContent = "ERROR: " + message;
    el.error.style.display = "block";
  }
  function clearError() {
    el.error.style.display = "none";
  }

  function maybeLoadHistory(range) {
    if (!range || !chartData || loadingHistory || !chartData.meta.hasMore) return;
    if (chartData.meta.timeMode === "range") return;
    const info = candleSeries.barsInLogicalRange(range);
    if (info && info.barsBefore < 0) loadHistory();
  }

  async function loadHistory() {
    if (!chartData || loadingHistory || !chartData.meta.hasMore || !chartData.candles.length) return;
    loadingHistory = true;
    const version = scanVersion;
    const oldest = chartData.candles[0].time;
    const params = currentRequestParams();
    params.set("before", String(oldest));
    const visible = priceChart.timeScale().getVisibleLogicalRange();
    const previousCount = chartData.candles.length;
    try {
      const response = await fetch("/api/scan?" + params.toString());
      const payload = await response.json();
      if (version !== scanVersion) return;
      if (!response.ok) {
        showError(payload.error || ("HTTP " + response.status));
        return;
      }
      if (!payload.candles.length) {
        chartData.meta.hasMore = false;
        return;
      }
      chartData = mergeHistory(chartData, payload);
      const added = chartData.candles.length - previousCount;
      const restored = visible && added > 0
        ? { from: visible.from + added, to: visible.to + added }
        : visible;
      renderChart(chartData, restored);
      renderStats(chartData.signals, chartData.meta, chartData.candles.length, chartData.stats);
    } catch (err) {
      if (version === scanVersion) showError("Network error: " + err);
    } finally {
      if (version === scanVersion) loadingHistory = false;
    }
  }

  async function scan() {
    const version = ++scanVersion;
    loadingHistory = false;
    clearError();
    hideSignalDetail();
    let response, payload;
    try {
      response = await fetch("/api/scan?" + currentRequestParams().toString());
      payload = await response.json();
    } catch (err) {
      if (version === scanVersion) showError("Network error: " + err);
      return;
    }
    if (version !== scanVersion) return;
    if (!response.ok) {
      showError(payload.error || ("HTTP " + response.status));
      return;
    }
    chartData = payload;
    renderChart(chartData, null);
    renderStats(chartData.signals, chartData.meta, chartData.candles.length, chartData.stats);
  }

  function setAutoRefresh() {
    if (autoTimer) { clearInterval(autoTimer); autoTimer = null; }
    const ms = parseInt(el.auto.value, 10);
    if (ms > 0) autoTimer = setInterval(scan, ms);
  }

  el.strategy.addEventListener("change", () => {
    populateSymbols();
    populateTimeframes();
    populateTrendControls();
    renderParamFields();
    scan();
  });
  el.symbol.addEventListener("change", scan);
  el.tf.addEventListener("change", scan);
  el.bars.addEventListener("change", scan);
  el.fromTime.addEventListener("change", scan);
  el.toTime.addEventListener("change", scan);
  el.trendMode.addEventListener("change", () => { updateTrendControls(); scan(); });
  el.trendTf.addEventListener("change", scan);
  el.refresh.addEventListener("click", scan);
  el.export.addEventListener("click", exportCsv);
  el.auto.addEventListener("change", setAutoRefresh);

  populateControls();
  scan();
})();
</script>
</body>
</html>
"""


def _read_vendor_js() -> str:
    """Đọc nội dung thư viện Lightweight Charts đã vendor sẵn."""
    return _VENDOR_JS_PATH.read_text(encoding="utf-8")


def render_dashboard_html(
    *,
    strategies: dict[str, dict[str, Any]],
    symbols: list[str],
    timeframes: list[str],
    default_strategy: str,
    default_symbol: str,
    default_bars: int,
    bars_min: int,
    bars_max: int,
) -> str:
    """Dựng trang dashboard sống — HTML tự chứa, JS bên trong tự fetch /api/scan.

    `strategies` (key -> label/recommendedTimeframes/supportedTimeframes/
    defaultTimeframe), `symbols`, `timeframes`, 3 default_* và bars_min/
    bars_max (giới hạn ô nhập Bars — cùng nguồn với cli.clamp_bars() phía
    server, tránh gõ chết lại số trong HTML) được nhúng thẳng làm `CONFIG`
    cho JS phía client.
    """
    config = {
        "strategies": strategies,
        "symbols": symbols,
        "timeframes": timeframes,
        "defaultStrategy": default_strategy,
        "defaultSymbol": default_symbol,
        "defaultBars": default_bars,
        "barsMin": bars_min,
        "barsMax": bars_max,
    }
    html = _TEMPLATE
    html = html.replace("__VENDOR_JS__", _read_vendor_js())
    html = html.replace("__CONFIG_JSON__", json.dumps(config))
    return html
