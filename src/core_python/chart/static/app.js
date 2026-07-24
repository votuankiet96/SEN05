const state = {
  config: null,
  priceChart: null,
  panelCharts: [],
  panelSeries: [],
  lastPayload: null,
  scanAbort: null,
  scanRequestId: 0,
  scanTimer: null,
  queuedScan: false,
  viewVersion: 0,
  isLoading: false,
  isExporting: false,
  theme: "light",
  candleUpColor: "#16a34a",
  candleDownColor: "#dc2626",
};

const el = {
  strategy: document.getElementById("strategy"),
  assetType: document.getElementById("asset-type"),
  symbol: document.getElementById("symbol"),
  tf: document.getElementById("tf"),
  bars: document.getElementById("bars"),
  barsCaption: document.getElementById("bars-caption"),
  startDate: document.getElementById("start-date"),
  endDate: document.getElementById("end-date"),
  themeSelect: document.getElementById("theme-select"),
  candleUpColor: document.getElementById("candle-up-color"),
  candleDownColor: document.getElementById("candle-down-color"),
  params: document.getElementById("params"),
  refresh: document.getElementById("refresh"),
  meta: document.getElementById("meta"),
  error: document.getElementById("error"),
  scanWarning: document.getElementById("scan-warning"),
  loadingOverlay: document.getElementById("loading-overlay"),
  loadingText: document.getElementById("loading-text"),
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
  bulkExportBtn: document.getElementById("bulk-export-btn"),
  exportModal: document.getElementById("export-modal"),
  exportCols: document.getElementById("export-cols"),
  exportStartDate: document.getElementById("export-start-date"),
  exportEndDate: document.getElementById("export-end-date"),
  exportRangeNote: document.getElementById("export-range-note"),
  modalClose: document.getElementById("modal-close"),
  modalCancel: document.getElementById("modal-cancel"),
  modalExport: document.getElementById("modal-export"),
  bulkExportModal: document.getElementById("bulk-export-modal"),
  bulkSymbols: document.getElementById("bulk-symbols"),
  bulkCols: document.getElementById("bulk-cols"),
  bulkExportStartDate: document.getElementById("bulk-export-start-date"),
  bulkExportEndDate: document.getElementById("bulk-export-end-date"),
  bulkExportRangeNote: document.getElementById("bulk-export-range-note"),
  bulkSymbolsAll: document.getElementById("bulk-symbols-all"),
  bulkSymbolsNone: document.getElementById("bulk-symbols-none"),
  bulkModalClose: document.getElementById("bulk-modal-close"),
  bulkModalCancel: document.getElementById("bulk-modal-cancel"),
  bulkModalExport: document.getElementById("bulk-modal-export"),
};

const THEME_STORAGE_KEY = "sen05.chart.theme";
const CANDLE_UP_STORAGE_KEY = "sen05.chart.candleUp";
const CANDLE_DOWN_STORAGE_KEY = "sen05.chart.candleDown";

const CHART_THEMES = {
  light: {
    background: "#ffffff",
    text: "#334155",
    grid: "#e2e8f0",
  },
  dark: {
    background: "#161b22",
    text: "#e6edf3",
    grid: "#30363d",
  },
};

function isHexColor(value) {
  return /^#[0-9a-f]{6}$/i.test(String(value || ""));
}

function loadVisualPrefs() {
  const storedTheme = localStorage.getItem(THEME_STORAGE_KEY);
  const storedUp = localStorage.getItem(CANDLE_UP_STORAGE_KEY);
  const storedDown = localStorage.getItem(CANDLE_DOWN_STORAGE_KEY);
  state.theme = storedTheme === "dark" ? "dark" : "light";
  if (isHexColor(storedUp)) state.candleUpColor = storedUp;
  if (isHexColor(storedDown)) state.candleDownColor = storedDown;
}

function applyVisualPrefs() {
  document.documentElement.dataset.theme = state.theme;
  el.themeSelect.value = state.theme;
  el.candleUpColor.value = state.candleUpColor;
  el.candleDownColor.value = state.candleDownColor;
}

