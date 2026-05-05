const state = {
  config: null,
  priceChart: null,
  panelCharts: [],
  panelSeries: [],
};

const el = {
  strategy: document.getElementById("strategy"),
  assetType: document.getElementById("asset-type"),
  symbol: document.getElementById("symbol"),
  tf: document.getElementById("tf"),
  bars: document.getElementById("bars"),
  params: document.getElementById("params"),
  refresh: document.getElementById("refresh"),
  meta: document.getElementById("meta"),
  error: document.getElementById("error"),
  priceChart: document.getElementById("price-chart"),
  chartLegend: document.getElementById("chart-legend"),
  panelCharts: document.getElementById("panel-charts"),
  statTotal: document.getElementById("stat-total"),
  statBuy: document.getElementById("stat-buy"),
  statSell: document.getElementById("stat-sell"),
  statLast: document.getElementById("stat-last"),
  signalsHead: document.getElementById("signals-head"),
  signalsBody: document.getElementById("signals-body"),
  exportBtn: document.getElementById("export-btn"),
  exportModal: document.getElementById("export-modal"),
  exportCols: document.getElementById("export-cols"),
  modalClose: document.getElementById("modal-close"),
  modalCancel: document.getElementById("modal-cancel"),
  modalExport: document.getElementById("modal-export"),
};

const chartTheme = {
  layout: {
    background: { color: "#161b22" },
    textColor: "#e6edf3",
  },
  grid: {
    vertLines: { color: "#30363d" },
    horzLines: { color: "#30363d" },
  },
  rightPriceScale: {
    borderColor: "#30363d",
  },
  timeScale: {
    borderColor: "#30363d",
    timeVisible: true,
    secondsVisible: false,
  },
  crosshair: {
    mode: LightweightCharts.CrosshairMode.Normal,
  },
};

const COLUMN_LABELS = {
  bartime: "Bar Time",
  side: "Side",
  reason: "Reason",
  entry: "Entry",
  sl: "SL",
  tp: "TP",
  rr: "R:R",
  atr: "ATR",
  ma: "MA",
  macd_h: "MACD-H",
  fast_ma: "Fast MA",
  slow_ma: "Slow MA",
  ma_gap_atr: "Gap/ATR",
};

const TEXT_COLS = new Set(["bartime", "side", "reason"]);

const EXPORT_COLUMNS = {
  combo: [
    { key: "bartime", label: "Bar Time", checked: true },
    { key: "side", label: "Side", checked: true },
    { key: "signal_reason", label: "Reason", checked: true },
    { key: "entry_price", label: "Entry", checked: true },
    { key: "sl_price", label: "SL", checked: true },
    { key: "tp_price", label: "TP", checked: true },
    { key: "risk_reward", label: "R:R", checked: true },
    { key: "atr", label: "ATR", checked: false },
    { key: "open", label: "Open", checked: false },
    { key: "high", label: "High", checked: false },
    { key: "low", label: "Low", checked: false },
    { key: "close", label: "Close", checked: false },
    { key: "ma", label: "MA", checked: false },
    { key: "macd_h", label: "MACD-H", checked: false },
  ],
  ma_cross: [
    { key: "bartime", label: "Bar Time", checked: true },
    { key: "side", label: "Side", checked: true },
    { key: "signal_reason", label: "Reason", checked: true },
    { key: "entry_price", label: "Entry", checked: true },
    { key: "sl_price", label: "SL", checked: true },
    { key: "tp_price", label: "TP", checked: true },
    { key: "risk_reward", label: "R:R", checked: true },
    { key: "atr", label: "ATR", checked: false },
    { key: "open", label: "Open", checked: false },
    { key: "high", label: "High", checked: false },
    { key: "low", label: "Low", checked: false },
    { key: "close", label: "Close", checked: false },
    { key: "fast_ma", label: "Fast MA", checked: false },
    { key: "slow_ma", label: "Slow MA", checked: false },
    { key: "ma_gap_atr", label: "Gap/ATR", checked: false },
  ],
};

