const state = {
  config: null,
  priceChart: null,
  panelCharts: [],
  panelSeries: [],
  markRefreshers: [],
  lastPayload: null,
  scanAbort: null,
  scanRequestId: 0,
  scanTimer: null,
  queuedScan: false,
  viewVersion: 0,
  isLoading: false,
  isExporting: false,
  pendingRefresh: false,
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

function isAiTrend() {
  return el.strategy.value === "ai_trend";
}

function isTrendEntryStrategy() {
  return ["ai_trend", "knn_combo"].includes(el.strategy.value);
}

function updateRefreshButton() {
  if (state.isLoading) {
    el.refresh.textContent = state.queuedScan ? "Queued..." : "Loading...";
    el.refresh.disabled = true;
    return;
  }
  el.refresh.disabled = false;
  el.refresh.textContent = state.pendingRefresh ? "Apply" : "Refresh";
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

function markPendingRefresh(message = "Settings changed. Click Apply to refresh the preview.") {
  state.pendingRefresh = true;
  updateRefreshButton();
  showWarning(message);
}

function scheduleLoadScan(delay = 300) {
  clearTimeout(state.scanTimer);
  state.scanTimer = setTimeout(() => {
    loadScan();
  }, delay);
}

function handleAutoScanChange(message) {
  markViewChanged();
  if (isTrendEntryStrategy()) {
    markPendingRefresh(message);
    return;
  }
  scheduleLoadScan();
}

const TF_MINUTES = {
  M5: 5,
  M10: 10,
  M15: 15,
  M20: 20,
  M30: 30,
  M45: 45,
  M90: 90,
  H1: 60,
  H2: 120,
  H3: 180,
  H4: 240,
  H6: 360,
  H8: 480,
  D1: 1440,
  W: 10080,
};

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
  atr5: "ATR5",
  atr14: "ATR14",
  sl_dow: "SL_Dow(3,5,0.5)",
  ma: "MA",
  macd_h: "MACD-H",
  fast_ma: "Fast MA",
  slow_ma: "Slow MA",
  ma_gap_atr: "Gap/ATR",
  raw_signal: "Raw",
  filter_reason: "Filter",
  htf_trend: "HTF Trend",
  htf_slope: "HTF Slope",
  htf_close: "HTF Close",
  trend_bias: "KNN Bias",
  trend_bias_label: "KNN Trend",
  trend_close_time: "Trend Close",
  trend_ai_knn: "Trend KNN",
  trend_ai_avg: "Trend Avg",
  trend_ai_direction: "Trend Direction",
  open: "Open",
  high: "High",
  low: "Low",
  volume: "Volume",
  signal_reason: "Reason",
  entry_time: "Entry Time",
  entry_price: "Entry",
  sl_price: "SL",
  tp_price: "TP",
  risk_reward: "R:R",
  h3_bias: "Trend Bias",
  h3_bias_segment: "Trend Segment",
  h3_close_time: "H3 Close Time",
  h3_bias_started_at: "Trend Started",
  h3_bias_started_close_time: "Trend Start Close",
  h3_ai_knn: "Trend KNN",
  h3_ai_avg: "Trend Avg",
  h3_ai_direction: "Trend Direction",
  ema_fast: "EMA Fast",
  ema_slow: "EMA Slow",
  prev_close: "Prev Close",
  prev_ema_fast: "Prev EMA Fast",
  prev_ema_slow: "Prev EMA Slow",
  ema_gap: "EMA Gap",
  ema_gap_pct: "EMA Gap %",
  close: "Close",
  m45_close_time: "M45 Close Time",
  dow_pivot_type: "Dow Pivot Type",
  dow_pivot_price: "Dow Pivot Price",
  dow_label: "Dow Label",
};

const TEXT_COLS = new Set([
  "bartime",
  "side",
  "reason",
  "signal_reason",
  "filter_reason",
  "htf_trend",
  "trend_bias_label",
  "trend_close_time",
  "entry_time",
  "m45_close_time",
  "h3_close_time",
  "h3_bias_started_at",
  "h3_bias_started_close_time",
  "dow_pivot_type",
  "dow_label",
]);

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
    { key: "raw_signal", label: "Raw Signal", checked: false },
    { key: "filter_reason", label: "Filter", checked: false },
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
    { key: "ma_gap_atr", label: "Gap/ATR", checked: false },
  ],
  ai_trend: [
    { key: "bartime", label: "Bar Time", checked: true },
    { key: "side", label: "Side", checked: false },
    { key: "signal", label: "Signal", checked: true },
    { key: "open", label: "Open", checked: false },
    { key: "high", label: "High", checked: false },
    { key: "low", label: "Low", checked: false },
    { key: "close", label: "Close", checked: false },
    { key: "volume", label: "Volume", checked: false },
    { key: "ema_fast", label: "EMA Fast", checked: false },
    { key: "ema_slow", label: "EMA Slow", checked: false },
    { key: "prev_close", label: "Prev Close", checked: false },
    { key: "prev_ema_fast", label: "Prev EMA Fast", checked: false },
    { key: "prev_ema_slow", label: "Prev EMA Slow", checked: false },
    { key: "ema_gap", label: "EMA Gap", checked: false },
    { key: "ema_gap_pct", label: "EMA Gap %", checked: false },
    { key: "macd_h", label: "MACD-H", checked: false },
    { key: "atr", label: "ATR", checked: false },
    { key: "atr5", label: "ATR5", checked: true },
    { key: "atr14", label: "ATR14", checked: true },
    { key: "m45_close_time", label: "M45 Close Time", checked: false },
    { key: "dow_pivot_type", label: "Dow Pivot Type", checked: false },
    { key: "dow_pivot_price", label: "Dow Pivot Price", checked: false },
    { key: "dow_label", label: "Dow Label", checked: false },
    { key: "h3_bias", label: "Trend Bias", checked: false },
    { key: "h3_bias_segment", label: "Trend Segment", checked: false },
    { key: "h3_close_time", label: "H3 Close Time", checked: false },
    { key: "h3_bias_started_at", label: "Trend Started", checked: false },
    { key: "h3_bias_started_close_time", label: "Trend Start Close", checked: false },
    { key: "h3_ai_knn", label: "Trend KNN", checked: false },
    { key: "h3_ai_avg", label: "Trend Avg", checked: false },
    { key: "h3_ai_direction", label: "Trend Direction", checked: false },
    { key: "signal_reason", label: "Reason", checked: false },
    { key: "entry_time", label: "Entry Time", checked: false },
    { key: "entry_price", label: "Entry", checked: false },
    { key: "sl_dow", label: "SL_Dow(3,5,0.5)", checked: true },
    { key: "sl_price", label: "SL", checked: false },
    { key: "tp_price", label: "TP", checked: false },
    { key: "risk_reward", label: "R:R", checked: false },
  ],
  knn_combo: [
    { key: "bartime", label: "Bar Time", checked: true },
    { key: "side", label: "Side", checked: true },
    { key: "signal", label: "Signal", checked: false },
    { key: "signal_reason", label: "Reason", checked: false },
    { key: "raw_signal", label: "Raw Signal", checked: false },
    { key: "filter_reason", label: "Filter", checked: false },
    { key: "trend_bias", label: "KNN Bias", checked: false },
    { key: "trend_bias_label", label: "KNN Trend", checked: false },
    { key: "trend_close_time", label: "Trend Close", checked: false },
    { key: "trend_ai_knn", label: "Trend KNN", checked: false },
    { key: "trend_ai_avg", label: "Trend Avg", checked: false },
    { key: "ma", label: "MA", checked: false },
    { key: "macd_h", label: "MACD-H", checked: false },
    { key: "atr", label: "ATR", checked: false },
    { key: "open", label: "Open", checked: false },
    { key: "high", label: "High", checked: false },
    { key: "low", label: "Low", checked: false },
    { key: "close", label: "Close", checked: false },
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
    { key: "raw_signal", label: "Raw Signal", checked: false },
    { key: "filter_reason", label: "Filter", checked: false },
  ],
  ma_cross: [
    { key: "atr", label: "ATR", checked: true },
    { key: "fast_ma", label: "Fast MA", checked: false },
    { key: "slow_ma", label: "Slow MA", checked: false },
    { key: "ma_gap_atr", label: "Gap/ATR", checked: false },
    { key: "open", label: "Open", checked: false },
    { key: "high", label: "High", checked: false },
    { key: "low", label: "Low", checked: false },
    { key: "close", label: "Close", checked: false },
    { key: "signal_reason", label: "Reason", checked: false },
  ],
  ai_trend: [
    { key: "sl_dow", label: "SL_Dow(3,5,0.5)", checked: true },
    { key: "atr5", label: "ATR5", checked: true },
    { key: "atr14", label: "ATR14", checked: true },
    { key: "entry_price", label: "Entry", checked: false },
    { key: "sl_price", label: "SL", checked: false },
    { key: "tp_price", label: "TP", checked: false },
    { key: "risk_reward", label: "R:R", checked: false },
    { key: "h3_bias", label: "Trend Bias", checked: false },
    { key: "h3_ai_knn", label: "Trend KNN", checked: false },
    { key: "h3_ai_avg", label: "Trend Avg", checked: false },
    { key: "ema_fast", label: "EMA Fast", checked: false },
    { key: "ema_slow", label: "EMA Slow", checked: false },
    { key: "macd_h", label: "MACD-H", checked: false },
    { key: "atr", label: "ATR", checked: false },
    { key: "open", label: "Open", checked: false },
    { key: "high", label: "High", checked: false },
    { key: "low", label: "Low", checked: false },
    { key: "close", label: "Close", checked: false },
    { key: "signal_reason", label: "Reason", checked: false },
  ],
  knn_combo: [
    { key: "trend_bias", label: "KNN Bias", checked: true },
    { key: "trend_ai_knn", label: "Trend KNN", checked: false },
    { key: "trend_ai_avg", label: "Trend Avg", checked: false },
    { key: "ma", label: "MA", checked: false },
    { key: "macd_h", label: "MACD-H", checked: false },
    { key: "atr", label: "ATR", checked: true },
    { key: "open", label: "Open", checked: false },
    { key: "high", label: "High", checked: false },
    { key: "low", label: "Low", checked: false },
    { key: "close", label: "Close", checked: false },
    { key: "signal_reason", label: "Reason", checked: false },
    { key: "filter_reason", label: "Filter", checked: false },
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

function fmtBarOhlc(label, time, candle) {
  return `${label} ${fmtUtcMinute(time)} | ${fmtOhlc(candle)}`;
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
  return {
    symbol: defaults.symbol || state.config.defaultSymbol,
    tf: defaults.tf || state.config.defaultTf,
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
  const defaults = strategyDefaults(strategy);
  if (defaults.symbol && symbolMeta[defaults.symbol] && ![...el.symbol.options].some((option) => option.value === defaults.symbol)) {
    el.assetType.value = "";
    populateSymbols("", defaults.symbol);
  }
  selectOptionIfPresent(el.symbol, defaults.symbol);
  selectOptionIfPresent(el.tf, defaults.tf);
}

function populateSymbols(assetFilter, selectedValue = el.symbol.value || strategyDefaults().symbol) {
  el.symbol.replaceChildren();
  state.config.symbols
    .filter((s) => !assetFilter || s.asset_type === assetFilter)
    .forEach((s) => createOption(el.symbol, s.name, s.name, selectedValue));
}

function updateXDefault() {
  if (el.strategy.value !== "combo") return;
  const meta = symbolMeta[el.symbol.value];
  if (!meta) return;
  const xInput = el.params.querySelector('[data-param="X"]');
  if (xInput && meta.x != null) xInput.value = meta.x > 0 ? meta.x : "";
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

  state.config.timeframes.forEach((tf) => createOption(el.tf, tf, tf, strategyDefaults().tf));
  el.bars.value = state.config.defaultBars;
  el.startDate.value = defaultStartDate();
  el.endDate.value = defaultEndDate();

  el.strategy.addEventListener("change", () => {
    markViewChanged();
    renderParamControls();
    applyStrategyDefaults({ resetSelection: true });
    updateXDefault();
    if (isTrendEntryStrategy()) {
      state.lastPayload = null;
      resetCharts();
      resetStats();
      clearError();
      const label = state.config.strategies[el.strategy.value]?.label || el.strategy.value;
      el.meta.textContent = `${label} | ${el.symbol.value} | click Apply to load preview`;
      markPendingRefresh(`${label} selected. Click Apply to load the preview.`);
      return;
    }
    loadScan();
  });
  el.assetType.addEventListener("change", () => {
    populateSymbols(el.assetType.value);
    updateXDefault();
    handleAutoScanChange("Asset filter changed. Click Apply to refresh the preview.");
  });
  el.symbol.addEventListener("change", () => {
    updateXDefault();
    handleAutoScanChange("Symbol changed. Click Apply to refresh the preview.");
  });
  el.tf.addEventListener("change", handleTopTfChange);
  el.bars.addEventListener("change", () => handleAutoScanChange("Bar count changed. Click Apply to refresh the preview."));
  el.startDate.addEventListener("change", () => handleAutoScanChange("Date range changed. Click Apply to refresh the preview."));
  el.endDate.addEventListener("change", () => handleAutoScanChange("Date range changed. Click Apply to refresh the preview."));
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
  updateXDefault();
  await loadScan();
}

function renderParamControls() {
  const strategy = el.strategy.value;
  const spec = state.config.strategies[strategy];
  el.params.replaceChildren();

  spec.fields.forEach((field) => {
    if (hiddenParamField(strategy, field.key)) return;
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

  applyBarsDefaultForStrategy(strategy, spec);
}

function handleParamChange(event) {
  const key = event.currentTarget?.dataset?.param || "parameter";
  markViewChanged();
  if (isTrendEntryStrategy()) {
    if (key === "ENTRY_TF") syncTopTfWithAiEntryParam();
    markPendingRefresh(`${paramLabel(key)} changed. Click Apply to refresh the preview.`);
    return;
  }
  updateBarsControlLabel();
  scheduleLoadScan();
}

function handleTopTfChange() {
  markViewChanged();
  if (isTrendEntryStrategy()) {
    syncAiEntryParamWithTopTf();
    markPendingRefresh("Entry TF changed. Click Apply to refresh the preview.");
    return;
  }
  scheduleLoadScan();
}

function paramLabel(key) {
  const input = el.params.querySelector(`[data-param="${key}"]`);
  const label = input?.closest("label");
  return label?.childNodes?.[0]?.textContent?.trim() || key;
}

function syncTopTfWithAiEntryParam() {
  if (!isTrendEntryStrategy()) return;
  const entryInput = el.params.querySelector('[data-param="ENTRY_TF"]');
  const spec = state.config.strategies[el.strategy.value];
  const entryTf = entryInput?.value || spec?.defaults?.ENTRY_TF || "M45";
  if ([...el.tf.options].some((option) => option.value === entryTf)) {
    el.tf.value = entryTf;
  }
}

function syncAiEntryParamWithTopTf() {
  const entryInput = el.params.querySelector('[data-param="ENTRY_TF"]');
  if (!entryInput) return;
  const allowed = [...entryInput.options].some((option) => option.value === el.tf.value);
  if (allowed) {
    entryInput.value = el.tf.value;
  } else {
    syncTopTfWithAiEntryParam();
  }
}

function hiddenParamField(strategy, key) {
  if (strategy === "combo" && (key.startsWith("HTF_") || key === "SHOW_HTF_TREND" || key === "SHOW_FILTERED_SIGNALS")) return true;
  if (["ai_trend", "knn_combo"].includes(strategy) && (key === "TREND_BARS" || key === "ENTRY_BARS")) return true;
  return false;
}

function updateBarsControlLabel() {
  const params = collectParams();
  if (isTrendEntryStrategy()) {
    el.barsCaption.textContent = "Trend Bars";
    return;
  }
  if (isComboHtfEnabled(params)) {
    el.barsCaption.textContent = "HTF Bars";
    return;
  }
  el.barsCaption.textContent = "Bars";
}

function applyBarsDefaultForStrategy(strategy, spec) {
  if (["ai_trend", "knn_combo"].includes(strategy)) {
    el.bars.value = spec.defaults.TREND_BARS || 500;
  } else if (strategy === "combo") {
    el.bars.value = spec.defaults.HTF_TREND_ENABLED
      ? spec.defaults.HTF_BARS || state.config.defaultBars
      : state.config.defaultBars;
  } else {
    el.bars.value = state.config.defaultBars;
  }
  updateBarsControlLabel();
}

function applyStrategyDefaults({ resetSelection = false } = {}) {
  if (el.strategy.value === "combo") {
    if (resetSelection) applyStrategySelectionDefaults("combo");
    el.barsCaption.textContent = "Bars";
    return;
  }
  if (!isTrendEntryStrategy()) return;
  el.barsCaption.textContent = "Trend Bars";
  syncTopTfWithAiEntryParam();
  if (!el.bars.value) {
    el.bars.value = state.config.strategies[el.strategy.value]?.defaults?.TREND_BARS || 500;
  }
}

function collectParams() {
  const params = {};
  el.params.querySelectorAll("[data-param]").forEach((input) => {
    params[input.dataset.param] = input.type === "checkbox" ? String(input.checked) : input.value;
  });
  return params;
}

function isComboHtfEnabled(params) {
  return false;
}

function calcEntryBarsForHtfBars(htfBars, htfTf, entryTf) {
  const trendMinutes = TF_MINUTES[String(htfTf || "D1").toUpperCase()] || TF_MINUTES.D1;
  const entryMinutes = TF_MINUTES[String(entryTf || "").toUpperCase()] || trendMinutes;
  const rawBars = Math.ceil(Number(htfBars || 50) * trendMinutes / entryMinutes);
  return Math.max(50, Math.min(20000, rawBars));
}

function syncAiTrendEntryBars(params) {
  if (!isTrendEntryStrategy()) return el.bars.value;
  const spec = state.config.strategies[el.strategy.value];
  const trendBars = Math.max(50, Math.min(20000, Number(el.bars.value || params.TREND_BARS || spec?.defaults?.TREND_BARS || 500)));
  const entryBars = calcEntryBarsForHtfBars(trendBars, params.TREND_TF || "H3", params.ENTRY_TF || spec?.defaults?.ENTRY_TF || "M45");
  params.TREND_BARS = String(trendBars);
  params.ENTRY_BARS = String(entryBars);
  params.AUTO_ENTRY_BARS = "true";
  const trendInput = el.params.querySelector('[data-param="TREND_BARS"]');
  const entryInput = el.params.querySelector('[data-param="ENTRY_BARS"]');
  if (trendInput) trendInput.value = String(trendBars);
  if (entryInput) entryInput.value = String(entryBars);
  return entryBars;
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
  const params = collectParams();
  let bars = el.bars.value;
  let exportTf = el.tf.value;
  if (isComboHtfEnabled(params)) {
    const htfBars = Math.max(50, Math.min(20000, Number(el.bars.value || 100)));
    params.HTF_BARS = String(htfBars);
    bars = String(calcEntryBarsForHtfBars(htfBars, params.HTF_TF || "D1", el.tf.value));
  } else if (isTrendEntryStrategy()) {
    bars = String(syncAiTrendEntryBars(params));
    exportTf = params.ENTRY_TF || el.tf.value;
  }
  return { params, bars, exportTf };
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
  state.pendingRefresh = false;
  updateRefreshButton();
  applyStrategyDefaults();
  const params = collectParams();
  let bars = el.bars.value;
  if (isComboHtfEnabled(params)) {
    const htfBars = Math.max(50, Math.min(20000, Number(el.bars.value || 100)));
    params.HTF_BARS = String(htfBars);
    bars = String(calcEntryBarsForHtfBars(htfBars, params.HTF_TF || "D1", el.tf.value));
  } else if (isTrendEntryStrategy()) {
    bars = String(syncAiTrendEntryBars(params));
  }
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
  state.markRefreshers = [];
  if (state.priceChart) {
    state.priceChart.remove();
    state.priceChart = null;
  }
  state.panelCharts.forEach((chart) => chart.remove());
  state.panelCharts = [];
  state.panelSeries = [];
  el.panelCharts.replaceChildren();
  el.priceChart.classList.remove("ai-trend-h3-chart");
  el.priceChart.classList.remove("combo-trend-chart");
  el.chartLegend.textContent = "";
}

function renderPayload(payload) {
  state.lastPayload = payload;
  if (payload.meta?.previewWarning) {
    showWarning(payload.meta.previewWarning);
  } else {
    clearWarning();
  }
  if (payload.layout === "combo_mtf") {
    renderComboMtfPayload(payload);
    return;
  }
  if (payload.layout === "ai_trend_mtf") {
    renderAiTrendPayload(payload);
    return;
  }
  if (payload.layout === "knn_combo_mtf") {
    renderKnnComboPayload(payload);
    return;
  }
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

function renderComboMtfPayload(payload) {
  resetCharts();
  el.priceChart.classList.add("combo-trend-chart");
  const stats = payload.stats || {};
  el.meta.textContent = `${payload.meta.strategyLabel} | ${payload.meta.symbol} ${payload.meta.trendTf}/${payload.meta.entryTf} | ${metaWindowText(payload.meta)} | ${payload.meta.trendBars} ${payload.meta.trendTf} bars, ${payload.meta.entryBars} ${payload.meta.entryTf} bars | raw ${stats.rawTotal ?? 0}, valid ${stats.total ?? 0}, filtered ${stats.filteredTotal ?? 0}`;
  el.statTotal.textContent = stats.total ?? 0;
  el.statBuy.textContent = stats.buy ?? 0;
  el.statSell.textContent = stats.sell ?? 0;
  el.statLast.textContent = stats.last ?? "-";
  el.chartLegend.textContent = payload.charts.trend.label;

  const trendChart = createPriceChart(el.priceChart, payload.charts.trend);
  state.priceChart = trendChart.chart;

  const entryContainer = document.createElement("div");
  entryContainer.className = "chart combo-entry-chart";
  const entryLabel = document.createElement("div");
  entryLabel.className = "ai-chart-label";
  entryLabel.textContent = payload.charts.entry.label;
  entryLabel.dataset.baseLabel = payload.charts.entry.label;
  entryContainer.appendChild(entryLabel);
  el.panelCharts.appendChild(entryContainer);

  const entryChart = createPriceChart(entryContainer, payload.charts.entry);
  state.panelCharts.push(entryChart.chart);
  const entryPanelViews = renderIndicatorPanels(payload.charts.entry.panels || [], entryChart.chart);
  setupComboMtfLinking(payload, trendChart, entryChart, entryLabel, entryPanelViews);

  trendChart.chart.timeScale().fitContent();
  entryChart.chart.timeScale().fitContent();
  renderSignalsTable(payload.signals);
}

function renderKnnComboPayload(payload) {
  renderComboMtfPayload(payload);
}

function renderAiTrendPayload(payload) {
  resetCharts();
  el.priceChart.classList.add("ai-trend-h3-chart");
  el.meta.textContent = `${payload.meta.strategyLabel} | ${payload.meta.symbol} ${payload.meta.trendTf}/${payload.meta.entryTf} | ${metaWindowText(payload.meta)} | ${payload.meta.trendTf} ${payload.meta.trendBars} bars, ${payload.meta.entryTf} ${payload.meta.entryBars} bars`;
  el.statTotal.textContent = payload.stats.total;
  el.statBuy.textContent = payload.stats.buy;
  el.statSell.textContent = payload.stats.sell;
  el.statLast.textContent = payload.stats.last;
  el.chartLegend.textContent = payload.charts.trend.label;

  const trendChart = createPriceChart(el.priceChart, payload.charts.trend);
  state.priceChart = trendChart.chart;

  const entryContainer = document.createElement("div");
  entryContainer.className = "chart ai-entry-chart";
  const entryLabel = document.createElement("div");
  entryLabel.className = "ai-chart-label";
  entryLabel.textContent = payload.charts.entry.label;
  entryLabel.dataset.baseLabel = payload.charts.entry.label;
  entryContainer.appendChild(entryLabel);
  el.panelCharts.appendChild(entryContainer);

  const entryChart = createPriceChart(entryContainer, payload.charts.entry);
  state.panelCharts.push(entryChart.chart);
  const entryPanelViews = renderIndicatorPanels(payload.charts.entry.panels || [], entryChart.chart);
  setupAiTrendLinking(payload, trendChart, entryChart, entryLabel, entryPanelViews);

  trendChart.chart.timeScale().fitContent();
  entryChart.chart.timeScale().fitContent();
  renderSignalsTable(payload.signals);
}

function createPriceChart(container, chartPayload) {
  const chart = LightweightCharts.createChart(container, {
    ...currentChartTheme(),
    width: container.clientWidth,
    height: container.clientHeight,
  });
  const candleSeries = chart.addCandlestickSeries(currentCandleOptions());
  candleSeries.setData(chartPayload.candles);
  const baseMarkers = (chartPayload.markers || []).sort((a, b) => a.time - b.time);
  setSeriesMarkers(candleSeries, baseMarkers);
  const candleByTime = new Map((chartPayload.candles || []).map((candle) => [candle.time, candle]));
  const candleTimes = (chartPayload.candles || []).map((candle) => candle.time).sort((a, b) => a - b);

  (chartPayload.overlays || []).forEach((line) => {
    const series = chart.addLineSeries({
      color: line.color,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      title: line.label,
    });
    series.setData(line.data);
  });
  (chartPayload.segments || []).forEach((segment) => {
    const series = chart.addLineSeries({
      color: segment.color || "#e5e7eb",
      lineWidth: segment.width || 2,
      lineStyle: lineStyle(segment.style),
      priceLineVisible: false,
      lastValueVisible: false,
      title: "",
    });
    series.setData(segment.data || []);
  });
  return { chart, container, candleSeries, candleByTime, candleTimes, baseMarkers };
}

function nearestChartTime(times, target) {
  if (!times.length) return null;
  if (times.includes(target)) return target;
  let best = times[0];
  let bestDist = Math.abs(best - target);
  for (const t of times) {
    const dist = Math.abs(t - target);
    if (dist < bestDist) {
      best = t;
      bestDist = dist;
    }
  }
  return best;
}

function firstChartTimeAtOrAfter(times, target) {
  for (const time of times) {
    if (time >= target) return time;
  }
  return null;
}

function lastChartTimeAtOrBefore(times, target) {
  for (let i = times.length - 1; i >= 0; i -= 1) {
    if (times[i] <= target) return times[i];
  }
  return null;
}

function sortedMarkers(markers) {
  return (markers || []).slice().sort((a, b) => Number(a.time) - Number(b.time));
}

function setSeriesMarkers(series, markers) {
  series.setMarkers(sortedMarkers(markers));
}

function createMarkLine(container) {
  const line = document.createElement("div");
  line.className = "ai-mark-line";
  line.hidden = true;
  container.appendChild(line);
  return line;
}

function setMarkLine(view, line, time) {
  if (time == null) {
    line.hidden = true;
    return;
  }
  const coordinate = view.chart.timeScale().timeToCoordinate(time);
  if (coordinate == null) {
    line.hidden = true;
    return;
  }
  line.hidden = false;
  line.style.left = `${Math.round(coordinate)}px`;
}

function setupAiTrendLinking(payload, trendView, entryView, entryLabel, entryPanels = []) {
  const trendTf = payload.meta?.trendTf || "Trend";
  const entryTf = payload.meta?.entryTf || "Entry";
  const trendSeconds = Math.max(1, Number(payload.meta?.trendTfMinutes || 180)) * 60;
  const entrySeconds = Math.max(1, Number(payload.meta?.entryTfMinutes || 45)) * 60;
  const contextBars = Math.max(6, Number(payload.params?.M45_CONTEXT_BARS || 45));
  const trendMarkLine = createMarkLine(trendView.container);
  const entryMarkLine = createMarkLine(entryView.container);
  const entryPanelMarkLines = entryPanels.map((view) => createMarkLine(view.container));
  let markedH3Time = null;
  let markedEntryTime = null;

  const refreshMarkLines = () => {
    setMarkLine(trendView, trendMarkLine, markedH3Time);
    setMarkLine(entryView, entryMarkLine, markedEntryTime);
    entryPanels.forEach((view, index) => setMarkLine(view, entryPanelMarkLines[index], markedEntryTime));
  };
  state.markRefreshers.push(refreshMarkLines);
  trendView.chart.timeScale().subscribeVisibleLogicalRangeChange(refreshMarkLines);
  entryView.chart.timeScale().subscribeVisibleLogicalRangeChange(refreshMarkLines);
  entryPanels.forEach((view) => view.chart.timeScale().subscribeVisibleLogicalRangeChange(refreshMarkLines));

  const setEntryPanelCrosshairs = (time) => {
    entryPanels.forEach((view) => {
      const point = view.dataByTime.get(time);
      if (point) {
        view.chart.setCrosshairPosition(point.value, time, view.series);
      } else {
        view.chart.clearCrosshairPosition();
      }
    });
  };

  const clearEntryPanelCrosshairs = () => {
    entryPanels.forEach((view) => view.chart.clearCrosshairPosition());
  };

  const setWindowAround = (focusTime) => {
    const index = entryView.candleTimes.indexOf(focusTime);
    if (index < 0) return;
    const beforeBars = Math.max(1, Math.floor(contextBars / 3));
    const maxFrom = Math.max(0, entryView.candleTimes.length - contextBars);
    const fromIndex = Math.max(0, Math.min(index - beforeBars, maxFrom));
    const toIndex = Math.min(entryView.candleTimes.length - 1, fromIndex + contextBars - 1);
    entryView.chart.timeScale().setVisibleRange({
      from: entryView.candleTimes[fromIndex],
      to: entryView.candleTimes[toIndex],
    });
  };

  const clearMark = () => {
    markedH3Time = null;
    markedEntryTime = null;
    setSeriesMarkers(trendView.candleSeries, trendView.baseMarkers);
    setSeriesMarkers(entryView.candleSeries, entryView.baseMarkers);
    refreshMarkLines();
    trendView.chart.clearCrosshairPosition();
    entryView.chart.clearCrosshairPosition();
    clearEntryPanelCrosshairs();
    entryLabel.textContent = entryLabel.dataset.baseLabel;
    el.chartLegend.textContent = payload.charts.trend.label;
  };

  const manualMarkers = (time, label) => ([
    {
      time,
      position: "aboveBar",
      color: "#facc15",
      shape: "square",
      text: label,
    },
    {
      time,
      position: "belowBar",
      color: "#facc15",
      shape: "circle",
      text: "MARK",
    },
  ]);

  const markH3Bar = (rawH3Time) => {
    const h3Time = nearestChartTime(trendView.candleTimes, rawH3Time);
    if (h3Time == null) return;
    const trendCandle = trendView.candleByTime.get(h3Time);
    if (!trendCandle) return;
    const trendCloseTime = h3Time + trendSeconds;
    const candidateFocusTime = firstChartTimeAtOrAfter(entryView.candleTimes, trendCloseTime);
    const focusTime = candidateFocusTime != null && candidateFocusTime <= trendCloseTime + entrySeconds
      ? candidateFocusTime
      : null;
    const entryCandle = focusTime == null ? null : entryView.candleByTime.get(focusTime);
    markedH3Time = h3Time;
    markedEntryTime = focusTime;

    setSeriesMarkers(trendView.candleSeries, [
      ...trendView.baseMarkers,
      ...manualMarkers(h3Time, `${trendTf} MARK`),
    ]);
    trendView.chart.setCrosshairPosition(trendCandle.close, h3Time, trendView.candleSeries);

    setSeriesMarkers(entryView.candleSeries, entryCandle ? [
      ...entryView.baseMarkers,
      ...manualMarkers(focusTime, `${trendTf} close`),
    ] : entryView.baseMarkers);

    if (entryCandle) {
      entryView.chart.setCrosshairPosition(entryCandle.close, focusTime, entryView.candleSeries);
      setEntryPanelCrosshairs(focusTime);
      setWindowAround(focusTime);
    } else {
      entryView.chart.clearCrosshairPosition();
      clearEntryPanelCrosshairs();
    }

    const focusText = focusTime == null ? `no ${entryTf} bar` : fmtUtcMinute(focusTime);
    const entryOhlcText = entryCandle ? ` | ${fmtOhlc(entryCandle)}` : "";
    entryLabel.textContent = `${entryLabel.dataset.baseLabel} | ${trendTf} mark ${fmtUtcMinute(h3Time)} -> ${entryTf} ${focusText}${entryOhlcText} | ${contextBars} bars, 1/3 before`;
    el.chartLegend.textContent = `${payload.charts.trend.label} | marked ${fmtBarOhlc(trendTf, h3Time, trendCandle)} | ${trendTf} close ${fmtUtcMinute(trendCloseTime)}`;
    requestAnimationFrame(refreshMarkLines);
  };

  const markEntryBar = (rawEntryTime) => {
    const focusTime = nearestChartTime(entryView.candleTimes, rawEntryTime);
    if (focusTime == null) return;
    const entryCandle = entryView.candleByTime.get(focusTime);
    if (!entryCandle) return;

    markedH3Time = null;
    markedEntryTime = focusTime;
    setSeriesMarkers(trendView.candleSeries, trendView.baseMarkers);
    trendView.chart.clearCrosshairPosition();
    setSeriesMarkers(entryView.candleSeries, [
      ...entryView.baseMarkers,
      ...manualMarkers(focusTime, `${entryTf} MARK`),
    ]);
    entryView.chart.setCrosshairPosition(entryCandle.close, focusTime, entryView.candleSeries);
    setEntryPanelCrosshairs(focusTime);
    entryLabel.textContent = `${entryLabel.dataset.baseLabel} | marked ${fmtBarOhlc(entryTf, focusTime, entryCandle)}`;
    el.chartLegend.textContent = `${payload.charts.entry.label} | marked ${fmtBarOhlc(entryTf, focusTime, entryCandle)}`;
    requestAnimationFrame(refreshMarkLines);
  };

  trendView.chart.subscribeClick((param) => {
    if (!param || param.time == null) return;
    markH3Bar(Number(param.time));
  });
  entryView.chart.subscribeClick((param) => {
    if (!param || param.time == null) return;
    markEntryBar(Number(param.time));
  });
  if (typeof trendView.chart.subscribeDblClick === "function") {
    trendView.chart.subscribeDblClick(clearMark);
  }
  if (typeof entryView.chart.subscribeDblClick === "function") {
    entryView.chart.subscribeDblClick(clearMark);
  }
  trendView.container.addEventListener("dblclick", clearMark);
  entryView.container.addEventListener("dblclick", clearMark);
}

function setupComboMtfLinking(payload, trendView, entryView, entryLabel, entryPanels = []) {
  const trendTf = payload.meta?.trendTf || "HTF";
  const entryTf = payload.meta?.entryTf || "Entry";
  const trendSeconds = Math.max(1, Number(payload.meta?.trendTfMinutes || 1440)) * 60;
  const trendByEntry = new Map((payload.entryTrendLinks || []).map((link) => [Number(link.entryTime), link]));
  const entriesByTrend = new Map();
  (payload.entryTrendLinks || []).forEach((link) => {
    const key = Number(link.trendTime);
    if (!entriesByTrend.has(key)) entriesByTrend.set(key, []);
    entriesByTrend.get(key).push(Number(link.entryTime));
  });

  const trendMarkLine = createMarkLine(trendView.container);
  const entryMarkLine = createMarkLine(entryView.container);
  const entryPanelMarkLines = entryPanels.map((view) => createMarkLine(view.container));
  const trendRangeStartLine = createMarkLine(trendView.container);
  const trendRangeEndLine = createMarkLine(trendView.container);
  const entryRangeStartLine = createMarkLine(entryView.container);
  const entryRangeEndLine = createMarkLine(entryView.container);
  const entryPanelRangeLines = entryPanels.map((view) => ({
    start: createMarkLine(view.container),
    end: createMarkLine(view.container),
  }));
  let markedTrendTime = null;
  let markedEntryTime = null;
  let rangeStartTrendTime = null;
  let rangeEndTrendTime = null;
  let rangeStartEntryTime = null;
  let rangeEndEntryTime = null;
  let syncing = false;

  const refreshMarkLines = () => {
    setMarkLine(trendView, trendMarkLine, markedTrendTime);
    setMarkLine(entryView, entryMarkLine, markedEntryTime);
    entryPanels.forEach((view, index) => setMarkLine(view, entryPanelMarkLines[index], markedEntryTime));
    setMarkLine(trendView, trendRangeStartLine, rangeStartTrendTime);
    setMarkLine(trendView, trendRangeEndLine, rangeEndTrendTime);
    setMarkLine(entryView, entryRangeStartLine, rangeStartEntryTime);
    setMarkLine(entryView, entryRangeEndLine, rangeEndEntryTime);
    entryPanels.forEach((view, index) => {
      setMarkLine(view, entryPanelRangeLines[index].start, rangeStartEntryTime);
      setMarkLine(view, entryPanelRangeLines[index].end, rangeEndEntryTime);
    });
  };
  state.markRefreshers.push(refreshMarkLines);
  trendView.chart.timeScale().subscribeVisibleLogicalRangeChange(refreshMarkLines);
  entryView.chart.timeScale().subscribeVisibleLogicalRangeChange(refreshMarkLines);
  entryPanels.forEach((view) => view.chart.timeScale().subscribeVisibleLogicalRangeChange(refreshMarkLines));

  const setEntryPanelCrosshairs = (time) => {
    entryPanels.forEach((view) => {
      const point = view.dataByTime.get(time);
      if (point) {
        view.chart.setCrosshairPosition(point.value, time, view.series);
      } else {
        view.chart.clearCrosshairPosition();
      }
    });
  };

  const trendRangeMarkers = () => {
    const markers = [];
    if (rangeStartTrendTime != null) {
      markers.push({
        time: rangeStartTrendTime,
        position: "belowBar",
        color: "#facc15",
        shape: "circle",
        text: "START",
      });
    }
    if (rangeEndTrendTime != null) {
      markers.push({
        time: rangeEndTrendTime,
        position: "aboveBar",
        color: "#facc15",
        shape: "square",
        text: "END",
      });
    }
    return markers;
  };

  const refreshTrendMarkers = () => {
    setSeriesMarkers(trendView.candleSeries, [...trendView.baseMarkers, ...trendRangeMarkers()]);
  };

  const clearCrosshairs = () => {
    markedTrendTime = null;
    markedEntryTime = null;
    trendView.chart.clearCrosshairPosition();
    entryView.chart.clearCrosshairPosition();
    entryPanels.forEach((view) => view.chart.clearCrosshairPosition());
    refreshMarkLines();
  };

  const clearAll = () => {
    clearCrosshairs();
    rangeStartTrendTime = null;
    rangeEndTrendTime = null;
    rangeStartEntryTime = null;
    rangeEndEntryTime = null;
    refreshTrendMarkers();
    entryLabel.textContent = entryLabel.dataset.baseLabel;
    el.chartLegend.textContent = payload.charts.trend.label;
    refreshMarkLines();
  };

  const showEntryTime = (entryTime) => {
    const entryCandle = entryView.candleByTime.get(entryTime);
    if (!entryCandle) return;
    const link = trendByEntry.get(entryTime);
    const trendTime = link ? Number(link.trendTime) : null;
    const trendCandle = trendTime == null ? null : trendView.candleByTime.get(trendTime);

    markedEntryTime = entryTime;
    markedTrendTime = trendCandle ? trendTime : null;
    entryView.chart.setCrosshairPosition(entryCandle.close, entryTime, entryView.candleSeries);
    setEntryPanelCrosshairs(entryTime);
    if (trendCandle && trendTime != null) {
      trendView.chart.setCrosshairPosition(trendCandle.close, trendTime, trendView.candleSeries);
    } else {
      trendView.chart.clearCrosshairPosition();
    }

    const trendText = link ? `${trendTf} ${fmtUtcMinute(link.trendTime)} ${link.trendLabel || ""}` : `no closed ${trendTf}`;
    entryLabel.textContent = `${entryLabel.dataset.baseLabel} | ${fmtBarOhlc(entryTf, entryTime, entryCandle)} | ${trendText}`;
    el.chartLegend.textContent = trendCandle
      ? `${payload.charts.trend.label} | ${fmtBarOhlc(trendTf, trendTime, trendCandle)} | ${trendText} | close ${fmtUtcMinute(link.trendCloseTime)}`
      : `${payload.charts.trend.label} | ${trendText}`;
    requestAnimationFrame(refreshMarkLines);
  };

  const showTrendTime = (trendTime) => {
    const trendCandle = trendView.candleByTime.get(trendTime);
    if (!trendCandle) return;
    const entryTimes = entriesByTrend.get(trendTime) || [];
    const entryTime = entryTimes.length ? entryTimes[0] : null;
    const entryCandle = entryTime == null ? null : entryView.candleByTime.get(entryTime);

    markedTrendTime = trendTime;
    markedEntryTime = entryCandle ? entryTime : null;
    trendView.chart.setCrosshairPosition(trendCandle.close, trendTime, trendView.candleSeries);
    if (entryCandle && entryTime != null) {
      entryView.chart.setCrosshairPosition(entryCandle.close, entryTime, entryView.candleSeries);
      setEntryPanelCrosshairs(entryTime);
      entryLabel.textContent = `${entryLabel.dataset.baseLabel} | ${trendTf} ${fmtUtcMinute(trendTime)} -> ${fmtBarOhlc(entryTf, entryTime, entryCandle)}`;
    } else {
      entryView.chart.clearCrosshairPosition();
      entryPanels.forEach((view) => view.chart.clearCrosshairPosition());
      entryLabel.textContent = `${entryLabel.dataset.baseLabel} | ${trendTf} ${fmtUtcMinute(trendTime)} has no entry bars`;
    }
    el.chartLegend.textContent = `${payload.charts.trend.label} | ${fmtBarOhlc(trendTf, trendTime, trendCandle)}`;
    requestAnimationFrame(refreshMarkLines);
  };

  const applyTrendRange = () => {
    if (rangeStartTrendTime == null) return;
    const fromTrend = Math.min(rangeStartTrendTime, rangeEndTrendTime ?? rangeStartTrendTime);
    const toTrendOpen = Math.max(rangeStartTrendTime, rangeEndTrendTime ?? rangeStartTrendTime);
    const toTrend = toTrendOpen + trendSeconds;
    const firstEntry = firstChartTimeAtOrAfter(entryView.candleTimes, fromTrend);
    const lastEntry = lastChartTimeAtOrBefore(entryView.candleTimes, toTrend);
    rangeStartEntryTime = firstEntry;
    rangeEndEntryTime = lastEntry;

    if (firstEntry != null && lastEntry != null && firstEntry <= lastEntry) {
      entryView.chart.timeScale().setVisibleRange({ from: firstEntry, to: lastEntry });
      const firstCandle = entryView.candleByTime.get(firstEntry);
      if (firstCandle) {
        entryView.chart.setCrosshairPosition(firstCandle.close, firstEntry, entryView.candleSeries);
        setEntryPanelCrosshairs(firstEntry);
      }
      entryLabel.textContent = `${entryLabel.dataset.baseLabel} | ${trendTf} range ${fmtUtcMinute(fromTrend)} -> ${fmtUtcMinute(toTrendOpen)} | ${entryTf} ${fmtUtcMinute(firstEntry)} -> ${fmtUtcMinute(lastEntry)}`;
    } else {
      entryLabel.textContent = `${entryLabel.dataset.baseLabel} | ${trendTf} range has no loaded ${entryTf} bars`;
    }
    const fromCandle = trendView.candleByTime.get(fromTrend);
    const toCandle = trendView.candleByTime.get(toTrendOpen);
    const rangeOhlcText = toCandle && toTrendOpen !== fromTrend
      ? `${fmtBarOhlc(trendTf, fromTrend, fromCandle)} -> ${fmtBarOhlc(trendTf, toTrendOpen, toCandle)}`
      : fmtBarOhlc(trendTf, fromTrend, fromCandle);
    el.chartLegend.textContent = `${payload.charts.trend.label} | selected ${rangeOhlcText}`;
    refreshTrendMarkers();
    requestAnimationFrame(refreshMarkLines);
  };

  const markTrendRange = (rawTrendTime) => {
    const trendTime = nearestChartTime(trendView.candleTimes, rawTrendTime);
    if (trendTime == null) return;
    if (rangeStartTrendTime == null || rangeEndTrendTime != null) {
      rangeStartTrendTime = trendTime;
      rangeEndTrendTime = null;
    } else {
      rangeEndTrendTime = trendTime;
    }
    applyTrendRange();
  };

  entryView.chart.subscribeCrosshairMove((param) => {
    if (syncing) return;
    if (!param.time || !param.seriesData.has(entryView.candleSeries)) {
      clearCrosshairs();
      return;
    }
    syncing = true;
    showEntryTime(Number(param.time));
    syncing = false;
  });

  trendView.chart.subscribeCrosshairMove((param) => {
    if (syncing) return;
    if (!param.time || !param.seriesData.has(trendView.candleSeries)) {
      clearCrosshairs();
      return;
    }
    syncing = true;
    showTrendTime(Number(param.time));
    syncing = false;
  });

  trendView.chart.subscribeClick((param) => {
    if (!param || param.time == null) return;
    markTrendRange(Number(param.time));
  });
  entryView.chart.subscribeClick((param) => {
    if (!param || param.time == null) return;
    showEntryTime(Number(param.time));
  });

  entryView.container.addEventListener("dblclick", clearAll);
  trendView.container.addEventListener("dblclick", clearAll);
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
  let bulkBars = bars;
  if (el.strategy.value === "combo") {
    delete params.X;
  }
  const query = new URLSearchParams({
    strategy: el.strategy.value,
    tf: exportTf,
    bars: bulkBars,
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
  state.markRefreshers.forEach((refresh) => refresh());
});

init().catch((error) => showError(error.message));