function saveVisualPrefs() {
  localStorage.setItem(THEME_STORAGE_KEY, state.theme);
  localStorage.setItem(CANDLE_UP_STORAGE_KEY, state.candleUpColor);
  localStorage.setItem(CANDLE_DOWN_STORAGE_KEY, state.candleDownColor);
}

function currentChartTheme() {
  const colors = CHART_THEMES[state.theme] || CHART_THEMES.light;
  return {
    layout: {
      background: { color: colors.background },
      textColor: colors.text,
    },
    grid: {
      vertLines: { color: colors.grid },
      horzLines: { color: colors.grid },
    },
    rightPriceScale: {
      borderColor: colors.grid,
    },
    timeScale: {
      borderColor: colors.grid,
      timeVisible: true,
      secondsVisible: false,
    },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
    },
  };
}

function currentCandleOptions() {
  return {
    upColor: state.candleUpColor,
    downColor: state.candleDownColor,
    borderUpColor: state.candleUpColor,
    borderDownColor: state.candleDownColor,
    wickUpColor: state.candleUpColor,
    wickDownColor: state.candleDownColor,
  };
}

function updateThemeFromControls() {
  state.theme = el.themeSelect.value === "dark" ? "dark" : "light";
  if (isHexColor(el.candleUpColor.value)) state.candleUpColor = el.candleUpColor.value;
  if (isHexColor(el.candleDownColor.value)) state.candleDownColor = el.candleDownColor.value;
  applyVisualPrefs();
  saveVisualPrefs();
  if (state.lastPayload) {
    renderPayload(state.lastPayload);
  } else if (state.config) {
    loadScan();
  }
}

function updateRefreshButton() {
  if (state.isLoading) {
    el.refresh.textContent = state.queuedScan ? "Queued..." : "Loading...";
    el.refresh.disabled = true;
    return;
  }
  el.refresh.disabled = false;
  el.refresh.textContent = "Refresh";
}

function setLoading(loading, message = "Loading chart data...") {
  state.isLoading = loading;
  if (el.loadingOverlay) {
    el.loadingOverlay.hidden = !loading;
  }
  if (loading && el.loadingText) {
    el.loadingText.textContent = state.queuedScan ? "Current scan is finishing. Latest selection will load next..." : message;
  }
  updateRefreshButton();
}

function markViewChanged() {
  state.viewVersion += 1;
}

function scheduleLoadScan(delay = 300) {
  clearTimeout(state.scanTimer);
  state.scanTimer = setTimeout(() => {
    loadScan();
  }, delay);
}

function handleAutoScanChange() {
  markViewChanged();
  scheduleLoadScan();
}

const COLUMN_LABELS = {
  bartime: "Bar Time",
  side: "Side",
  signal: "Signal",
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
  open: "Open",
  high: "High",
  low: "Low",
  close: "Close",
  volume: "Volume",
  signal_reason: "Reason",
  entry_time: "Entry Time",
  entry_price: "Entry",
  sl_price: "SL",
  tp_price: "TP",
  risk_reward: "R:R",
};

const TEXT_COLS = new Set(["bartime", "side", "reason", "signal_reason", "entry_time"]);

const EXPORT_COLUMNS = {
  combo: [
    { key: "bartime", label: "Bar Time", checked: true },
    { key: "side", label: "Side", checked: true },
    { key: "signal", label: "Signal", checked: false },
    { key: "signal_reason", label: "Reason", checked: false },
    { key: "entry_price", label: "Entry", checked: false },
    { key: "sl_price", label: "SL", checked: false },
    { key: "tp_price", label: "TP", checked: false },
    { key: "risk_reward", label: "R:R", checked: false },
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
    { key: "signal", label: "Signal", checked: false },
    { key: "signal_reason", label: "Reason", checked: false },
    { key: "entry_price", label: "Entry", checked: false },
    { key: "sl_price", label: "SL", checked: false },
    { key: "tp_price", label: "TP", checked: false },
    { key: "risk_reward", label: "R:R", checked: false },
    { key: "atr", label: "ATR", checked: false },
    { key: "open", label: "Open", checked: false },
    { key: "high", label: "High", checked: false },
    { key: "low", label: "Low", checked: false },
    { key: "close", label: "Close", checked: false },
    { key: "fast_ma", label: "Fast MA", checked: false },
    { key: "slow_ma", label: "Slow MA", checked: false },
    { key: "macd_h", label: "MACD-H", checked: false },
    { key: "ma_gap_atr", label: "Gap/ATR", checked: false },
  ],
};