function fmtNum(value) {
  if (value == null || value === "") return "";
  const n = Number(value);
  if (!isFinite(n)) return String(value);
  const abs = Math.abs(n);
  if (abs >= 1000) return new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n);
  if (abs >= 10) return n.toFixed(2);
  if (abs >= 0.01) return n.toFixed(4);
  return n.toFixed(5);
}

function fmtPrice(n) {
  const abs = Math.abs(n);
  if (abs >= 100) return new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n);
  return n.toFixed(5);
}

function createOption(parent, value, label, selectedValue) {
  const option = document.createElement("option");
  option.value = value;
  option.textContent = label ?? value;
  option.selected = value === selectedValue;
  parent.appendChild(option);
}

const symbolMeta = {};

function populateSymbols(assetFilter) {
  el.symbol.replaceChildren();
  state.config.symbols
    .filter((s) => !assetFilter || s.asset_type === assetFilter)
    .forEach((s) => createOption(el.symbol, s.name, s.name, state.config.defaultSymbol));
}

function updateXDefault() {
  if (el.strategy.value !== "combo") return;
  const meta = symbolMeta[el.symbol.value];
  if (!meta) return;
  const xInput = el.params.querySelector('[data-param="X"]');
  if (xInput && meta.x != null) xInput.value = meta.x > 0 ? meta.x : "";
}

async function init() {
  const response = await fetch("/api/config");
  state.config = await response.json();

  Object.entries(state.config.strategies).forEach(([key, spec]) => {
    createOption(el.strategy, key, spec.label, state.config.defaultStrategy);
  });

  state.config.symbols.forEach((s) => { symbolMeta[s.name] = s; });

  const assetTypes = [...new Set(state.config.symbols.map((s) => s.asset_type).filter(Boolean))].sort();
  createOption(el.assetType, "", "All", "");
  assetTypes.forEach((type) => createOption(el.assetType, type, type, ""));

  populateSymbols("");

  state.config.timeframes.forEach((tf) => createOption(el.tf, tf, tf, state.config.defaultTf));
  el.bars.value = state.config.defaultBars;

  el.strategy.addEventListener("change", () => {
    renderParamControls();
    updateXDefault();
    loadScan();
  });
  el.assetType.addEventListener("change", () => {
    populateSymbols(el.assetType.value);
    updateXDefault();
    loadScan();
  });
  el.symbol.addEventListener("change", () => {
    updateXDefault();
    loadScan();
  });
  el.tf.addEventListener("change", loadScan);
  el.bars.addEventListener("change", loadScan);
  el.refresh.addEventListener("click", loadScan);
  el.exportBtn.addEventListener("click", openExportModal);
  el.modalClose.addEventListener("click", () => el.exportModal.close());
  el.modalCancel.addEventListener("click", () => el.exportModal.close());
  el.modalExport.addEventListener("click", doExport);

  renderParamControls();
  updateXDefault();
  await loadScan();
}

function renderParamControls() {
  const strategy = el.strategy.value;
  const spec = state.config.strategies[strategy];
  el.params.replaceChildren();

  spec.fields.forEach((field) => {
    const defaultValue = spec.defaults[field.key];
    if (field.type === "bool") {
      const row = document.createElement("label");
      row.className = "check-row";
      row.textContent = field.label;
      const input = document.createElement("input");
      input.type = "checkbox";
      input.dataset.param = field.key;
      input.checked = Boolean(defaultValue);
      input.addEventListener("change", loadScan);
      row.appendChild(input);
      el.params.appendChild(row);
      return;
    }

    const label = document.createElement("label");
    label.textContent = field.label;
    let input;
    if (field.type === "select") {
      input = document.createElement("select");
      field.options.forEach((value) => createOption(input, value, value.toUpperCase(), defaultValue));
    } else {
      input = document.createElement("input");
      input.type = field.type === "text" ? "text" : "number";
      if (field.min !== undefined) input.min = field.min;
      if (field.max !== undefined) input.max = field.max;
      if (field.step !== undefined) input.step = field.step;
      input.value = defaultValue === null || defaultValue === undefined ? "" : defaultValue;
      if (field.type === "optional_number") input.placeholder = "off";
    }
    input.dataset.param = field.key;
    input.addEventListener("change", loadScan);
    label.appendChild(input);
    el.params.appendChild(label);
  });
}

