(function () {
  "use strict";

  const payload = window.__SIGNAL_CHART_PAYLOAD__ || {};
  const root = document.querySelector(".bt-chart-root");
  if (!root) {
    return;
  }

  const priceEl = root.querySelector(".bt-chart-price");
  const macdEl = root.querySelector(".bt-chart-macd");
  const titleEl = root.querySelector(".bt-chart-title");
  const metaEl = root.querySelector(".bt-chart-meta");
  const crosshairEl = root.querySelector(".bt-chart-crosshair");
  const candles = payload.candles || [];
  const meta = payload.meta || {};

  titleEl.textContent = `${meta.symbol || ""} ${meta.timeframe || ""}`.trim();
  metaEl.textContent = [
    `${meta.rendered_bar_count || candles.length} bars`,
    `${meta.rendered_signal_count || 0}/${meta.signal_count || 0} signals`,
    `SMA${meta.sma_period || 20}`,
    `MACD ${((meta.macd || {}).fast) || 5}/${((meta.macd || {}).slow) || 25}/${((meta.macd || {}).signal) || 5}`,
  ].join(" | ");

  if (!window.LightweightCharts || !candles.length) {
    priceEl.innerHTML = '<div class="bt-chart-empty">Chart data is not available.</div>';
    return;
  }

  const chartOptions = {
    layout: {
      background: { type: "solid", color: "#131722" },
      textColor: "#d1d4dc",
      fontFamily: "Arial, Helvetica, sans-serif",
      fontSize: 11,
    },
    grid: {
      vertLines: { color: "#242833" },
      horzLines: { color: "#242833" },
    },
    rightPriceScale: {
      borderColor: "#2a2e39",
      scaleMargins: { top: 0.12, bottom: 0.12 },
    },
    timeScale: {
      borderColor: "#2a2e39",
      timeVisible: true,
      secondsVisible: false,
      rightOffset: 6,
      barSpacing: 7,
    },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
      vertLine: { color: "#758696", labelBackgroundColor: "#363a45" },
      horzLine: { color: "#758696", labelBackgroundColor: "#363a45" },
    },
    handleScale: {
      axisPressedMouseMove: true,
      mouseWheel: true,
      pinch: true,
    },
    handleScroll: {
      mouseWheel: true,
      pressedMouseMove: true,
      horzTouchDrag: true,
      vertTouchDrag: true,
    },
    localization: {
      priceFormatter: (price) => Number(price).toFixed(2),
    },
  };

  const priceChart = LightweightCharts.createChart(priceEl, {
    ...chartOptions,
    width: priceEl.clientWidth,
    height: priceEl.clientHeight,
  });
  const macdChart = LightweightCharts.createChart(macdEl, {
    ...chartOptions,
    width: macdEl.clientWidth,
    height: macdEl.clientHeight,
    rightPriceScale: {
      borderColor: "#2a2e39",
      scaleMargins: { top: 0.18, bottom: 0.18 },
    },
    timeScale: {
      ...chartOptions.timeScale,
      visible: true,
    },
  });

  const candleSeries = priceChart.addCandlestickSeries({
    upColor: "#26a69a",
    downColor: "#ef5350",
    borderUpColor: "#26a69a",
    borderDownColor: "#ef5350",
    wickUpColor: "#26a69a",
    wickDownColor: "#ef5350",
    priceLineVisible: false,
  });
  candleSeries.setData(candles);
  candleSeries.setMarkers(payload.markers || []);

  const smaSeries = priceChart.addLineSeries({
    color: "#2962ff",
    lineWidth: 1,
    priceLineVisible: false,
    lastValueVisible: false,
    title: `SMA${meta.sma_period || 20}`,
  });
  smaSeries.setData(payload.sma20 || []);

  const macd = payload.macd || {};
  const histogramSeries = macdChart.addHistogramSeries({
    priceLineVisible: false,
    lastValueVisible: false,
    priceFormat: { type: "price", precision: 4, minMove: 0.0001 },
  });
  histogramSeries.setData(macd.histogram || []);

  const macdLineSeries = macdChart.addLineSeries({
    color: "#3b82f6",
    lineWidth: 1,
    priceLineVisible: false,
    lastValueVisible: false,
    priceFormat: { type: "price", precision: 4, minMove: 0.0001 },
    title: "MACD",
  });
  macdLineSeries.setData(macd.line || []);

  const signalLineSeries = macdChart.addLineSeries({
    color: "#f59e0b",
    lineWidth: 1,
    priceLineVisible: false,
    lastValueVisible: false,
    priceFormat: { type: "price", precision: 4, minMove: 0.0001 },
    title: "Signal",
  });
  signalLineSeries.setData(macd.signal || []);

  let syncLock = false;
  priceChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
    if (syncLock || !range) return;
    syncLock = true;
    macdChart.timeScale().setVisibleLogicalRange(range);
    syncLock = false;
  });
  macdChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
    if (syncLock || !range) return;
    syncLock = true;
    priceChart.timeScale().setVisibleLogicalRange(range);
    syncLock = false;
  });

  priceChart.subscribeCrosshairMove((param) => {
    const point = param.seriesData && param.seriesData.get(candleSeries);
    if (!point) {
      crosshairEl.textContent = "";
      return;
    }
    crosshairEl.textContent = [
      `O ${formatPrice(point.open)}`,
      `H ${formatPrice(point.high)}`,
      `L ${formatPrice(point.low)}`,
      `C ${formatPrice(point.close)}`,
    ].join("  ");
  });

  const resize = () => {
    priceChart.resize(priceEl.clientWidth, priceEl.clientHeight);
    macdChart.resize(macdEl.clientWidth, macdEl.clientHeight);
  };

  if (window.ResizeObserver) {
    new ResizeObserver(resize).observe(root);
  } else {
    window.addEventListener("resize", resize);
  }

  priceChart.timeScale().fitContent();
  macdChart.timeScale().fitContent();
  resize();

  function formatPrice(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) {
      return "";
    }
    if (Math.abs(number) >= 1000) {
      return number.toFixed(1);
    }
    return number.toFixed(4);
  }
})();