const BULK_EXPORT_COLUMNS = {
  combo: [
    { key: "atr", label: "ATR", checked: true },
    { key: "ma", label: "MA", checked: false },
    { key: "macd_h", label: "MACD-H", checked: false },
    { key: "open", label: "Open", checked: false },
    { key: "high", label: "High", checked: false },
    { key: "low", label: "Low", checked: false },
    { key: "close", label: "Close", checked: false },
    { key: "signal_reason", label: "Reason", checked: false },
  ],
  ma_cross: [
    { key: "atr", label: "ATR", checked: true },
    { key: "fast_ma", label: "Fast MA", checked: false },
    { key: "slow_ma", label: "Slow MA", checked: false },
    { key: "macd_h", label: "MACD-H", checked: false },
    { key: "ma_gap_atr", label: "Gap/ATR", checked: false },
    { key: "open", label: "Open", checked: false },
    { key: "high", label: "High", checked: false },
    { key: "low", label: "Low", checked: false },
    { key: "close", label: "Close", checked: false },
    { key: "signal_reason", label: "Reason", checked: false },
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

function fmtUtcMinute(ts) {
  return new Date(ts * 1000).toISOString().slice(0, 16).replace("T", " ");
}

function fmtOhlc(candle) {
  if (!candle) return "";
  return `O:${fmtPrice(candle.open)} H:${fmtPrice(candle.high)} L:${fmtPrice(candle.low)} C:${fmtPrice(candle.close)}`;
}

function twoDigits(value) {
  return String(value).padStart(2, "0");
}

function displayDateFromDate(date) {
  return `${twoDigits(date.getDate())}/${twoDigits(date.getMonth() + 1)}/${date.getFullYear()}`;
}

function defaultStartDate() {
  const date = new Date();
  date.setDate(date.getDate() - 60);
  return displayDateFromDate(date);
}

function defaultEndDate() {
  return displayDateFromDate(new Date());
}

function createOption(parent, value, label, selectedValue) {
  const option = document.createElement("option");
  option.value = value;
  option.textContent = label ?? value;
  option.selected = value === selectedValue;
  parent.appendChild(option);
}

const symbolMeta = {};

function strategyDefaults(strategy = el.strategy.value) {
  const defaults = state.config?.strategyDefaults?.[strategy] || {};
  const spec = state.config?.strategies?.[strategy] || {};
  const recommended = spec.recommendedTimeframes || [];
  return {
    symbol: defaults.symbol || state.config.defaultSymbol,
    tf: defaults.tf || spec.defaultTimeframe || recommended[0] || state.config.defaultTf,
  };
}

function selectOptionIfPresent(selectEl, value) {
  if (!value) return false;
  const exists = [...selectEl.options].some((option) => option.value === value);
  if (exists) {
    selectEl.value = value;
  }
  return exists;
}

function applyStrategySelectionDefaults(strategy = el.strategy.value) {
  const configured = state.config?.strategyDefaults?.[strategy] || {};
  const defaults = strategyDefaults(strategy);
  if (configured.symbol && symbolMeta[defaults.symbol] && ![...el.symbol.options].some((option) => option.value === defaults.symbol)) {
    el.assetType.value = "";
    populateSymbols("", defaults.symbol);
  }
  if (configured.symbol) selectOptionIfPresent(el.symbol, defaults.symbol);
  populateTimeframes(strategy, defaults.tf);
}

function strategyTimeframes(strategy = el.strategy.value) {
  const supported = state.config?.strategies?.[strategy]?.supportedTimeframes || [];
  return supported.length ? supported : state.config.timeframes;
}

function populateTimeframes(strategy = el.strategy.value, selectedValue = el.tf.value) {
  const timeframes = strategyTimeframes(strategy);
  const fallback = strategyDefaults(strategy).tf;
  const selected = timeframes.includes(selectedValue)
    ? selectedValue
    : (timeframes.includes(fallback) ? fallback : timeframes[0]);
  el.tf.replaceChildren();
  timeframes.forEach((tf) => createOption(el.tf, tf, tf, selected));
}

function populateSymbols(assetFilter, selectedValue = el.symbol.value || strategyDefaults().symbol) {
  el.symbol.replaceChildren();
  state.config.symbols
    .filter((s) => !assetFilter || s.asset_type === assetFilter)
    .forEach((s) => createOption(el.symbol, s.name, s.name, selectedValue));
}

function updateSymbolDefaults() {
  const defaults = state.config?.strategies?.[el.strategy.value]?.symbolDefaults?.[el.symbol.value] || {};
  const xInput = el.params.querySelector('[data-param="X"]');
  if (xInput) xInput.value = defaults.X ?? "";
}

async function init() {
  loadVisualPrefs();
  applyVisualPrefs();

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

  populateTimeframes(state.config.defaultStrategy, strategyDefaults(state.config.defaultStrategy).tf);
  el.bars.value = state.config.defaultBars;
  el.startDate.value = defaultStartDate();
  el.endDate.value = defaultEndDate();

  el.strategy.addEventListener("change", () => {
    markViewChanged();
    renderParamControls();
    applyStrategyDefaults({ resetSelection: true });
    updateSymbolDefaults();
    loadScan();
  });
  el.assetType.addEventListener("change", () => {
    populateSymbols(el.assetType.value);
    updateSymbolDefaults();
    handleAutoScanChange();
  });
  el.symbol.addEventListener("change", () => {
    updateSymbolDefaults();
    handleAutoScanChange();
  });
  el.tf.addEventListener("change", handleAutoScanChange);
  el.bars.addEventListener("change", handleAutoScanChange);
  el.startDate.addEventListener("change", handleAutoScanChange);
  el.endDate.addEventListener("change", handleAutoScanChange);
  el.themeSelect.addEventListener("change", updateThemeFromControls);
  el.candleUpColor.addEventListener("change", updateThemeFromControls);
  el.candleDownColor.addEventListener("change", updateThemeFromControls);
  el.refresh.addEventListener("click", loadScan);
  el.exportBtn.addEventListener("click", openExportModal);
  el.bulkExportBtn.addEventListener("click", openBulkExportModal);
  el.modalClose.addEventListener("click", () => el.exportModal.close());
  el.modalCancel.addEventListener("click", () => el.exportModal.close());
  el.modalExport.addEventListener("click", doExport);
  el.bulkModalClose.addEventListener("click", () => el.bulkExportModal.close());
  el.bulkModalCancel.addEventListener("click", () => el.bulkExportModal.close());
  el.bulkModalExport.addEventListener("click", doBulkExport);
  el.bulkSymbolsAll.addEventListener("click", () => {
    setBulkSymbolsChecked(true);
    loadExportRange("bulk");
  });
  el.bulkSymbolsNone.addEventListener("click", () => {
    setBulkSymbolsChecked(false);
    loadExportRange("bulk");
  });

  renderParamControls();
  applyStrategyDefaults({ resetSelection: true });
  updateSymbolDefaults();
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
      input.addEventListener("change", handleParamChange);
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
    input.addEventListener("change", handleParamChange);
    label.appendChild(input);
    el.params.appendChild(label);
  });

  applyBarsDefaultForStrategy();
}