function collectParams() {
  const params = {};
  el.params.querySelectorAll("[data-param]").forEach((input) => {
    params[input.dataset.param] = input.type === "checkbox" ? String(input.checked) : input.value;
  });
  return params;
}

async function loadScan() {
  clearError();
  const query = new URLSearchParams({
    strategy: el.strategy.value,
    symbol: el.symbol.value,
    tf: el.tf.value,
    bars: el.bars.value,
    ...collectParams(),
  });

  try {
    const response = await fetch(`/api/scan?${query.toString()}`);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Scan failed");
    renderPayload(payload);
  } catch (error) {
    showError(error.message);
  }
}

function showError(message) {
  el.error.hidden = false;
  el.error.textContent = message;
}

function clearError() {
  el.error.hidden = true;
  el.error.textContent = "";
}

function resetCharts() {
  if (state.priceChart) {
    state.priceChart.remove();
    state.priceChart = null;
  }
  state.panelCharts.forEach((chart) => chart.remove());
  state.panelCharts = [];
  state.panelSeries = [];
  el.panelCharts.replaceChildren();
}

function renderPayload(payload) {
  resetCharts();
  el.meta.textContent = `${payload.meta.strategyLabel} | ${payload.meta.symbol} ${payload.meta.tf} | ${payload.meta.bars} bars`;
  el.statTotal.textContent = payload.stats.total;
  el.statBuy.textContent = payload.stats.buy;
  el.statSell.textContent = payload.stats.sell;
  el.statLast.textContent = payload.stats.last;

  const priceChart = LightweightCharts.createChart(el.priceChart, {
    ...chartTheme,
    width: el.priceChart.clientWidth,
    height: el.priceChart.clientHeight,
  });
  state.priceChart = priceChart;

  const candleSeries = priceChart.addCandlestickSeries({
    upColor: "#22c55e",
    downColor: "#ef4444",
    borderUpColor: "#22c55e",
    borderDownColor: "#ef4444",
    wickUpColor: "#22c55e",
    wickDownColor: "#ef4444",
  });
  candleSeries.setData(payload.candles);
  candleSeries.setMarkers(payload.markers.sort((a, b) => a.time - b.time));

  payload.overlays.forEach((line) => {
    const series = priceChart.addLineSeries({
      color: line.color,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      title: line.label,
    });
    series.setData(line.data);
  });

  payload.levels.forEach((level) => {
    const series = priceChart.addLineSeries({
      color: level.color,
      lineWidth: 1,
      lineStyle: lineStyle(level.style),
      priceLineVisible: false,
      lastValueVisible: false,
      title: "",
    });
    series.setData([
      { time: level.timeStart, value: level.price },
      { time: level.timeEnd, value: level.price },
    ]);
  });

  const indicatorByTime = {};
  payload.overlays.forEach((line) => {
    line.data.forEach((d) => {
      if (!indicatorByTime[d.time]) indicatorByTime[d.time] = {};
      indicatorByTime[d.time][line.key] = { value: d.value, label: line.label };
    });
  });
  payload.panels.forEach((panel) => {
    panel.data.forEach((d) => {
      if (!indicatorByTime[d.time]) indicatorByTime[d.time] = {};
      indicatorByTime[d.time][panel.key] = { value: d.value, label: panel.label };
    });
  });

  renderPanels(payload.panels, priceChart);

  el.chartLegend.textContent = "";
  priceChart.subscribeCrosshairMove((param) => {
    if (!param.time || !param.seriesData.has(candleSeries)) {
      el.chartLegend.textContent = "";
      state.panelSeries.forEach(({ chart }) => chart.clearCrosshairPosition());
      return;
    }
    const ohlcv = param.seriesData.get(candleSeries);
    const dateStr = new Date(param.time * 1000).toISOString().slice(0, 16).replace("T", " ");
    const ind = indicatorByTime[param.time] || {};
    const parts = [
      dateStr,
      `O:${fmtPrice(ohlcv.open)} H:${fmtPrice(ohlcv.high)} L:${fmtPrice(ohlcv.low)} C:${fmtPrice(ohlcv.close)}`,
      ...Object.values(ind).map((v) => `${v.label}: ${fmtNum(v.value)}`),
    ];
    el.chartLegend.textContent = parts.join("   |   ");

    state.panelSeries.forEach(({ chart, series, key }) => {
      const indVal = (indicatorByTime[param.time] || {})[key];
      if (indVal != null) {
        chart.setCrosshairPosition(indVal.value, param.time, series);
      } else {
        chart.clearCrosshairPosition();
      }
    });
  });

  renderSignalsTable(payload.signals);
  priceChart.timeScale().fitContent();
}

