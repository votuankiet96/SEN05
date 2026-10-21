"""Trang multi-timeframe của chart_simul: một symbol, tối đa 4 timeframe xếp dọc từ lớn xuống nhỏ.

Mô tả:
    File lá (cùng vai trò với live_page.py): chỉ dựng chuỗi HTML tự chứa, thư viện
    Lightweight Charts nhúng sẵn, không cần internet. Không đọc Redis, không tính
    gì, không import file nào khác trong chart_simul. server.py phục vụ endpoint
    /api/mtf mà JS ở đây gọi.

    Mục đích: nhìn cùng lúc nhiều khung của một symbol để kiểm tra DFS có phát
    nến đều và đúng không. Mỗi chart CHỈ có nến (không tín hiệu, không chỉ báo,
    không nến "đang hình thành" dựng thêm). Mỗi nến mới DFS phát thì khung tương
    ứng chớp nhãn NEW, các khung còn lại đứng yên đúng như dữ liệu (DFS chỉ phát
    nến ĐÃ ĐÓNG, nên khung lớn chỉ đổi khi nến lớn đóng).

    Đồng bộ các chart theo THỜI GIAN, không theo chỉ số nến (mỗi khung có số nến
    khác nhau): trình duyệt giữ một "khoảng thời gian chung", đổi nó thành khoảng
    chỉ số logic riêng của từng chart (có nội suy ra ngoài vùng dữ liệu bằng độ
    dài khung) rồi đặt vào chart. Trục của Lightweight Charts tính theo chỉ số nên
    các khoảng nghỉ (cuối tuần, giờ nghỉ của chỉ số) làm vị trí ở giữa lệch nhẹ
    giữa các chart; hai đầu khoảng nhìn thì luôn khớp thời gian.

    Phần nạp dữ liệu giống trang chính: mỗi nhịp chỉ hỏi server "có gì mới không"
    kèm dấu vân tay (stamp nến mới nhất từng khung), chỉ khung nào đổi mới nhận
    lại nến.

render_mtf_page(...) -> str: một trang HTML tự chứa.
"""

from __future__ import annotations

import json
from pathlib import Path

_VENDOR_JS_PATH = Path(__file__).resolve().parent / "vendor" / "lightweight-charts.js"