function handleParamChange() {
  markViewChanged();
  updateBarsControlLabel();
  scheduleLoadScan();
}

function updateBarsControlLabel() {
  el.barsCaption.textContent = "Bars";
}

function applyBarsDefaultForStrategy() {
  el.bars.value = state.config.defaultBars;
  updateBarsControlLabel();
}

function applyStrategyDefaults({ resetSelection = false } = {}) {
  if (resetSelection) applyStrategySelectionDefaults(el.strategy.value);
  el.barsCaption.textContent = "Bars";
}

function collectParams() {
  const params = {};
  el.params.querySelectorAll("[data-param]").forEach((input) => {
    params[input.dataset.param] = input.type === "checkbox" ? String(input.checked) : input.value;
  });
  return params;
}

function appendDateRange(query) {
  if (el.startDate.value && el.endDate.value) {
    query.set("start_date", el.startDate.value);
    query.set("end_date", el.endDate.value);
  }
}

function appendDateRangeFromInputs(query, startInput, endInput) {
  if (startInput.value && endInput.value) {
    query.set("start_date", startInput.value);
    query.set("end_date", endInput.value);
  }
}

function currentExportParams() {
  return { params: collectParams(), bars: el.bars.value, exportTf: el.tf.value };
}

function setExportRangeFallback(kind, message) {
  const startInput = kind === "bulk" ? el.bulkExportStartDate : el.exportStartDate;
  const endInput = kind === "bulk" ? el.bulkExportEndDate : el.exportEndDate;
  const note = kind === "bulk" ? el.bulkExportRangeNote : el.exportRangeNote;
  startInput.value = el.startDate.value || defaultStartDate();
  endInput.value = el.endDate.value || defaultEndDate();
  note.textContent = message || "Using current chart range. You can edit it before export.";
}