function renderPanels(panels, priceChart) {
  panels.forEach((panel) => {
    const container = document.createElement("div");
    container.className = "chart indicator-chart";
    el.panelCharts.appendChild(container);

    const chart = LightweightCharts.createChart(container, {
      ...chartTheme,
      width: container.clientWidth,
      height: container.clientHeight,
    });
    state.panelCharts.push(chart);

    let series;
    if (panel.type === "histogram") {
      series = chart.addHistogramSeries({
        priceFormat: { type: "price", precision: 5, minMove: 0.00001 },
        priceLineVisible: false,
        lastValueVisible: false,
      });
    } else {
      series = chart.addLineSeries({
        color: panel.color || "#a855f7",
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        title: panel.label,
      });
    }
    series.setData(panel.data);
    state.panelSeries.push({ chart, series, key: panel.key });

    priceChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (range) chart.timeScale().setVisibleLogicalRange(range);
    });
  });
}

function renderSignalsTable(rows) {
  el.signalsHead.replaceChildren();
  el.signalsBody.replaceChildren();
  if (!rows.length) return;

  const keys = Object.keys(rows[0]);
  const headRow = document.createElement("tr");
  keys.forEach((key) => {
    const th = document.createElement("th");
    th.textContent = COLUMN_LABELS[key] ?? key;
    headRow.appendChild(th);
  });
  el.signalsHead.appendChild(headRow);

  rows.slice().reverse().forEach((row) => {
    const tr = document.createElement("tr");
    keys.forEach((key) => {
      const td = document.createElement("td");
      const val = row[key];
      if (TEXT_COLS.has(key)) {
        td.textContent = val ?? "";
      } else {
        td.textContent = fmtNum(val);
        td.className = "num";
      }
      tr.appendChild(td);
    });
    el.signalsBody.appendChild(tr);
  });
}

function openExportModal() {
  const cols = EXPORT_COLUMNS[el.strategy.value] || EXPORT_COLUMNS.combo;
  el.exportCols.replaceChildren();
  cols.forEach((col) => {
    const label = document.createElement("label");
    label.className = "check-row";
    label.textContent = col.label;
    const input = document.createElement("input");
    input.type = "checkbox";
    input.dataset.col = col.key;
    input.checked = col.checked;
    label.appendChild(input);
    el.exportCols.appendChild(label);
  });
  el.exportModal.showModal();
}

function doExport() {
  const cols = [...el.exportCols.querySelectorAll("input[type=checkbox]:checked")]
    .map((cb) => cb.dataset.col)
    .join(",");
  const query = new URLSearchParams({
    strategy: el.strategy.value,
    symbol: el.symbol.value,
    tf: el.tf.value,
    bars: el.bars.value,
    cols,
    ...collectParams(),
  });
  window.open(`/api/export?${query.toString()}`, "_blank");
  el.exportModal.close();
}

function lineStyle(style) {
  if (style === "dotted") return LightweightCharts.LineStyle.Dotted;
  if (style === "dashed") return LightweightCharts.LineStyle.Dashed;
  return LightweightCharts.LineStyle.Solid;
}

window.addEventListener("resize", () => {
  if (state.priceChart) {
    state.priceChart.applyOptions({ width: el.priceChart.clientWidth });
  }
  state.panelCharts.forEach((chart) => {
    chart.applyOptions({ width: chart.chartElement?.clientWidth || el.priceChart.clientWidth });
  });
});

init().catch((error) => showError(error.message));

