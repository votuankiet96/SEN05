const state = {
  config: null,
  priceChart: null,
  panelCharts: [],
};

const el = {
  strategy: document.getElementById("strategy"),
  symbol: document.getElementById("symbol"),
  tf: document.getElementById("tf"),
  bars: document.getElementById("bars"),
  params: document.getElementById("params"),
  refresh: document.getElementById("refresh"),
  meta: document.getElementById("meta"),
  error: document.getElementById("error"),
  priceChart: document.getElementById("price-chart"),
  panelCharts: document.getElementById("panel-charts"),
  statTotal: document.getElementById("stat-total"),
  statBuy: document.getElementById("stat-buy"),
  statSell: document.getElementById("stat-sell"),
  statLast: document.getElementById("stat-last"),
  signalsHead: document.getElementById("signals-head"),
  signalsBody: document.getElementById("signals-body"),
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

function createOption(parent, value, label, selectedValue) {
  const option = document.createElement("option");
  option.value = value;
  option.textContent = label ?? value;
  option.selected = value === selectedValue;
  parent.appendChild(option);
}

async function init() {
  const response = await fetch("/api/config");
  state.config = await response.json();

  Object.entries(state.config.strategies).forEach(([key, spec]) => {
    createOption(el.strategy, key, spec.label, state.config.defaultStrategy);
  });
  state.config.symbols.forEach((symbol) => createOption(el.symbol, symbol, symbol, state.config.defaultSymbol));
  state.config.timeframes.forEach((tf) => createOption(el.tf, tf, tf, state.config.defaultTf));
  el.bars.value = state.config.defaultBars;

  el.strategy.addEventListener("change", () => {
    renderParamControls();
    loadScan();
  });
  [el.symbol, el.tf].forEach((node) => node.addEventListener("change", loadScan));
  el.bars.addEventListener("change", loadScan);
  el.refresh.addEventListener("click", loadScan);

  renderParamControls();
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
      title: level.label,
    });
    series.setData([
      { time: level.timeStart, value: level.price },
      { time: level.timeEnd, value: level.price },
    ]);
  });

  renderPanels(payload.panels, priceChart);
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

    if (panel.type === "histogram") {
      const series = chart.addHistogramSeries({
        priceFormat: { type: "price", precision: 5, minMove: 0.00001 },
        priceLineVisible: false,
        lastValueVisible: false,
      });
      series.setData(panel.data);
    } else {
      const series = chart.addLineSeries({
        color: panel.color || "#a855f7",
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        title: panel.label,
      });
      series.setData(panel.data);
    }

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
    th.textContent = key;
    headRow.appendChild(th);
  });
  el.signalsHead.appendChild(headRow);

  rows.slice().reverse().forEach((row) => {
    const tr = document.createElement("tr");
    keys.forEach((key) => {
      const td = document.createElement("td");
      td.textContent = row[key] ?? "";
      tr.appendChild(td);
    });
    el.signalsBody.appendChild(tr);
  });
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