_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>des multi-timeframe</title>
<style>
  html, body { height: 100%; margin: 0; padding: 0; }
  body { display: flex; flex-direction: column; overflow: hidden; font-family: -apple-system, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #e2e8f0; }
  #controls, #statusbar, #error { flex: none; }
  #controls { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; padding: 10px 16px; border-bottom: 1px solid #1e293b; }
  #controls label { font-size: 13px; }
  #controls a { color: #93c5fd; font-size: 13px; text-decoration: none; margin-right: 6px; }
  #controls select, #controls input { background: #1e293b; color: #e2e8f0; border: 1px solid #334155; border-radius: 4px; padding: 4px 6px; font-size: 13px; }
  #controls input { width: 70px; }
  #controls button { background: #2563eb; color: #fff; border: none; border-radius: 4px; padding: 5px 12px; font-size: 13px; cursor: pointer; }
  .dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; background: #64748b; }
  .dot.ok { background: #22c55e; }
  .dot.bad { background: #ef4444; }
  #statusbar { display: flex; flex-wrap: wrap; gap: 6px 18px; padding: 6px 16px; font-size: 12px; color: #94a3b8; border-bottom: 1px solid #1e293b; }
  #error { display: none; padding: 8px 16px; background: #7f1d1d; color: #fecaca; font-size: 13px; }
  #panes { flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column; overflow-y: auto; }
  .pane { flex: 1 1 0; min-height: 140px; display: flex; flex-direction: column; border-top: 1px solid #1e293b; }
  .pane-head { flex: none; display: flex; align-items: center; gap: 14px; height: 24px; box-sizing: border-box; padding: 0 10px; font-size: 12px; color: #94a3b8; background: #0b1222; }
  .pane-head .tf { color: #e2e8f0; font-weight: 700; font-size: 13px; min-width: 34px; }
  .pane-head .tag { visibility: hidden; color: #052e16; background: #22c55e; border-radius: 3px; padding: 0 6px; font-weight: 700; font-size: 11px; }
  .pane.flash .tag { visibility: visible; animation: fade 1.3s forwards; }
  @keyframes fade { 0% { opacity: 1; } 70% { opacity: 1; } 100% { opacity: 0; } }
  .pane-chart { flex: 1 1 0; min-height: 0; position: relative; }
</style>
</head>
<body>
<div id="controls">
  <a href="/">Single chart</a>
  <a href="/trend">Trend chart</a>
  <label>Symbol <select id="symbol"></select></label>
  <label>TF <select id="tf0"></select></label>
  <label>TF <select id="tf1"></select></label>
  <label>TF <select id="tf2"></select></label>
  <label>TF <select id="tf3"></select></label>
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
  <span id="updated"></span>
</div>
<div id="error"></div>
<div id="panes"></div>
<script>
__VENDOR_JS__
</script>
<script>
(function () {
  "use strict";
  const CONFIG = __CONFIG_JSON__;
  const MINUTES = CONFIG.timeframeMinutes;
  const SETTLE_MS = 700;   // thư viện tự đặt lại vị trí sau khi nạp nến; trong khoảng này không coi là người dùng kéo
  const REACT_MS = 200;    // sau khi mình đặt khoảng nhìn cho một chart, sự kiện của chính chart đó là phản ứng của thư viện
  const $ = (id) => document.getElementById(id);
  const el = {
    symbol: $("symbol"), bars: $("bars"), auto: $("auto"), refresh: $("refresh"), dot: $("live-dot"),
    clock: $("clock"), updated: $("updated"), error: $("error"), panes: $("panes"),
    tfs: [0, 1, 2, 3].map((i) => $("tf" + i)),
  };
  let panes = [];          // mỗi chart: {tf, tfSeconds, box, holder, chart, series, times, closes, stamp, ...}
  let paneKey = "";        // symbol|tfs của bộ chart đang dựng
  let dataKey = "";        // paneKey|bars: đổi thì nạp lại đủ, không gửi dấu vân tay
  let run = "";            // run_id của DFS mà dữ liệu đang giữ thuộc về
  let timeRange = null;    // {from, to} giây Unix dùng chung cho mọi chart
  let anchor = 0;          // nến mới nhất (lớn nhất giữa các khung) lúc đặt timeRange
  let following = true;    // đang bám nến mới nhất thì khoảng nhìn trượt theo
  let requestId = 0;
  let loading = false;
  let timer = null;
  let settleUntil = 0;     // trước mốc này (performance.now) bỏ qua sự kiện đổi khoảng nhìn

  function minutesOf(tf) { return MINUTES[tf] || 0; }

  function clampBars(value) {
    const number = Number.isFinite(value) ? value : CONFIG.defaultBars;
    return Math.min(CONFIG.barsMax, Math.max(CONFIG.barsMin, number));
  }

  // Các timeframe đang chọn: bỏ trùng, xếp lớn -> nhỏ (cùng thứ tự server trả về).
  function selectedTfs() {
    const seen = new Set();
    el.tfs.forEach((select) => { if (select.value) seen.add(select.value); });
    return Array.from(seen).sort((a, b) => minutesOf(b) - minutesOf(a)).slice(0, CONFIG.maxTimeframes);
  }

  function showError(text) { el.error.textContent = text; el.error.style.display = "block"; }
  function clearError() { el.error.style.display = "none"; }
  function setDot(kind) { el.dot.className = "dot" + (kind ? " " + kind : ""); }

  // ----------------------------------------------------------- điều khiển
  function populateControls() {
    const query = new URLSearchParams(window.location.search);
    CONFIG.symbols.forEach((symbol) => el.symbol.add(new Option(symbol, symbol)));
    const symbol = (query.get("symbol") || "").toUpperCase();
    el.symbol.value = CONFIG.symbols.includes(symbol) ? symbol : CONFIG.defaultSymbol;
    const fromUrl = (query.get("tfs") || "").split(",")
      .map((tf) => tf.trim().toUpperCase()).filter((tf) => tf in MINUTES);
    const wanted = (fromUrl.length ? fromUrl : CONFIG.defaultTimeframes).slice(0, el.tfs.length);
    el.tfs.forEach((select, index) => {
      select.add(new Option("-", ""));
      CONFIG.timeframes.forEach((tf) => select.add(new Option(tf, tf)));
      select.value = wanted[index] || "";
    });
    el.bars.value = String(clampBars(parseInt(query.get("bars") || "", 10)));
  }

  function updateUrl() {
    const query = new URLSearchParams({
      symbol: el.symbol.value, tfs: selectedTfs().join(","), bars: el.bars.value,
    });
    window.history.replaceState(null, "", "?" + query.toString());
  }

  // ----------------------------------------------------- đổi giữa thời gian và chỉ số
  // times tăng dần. Trong vùng dữ liệu nội suy giữa hai nến liền kề; ngoài vùng
  // ngoại suy bằng độ dài khung (đủ để căn thẳng hai đầu khoảng nhìn).
  function timeAt(pane, index) {
    const t = pane.times;
    const n = t.length;
    if (index <= 0) return t[0] + index * pane.tfSeconds;
    if (index >= n - 1) return t[n - 1] + (index - (n - 1)) * pane.tfSeconds;
    const lo = Math.floor(index);
    return t[lo] + (index - lo) * (t[lo + 1] - t[lo]);
  }

  function indexAt(pane, time) {
    const t = pane.times;
    const n = t.length;
    if (time <= t[0]) return (time - t[0]) / pane.tfSeconds;
    if (time >= t[n - 1]) return (n - 1) + (time - t[n - 1]) / pane.tfSeconds;
    let lo = 0;
    let hi = n - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (t[mid] <= time) lo = mid; else hi = mid;
    }
    return lo + (time - t[lo]) / (t[hi] - t[lo]);
  }

  // Chỉ số nến cuối cùng có giờ <= time; -1 nếu không có.
  function indexAtOrBefore(pane, time) {
    const t = pane.times;
    if (t.length === 0 || time < t[0]) return -1;
    let lo = 0;
    let hi = t.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (t[mid] <= time) lo = mid; else hi = mid - 1;
    }
    return lo;
  }

  // ------------------------------------------------------------- các chart
  // autoSize: chart tự co theo ô chứa (bố cục flex trong CSS). shiftVisibleRangeOnNewBar tắt
  // để thư viện không tự trượt khoảng nhìn khi có nến mới -- việc đó do refreshRange làm,
  // chung cho mọi chart.
  function chartOptions() {
    return {
      autoSize: true,
      layout: { background: { color: "#0f172a" }, textColor: "#e2e8f0" },
      grid: { vertLines: { color: "#1e293b" }, horzLines: { color: "#1e293b" } },
      timeScale: { timeVisible: true, secondsVisible: false, rightOffset: 2, shiftVisibleRangeOnNewBar: false },
    };
  }

  function part(parent, className, text) {
    const node = document.createElement("span");
    node.className = className;
    node.textContent = text;
    parent.appendChild(node);
    return node;
  }

  function buildPanes(order) {
    panes.forEach((pane) => pane.chart.remove());
    el.panes.innerHTML = "";
    panes = order.map((tf) => {
      const box = document.createElement("div");
      box.className = "pane";
      const head = document.createElement("div");
      head.className = "pane-head";
      part(head, "tf", tf);
      const lastEl = part(head, "last", "waiting for candles...");
      const countEl = part(head, "count", "");
      part(head, "tag", "NEW");
      const holder = document.createElement("div");
      holder.className = "pane-chart";
      box.appendChild(head);
      box.appendChild(holder);
      el.panes.appendChild(box);
      const chart = LightweightCharts.createChart(holder, chartOptions());
      const series = chart.addCandlestickSeries({
        upColor: "#16a34a", downColor: "#dc2626", borderVisible: false,
        wickUpColor: "#16a34a", wickDownColor: "#dc2626",
      });
      const pane = {
        tf: tf, tfSeconds: minutesOf(tf) * 60, box: box, holder: holder, chart: chart, series: series,
        lastEl: lastEl, countEl: countEl, times: [], closes: [], stamp: "", ignoreUntil: 0,
      };
      chart.timeScale().subscribeVisibleLogicalRangeChange((range) => onRange(pane, range));
      chart.subscribeCrosshairMove((param) => onCrosshair(pane, param));
      return pane;
    });
  }

  function flash(pane) {
    pane.box.classList.remove("flash");
    void pane.box.offsetWidth;
    pane.box.classList.add("flash");
  }

  function applyPane(data) {
    const pane = panes.find((item) => item.tf === data.tf);
    if (!pane) return;
    const fresh = pane.stamp !== "" && data.head !== "" && data.head !== pane.stamp;
    pane.stamp = data.head;
    pane.times = data.candles.map((candle) => candle.time);
    pane.closes = data.candles.map((candle) => candle.close);
    pane.series.setData(data.candles);
    pane.lastEl.textContent = data.lastCandle ? "last candle " + data.lastCandle + " UTC" : "no candles yet";
    pane.countEl.textContent = data.count + " candles";
    if (fresh) flash(pane);
  }

  // ------------------------------------------------------- đồng bộ theo thời gian
  function activePanes() { return panes.filter((pane) => pane.times.length > 0); }

  function baseSeconds() {
    const active = activePanes();
    return active[active.length - 1].tfSeconds;     // khung nhỏ nhất đang có nến
  }

  function latestTime() {
    return Math.max(...activePanes().map((pane) => pane.times[pane.times.length - 1]));
  }

  function placeAll(except) {
    activePanes().forEach((pane) => {
      if (pane === except) return;
      pane.ignoreUntil = performance.now() + REACT_MS;
      pane.chart.timeScale().setVisibleLogicalRange({
        from: indexAt(pane, timeRange.from), to: indexAt(pane, timeRange.to),
      });
    });
  }

  // Sau mỗi lần có dữ liệu mới: lần đầu đặt khoảng mặc định (mỗi nhịp của khung nhỏ
  // nhất, visibleBars nến); sau đó nếu đang bám nến mới nhất thì trượt khoảng theo.
  function refreshRange() {
    const active = activePanes();
    if (active.length === 0) return;
    const last = latestTime();
    if (timeRange === null) {
      const base = active[active.length - 1];
      const shown = Math.min(CONFIG.visibleBars, base.times.length);
      const to = last + 2 * base.tfSeconds;
      timeRange = { from: to - (shown + 2) * base.tfSeconds, to: to };
      following = true;
      settleUntil = performance.now() + SETTLE_MS;
      [100, 300, SETTLE_MS - 50].forEach((delay) => setTimeout(() => {
        if (timeRange !== null && performance.now() < settleUntil) placeAll(null);
      }, delay));
    } else if (following && last !== anchor) {
      const shift = last - anchor;
      timeRange = { from: timeRange.from + shift, to: timeRange.to + shift };
    }
    anchor = last;
    placeAll(null);
  }

  // Sự kiện đổi khoảng nhìn của thư viện bắn TRỄ (không đồng bộ), kể cả khi chính mình vừa
  // đặt, và thư viện có thể "kẹp" lại khoảng mình đặt (khung lớn có nến cuối cũ hơn mép phải
  // chung nên không hiển thị nổi vùng trống bên phải). Vì vậy: bỏ qua sự kiện của chart vừa
  // bị mình đặt (REACT_MS), còn lại chỉ coi là người dùng kéo/zoom khi lệch hơn dung sai.
  function onRange(pane, range) {
    // Chưa có khoảng chung (đang nạp lần đầu) hoặc đang trong thời gian ổn định sau khi đặt
    // khoảng mặc định: đó là thư viện tự bố trí lại, không phải người dùng kéo.
    const now = performance.now();
    if (!range || pane.times.length === 0 || timeRange === null || now < settleUntil || now < pane.ignoreUntil) return;
    const next = { from: timeAt(pane, range.from), to: timeAt(pane, range.to) };
    const tolerance = Math.max(1, (timeRange.to - timeRange.from) * 0.005);
    if (Math.abs(next.from - timeRange.from) <= tolerance && Math.abs(next.to - timeRange.to) <= tolerance) return;
    timeRange = next;
    // Bám nến mới nhất khi mép phải CHUNG đã tới nến mới nhất của khung nhỏ nhất (không phải
    // nến cuối của riêng chart nào: khung lớn luôn có nến cuối cũ hơn).
    anchor = latestTime();
    following = next.to >= anchor - 1.5 * baseSeconds();
    placeAll(pane);
  }

  // Crosshair: đặt ở nến gần nhất không muộn hơn giờ đang trỏ trên chart kia.
  // setCrosshairPosition không bắn lại sự kiện nên không có vòng lặp.
  function onCrosshair(source, param) {
    panes.forEach((pane) => {
      if (pane === source) return;
      const k = param.time ? indexAtOrBefore(pane, param.time) : -1;
      if (k < 0) pane.chart.clearCrosshairPosition();
      else pane.chart.setCrosshairPosition(pane.closes[k], pane.times[k], pane.series);
    });
  }

  // ------------------------------------------------------------------- nạp dữ liệu
  function renderClock(meta) {
    const clock = meta.clock || {};
    const parts = ["DFS clock " + (clock.simTime || "-") + " UTC"];
    if (clock.tick !== null && clock.tick !== undefined) parts.push("tick " + clock.tick);
    if (clock.status) parts.push(clock.status);
    if (clock.runId) parts.push("run " + clock.runId);
    el.clock.textContent = parts.join(" | ");
    el.updated.textContent = "server time " + meta.serverTime + " UTC";
  }

  async function load(force) {
    const id = ++requestId;
    loading = true;
    try {
      const order = selectedTfs();
      if (order.length === 0) { showError("Select at least one timeframe."); setDot("bad"); return; }
      const nextPaneKey = el.symbol.value + "|" + order.join(",");
      if (nextPaneKey !== paneKey) {
        buildPanes(order);
        paneKey = nextPaneKey;
        timeRange = null;
        following = true;
        run = "";
        force = true;
      }
      const nextDataKey = paneKey + "|" + el.bars.value;
      if (nextDataKey !== dataKey) { dataKey = nextDataKey; force = true; }
      const params = new URLSearchParams({ symbol: el.symbol.value, tfs: order.join(","), bars: el.bars.value });
      if (!force) {
        params.set("heads", panes.map((pane) => pane.tf + "=" + pane.stamp).join("|"));
        params.set("run", run);
      }
      const response = await fetch("/api/mtf?" + params.toString(), { cache: "no-store" });
      const payload = await response.json();
      if (id !== requestId) return;
      if (!response.ok) { showError(payload.error || ("HTTP " + response.status)); setDot("bad"); return; }
      clearError();
      setDot("ok");
      renderClock(payload.meta);
      if (payload.changed === false) return;
      const nextRun = (payload.meta.clock && payload.meta.clock.runId) || "";
      if (nextRun !== run) { run = nextRun; timeRange = null; following = true; }
      payload.panes.forEach(applyPane);
      refreshRange();
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
  el.symbol.addEventListener("change", reload);
  el.tfs.forEach((select) => select.addEventListener("change", reload));
  el.bars.addEventListener("change", () => { el.bars.value = String(clampBars(parseInt(el.bars.value, 10))); reload(); });
  el.refresh.addEventListener("click", reload);
  el.auto.addEventListener("change", setAuto);

  populateControls();
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


def render_mtf_page(
    *,
    symbols: list[str],
    timeframes: list[str],
    timeframe_minutes: dict[str, int],
    default_symbol: str,
    default_timeframes: list[str],
    max_timeframes: int,
    default_bars: int,
    bars_min: int,
    bars_max: int,
    visible_bars: int,
) -> str:
    """Dựng trang multi-timeframe -- HTML tự chứa, JS bên trong tự gọi /api/mtf.

    Các giá trị nhúng làm `CONFIG` cho JS (danh sách symbol, 15 khung DFS kèm số
    phút, số khung tối đa, giới hạn ô Bars) lấy từ cùng nguồn với server.
    """
    config = {
        "symbols": symbols,
        "timeframes": timeframes,
        "timeframeMinutes": timeframe_minutes,
        "defaultSymbol": default_symbol,
        "defaultTimeframes": default_timeframes,
        "maxTimeframes": max_timeframes,
        "defaultBars": default_bars,
        "barsMin": bars_min,
        "barsMax": bars_max,
        "visibleBars": visible_bars,
    }
    html = _TEMPLATE.replace("__VENDOR_JS__", _read_vendor_js())
    return html.replace("__CONFIG_JSON__", json.dumps(config))