async function loadExportRange(kind) {
  const isBulk = kind === "bulk";
  const startInput = isBulk ? el.bulkExportStartDate : el.exportStartDate;
  const endInput = isBulk ? el.bulkExportEndDate : el.exportEndDate;
  const note = isBulk ? el.bulkExportRangeNote : el.exportRangeNote;
  const { params, bars, exportTf } = currentExportParams();
  const query = new URLSearchParams({
    strategy: el.strategy.value,
    symbol: el.symbol.value,
    tf: exportTf,
    bars,
    ...params,
  });
  if (isBulk) {
    const symbols = [...el.bulkSymbols.querySelectorAll("input[type=checkbox]:checked")]
      .map((cb) => cb.dataset.symbol)
      .join(",");
    if (symbols) query.set("symbols", symbols);
  }

  note.textContent = "Loading full available data range...";
  try {
    const response = await fetch(`/api/data-range?${query.toString()}`);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not load data range");
    if (!payload.available) {
      setExportRangeFallback(kind, "No complete DB range found. Using current chart range.");
      return;
    }
    startInput.value = payload.startDate;
    endInput.value = payload.endDate;
    note.textContent = `Default full range from local DB: ${payload.startDate} -> ${payload.endDate}. You can edit it.`;
  } catch (error) {
    setExportRangeFallback(kind, `Could not load full DB range. Using current chart range. ${error.message}`);
  }
}

function metaWindowText(meta) {
  if (meta?.rangeMode === "date") {
    return `${meta.startDate} -> ${meta.endDate}`;
  }
  return `${meta?.bars ?? 0} bars`;
}

async function loadScan() {
  if (state.isLoading) {
    state.queuedScan = true;
    setLoading(true, "Current scan is finishing. Latest selection will load next...");
    showWarning("A scan is already running. The dashboard will load the latest selection next.");
    return;
  }

  clearTimeout(state.scanTimer);
  clearError();
  clearWarning();
  state.queuedScan = false;
  updateRefreshButton();
  applyStrategyDefaults();
  const params = collectParams();
  const bars = el.bars.value;
  const query = new URLSearchParams({
    strategy: el.strategy.value,
    symbol: el.symbol.value,
    tf: el.tf.value,
    bars,
    ...params,
  });
  appendDateRange(query);

  const requestId = state.scanRequestId + 1;
  const requestVersion = state.viewVersion;
  state.scanRequestId = requestId;
  setLoading(true);

  try {
    const response = await fetch(`/api/scan?${query.toString()}`);
    const payload = await response.json();
    if (requestId !== state.scanRequestId || requestVersion !== state.viewVersion || state.queuedScan) return;
    if (!response.ok) throw new Error(payload.error || "Scan failed");
    renderPayload(payload);
  } catch (error) {
    if (requestId !== state.scanRequestId || requestVersion !== state.viewVersion || state.queuedScan) return;
    state.lastPayload = null;
    resetCharts();
    el.meta.textContent = `${el.strategy.options[el.strategy.selectedIndex]?.textContent || el.strategy.value} | ${el.symbol.value} | scan failed`;
    resetStats();
    showError(error.message);
  } finally {
    if (requestId === state.scanRequestId) {
      state.scanAbort = null;
      const shouldRunQueued = state.queuedScan;
      setLoading(false);
      if (shouldRunQueued) {
        state.queuedScan = false;
        window.setTimeout(() => loadScan(), 0);
      }
    }
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

function showWarning(message) {
  el.scanWarning.hidden = false;
  el.scanWarning.textContent = message;
}

function clearWarning() {
  el.scanWarning.hidden = true;
  el.scanWarning.textContent = "";
}

function resetStats() {
  el.statTotal.textContent = "0";
  el.statBuy.textContent = "0";
  el.statSell.textContent = "0";
  el.statLast.textContent = "-";
  renderSignalsTable([]);
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
  el.chartLegend.textContent = "";
}

function renderPayload(payload) {
  state.lastPayload = payload;
  clearWarning();
  resetCharts();
  el.meta.textContent = `${payload.meta.strategyLabel} | ${payload.meta.symbol} ${payload.meta.tf} | ${metaWindowText(payload.meta)}`;
  el.statTotal.textContent = payload.stats.total;
  el.statBuy.textContent = payload.stats.buy;
  el.statSell.textContent = payload.stats.sell;
  el.statLast.textContent = payload.stats.last;

  const priceChart = LightweightCharts.createChart(el.priceChart, {
    ...currentChartTheme(),
    width: el.priceChart.clientWidth,
    height: el.priceChart.clientHeight,
  });
  state.priceChart = priceChart;

  const candleSeries = priceChart.addCandlestickSeries(currentCandleOptions());
  candleSeries.setData(payload.candles);
  setSeriesMarkers(candleSeries, payload.markers);
  const candleByTime = new Map((payload.candles || []).map((candle) => [Number(candle.time), candle]));

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
  let selectedLegendText = "";
  priceChart.subscribeCrosshairMove((param) => {
    if (!param.time || !param.seriesData.has(candleSeries)) {
      el.chartLegend.textContent = selectedLegendText;
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

  priceChart.subscribeClick((param) => {
    if (!param || param.time == null) return;
    const time = Number(param.time);
    const candle = candleByTime.get(time);
    if (!candle) return;
    selectedLegendText = `${payload.meta.tf} ${fmtUtcMinute(time)} | ${fmtOhlc(candle)}`;
    el.chartLegend.textContent = selectedLegendText;
  });

  renderSignalsTable(payload.signals);
  priceChart.timeScale().fitContent();
}

function renderIndicatorPanels(panels, priceChart) {
  const views = [];
  panels.forEach((panel) => {
    const container = document.createElement("div");
    container.className = "chart indicator-chart";
    el.panelCharts.appendChild(container);

    const chart = LightweightCharts.createChart(container, {
      ...currentChartTheme(),
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
    const dataByTime = new Map((panel.data || []).map((point) => [point.time, point]));
    state.panelSeries.push({ chart, series, key: panel.key, dataByTime, container });

    priceChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (range) chart.timeScale().setVisibleLogicalRange(range);
    });
    views.push({ chart, series, key: panel.key, dataByTime, container });
  });
  return views;
}

function renderPanels(panels, priceChart) {
  renderIndicatorPanels(panels, priceChart);
}

function sortedMarkers(markers) {
  return (markers || []).slice().sort((a, b) => Number(a.time) - Number(b.time));
}

function setSeriesMarkers(series, markers) {
  series.setMarkers(sortedMarkers(markers));
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

async function downloadCsv(url, button, idleText, modal) {
  if (state.isExporting) return;
  state.isExporting = true;
  button.disabled = true;
  button.textContent = "Exporting...";
  clearError();
  showWarning("Exporting CSV with the selected full range. Please wait...");

  try {
    const response = await fetch(url);
    if (!response.ok) {
      const errorPayload = await response.json().catch(() => null);
      throw new Error(errorPayload?.error || "Export failed");
    }
    const blob = await response.blob();
    const filename = filenameFromDisposition(response.headers.get("Content-Disposition")) || "signals.csv";
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(objectUrl);
    if (modal?.open) modal.close();
    showWarning(`Export downloaded: ${filename}`);
  } catch (error) {
    clearWarning();
    showError(error.message);
  } finally {
    state.isExporting = false;
    button.disabled = false;
    button.textContent = idleText;
  }
}

function filenameFromDisposition(disposition) {
  const text = String(disposition || "");
  const match = text.match(/filename\*?=(?:UTF-8''|")?([^";]+)/i);
  return match ? decodeURIComponent(match[1].replace(/"$/, "")) : "";
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
  setExportRangeFallback("single", "Loading full available data range...");
  el.exportModal.showModal();
  loadExportRange("single");
}

async function doExport() {
  const cols = [...el.exportCols.querySelectorAll("input[type=checkbox]:checked")]
    .map((cb) => cb.dataset.col)
    .join(",");
  const { params, bars, exportTf } = currentExportParams();
  const query = new URLSearchParams({
    strategy: el.strategy.value,
    symbol: el.symbol.value,
    tf: exportTf,
    bars,
    cols,
    ...params,
  });
  appendDateRangeFromInputs(query, el.exportStartDate, el.exportEndDate);
  await downloadCsv(`/api/export?${query.toString()}`, el.modalExport, "Download CSV", el.exportModal);
}

function openBulkExportModal() {
  renderBulkSymbols();
  renderBulkColumns();
  setExportRangeFallback("bulk", "Loading full available data range...");
  el.bulkExportModal.showModal();
  loadExportRange("bulk");
}

function renderBulkSymbols() {
  el.bulkSymbols.replaceChildren();
  const assetFilter = el.assetType.value;
  state.config.symbols
    .filter((s) => !assetFilter || s.asset_type === assetFilter)
    .forEach((symbol) => {
      const label = document.createElement("label");
      label.className = "check-row";
      label.textContent = symbol.name;
      const input = document.createElement("input");
      input.type = "checkbox";
      input.dataset.symbol = symbol.name;
      input.checked = symbol.name === el.symbol.value;
      input.addEventListener("change", () => loadExportRange("bulk"));
      label.appendChild(input);
      el.bulkSymbols.appendChild(label);
    });
}

function renderBulkColumns() {
  const cols = BULK_EXPORT_COLUMNS[el.strategy.value] || BULK_EXPORT_COLUMNS.combo;
  el.bulkCols.replaceChildren();
  cols.forEach((col) => {
    const label = document.createElement("label");
    label.className = "check-row";
    label.textContent = col.label;
    const input = document.createElement("input");
    input.type = "checkbox";
    input.dataset.col = col.key;
    input.checked = col.checked;
    label.appendChild(input);
    el.bulkCols.appendChild(label);
  });
}

function setBulkSymbolsChecked(checked) {
  el.bulkSymbols.querySelectorAll("input[type=checkbox]").forEach((input) => {
    input.checked = checked;
  });
}

async function doBulkExport() {
  const symbols = [...el.bulkSymbols.querySelectorAll("input[type=checkbox]:checked")]
    .map((cb) => cb.dataset.symbol)
    .join(",");
  if (!symbols) {
    showError("Select at least one symbol for bulk export.");
    return;
  }

  const cols = [...el.bulkCols.querySelectorAll("input[type=checkbox]:checked")]
    .map((cb) => cb.dataset.col)
    .join(",");
  const { params, bars, exportTf } = currentExportParams();
  if (el.strategy.value === "combo") {
    delete params.X;
  }
  const query = new URLSearchParams({
    strategy: el.strategy.value,
    tf: exportTf,
    bars,
    symbols,
    cols,
    ...params,
  });
  appendDateRangeFromInputs(query, el.bulkExportStartDate, el.bulkExportEndDate);
  await downloadCsv(`/api/export/bulk?${query.toString()}`, el.bulkModalExport, "Download CSV", el.bulkExportModal);
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
