"""
Render candles + markers thành 1 file HTML tự chứa (không cần server).

Mô tả:
    File này CHỈ lo việc lắp ráp HTML: nhận dữ liệu JSON-safe từ payload.py
    (candles, markers) cùng vài dòng metadata hiển thị, nhúng thẳng vào 1
    template HTML kèm nội dung thư viện Lightweight Charts (vendor/) —
    không tự tính toán chỉ báo/tín hiệu, không tự đọc DataFrame.

    Output là 1 file .html độc lập, mở trực tiếp bằng trình duyệt
    (file://) là chạy — không có API, không có phần tùy biến (overlay MA,
    panel MACD, entry/level...) như dashboard sống, đúng theo phạm vi
    "chỉ hiển thị điều kiện signal BUY/SELL".

Đầu vào:
    candles: list[dict] — output của payload.candlestick_points().
    markers: list[dict] — output của payload.signal_markers().

Đầu ra:
    render_chart_html(...) -> str: nội dung HTML hoàn chỉnh.
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
<title>__TITLE__</title>
<style>
  html, body { margin: 0; padding: 0; height: 100%; }
  body {
    font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
    background: #0f172a;
    color: #e2e8f0;
  }
  #header {
    padding: 10px 16px;
    font-size: 14px;
    border-bottom: 1px solid #1e293b;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  #chart { width: 100vw; height: calc(100vh - 41px); }
</style>
</head>
<body>
<div id="header">__HEADER__</div>
<div id="chart"></div>
<script>
__VENDOR_JS__
</script>
<script>
(function () {
  const candles = __CANDLES_JSON__;
  const markers = __MARKERS_JSON__;
  const container = document.getElementById("chart");

  const chart = LightweightCharts.createChart(container, {
    width: container.clientWidth,
    height: container.clientHeight,
    layout: { background: { color: "#0f172a" }, textColor: "#e2e8f0" },
    grid: {
      vertLines: { color: "#1e293b" },
      horzLines: { color: "#1e293b" },
    },
    timeScale: { timeVisible: true, secondsVisible: false },
  });

  const series = chart.addCandlestickSeries({
    upColor: "#16a34a",
    downColor: "#dc2626",
    borderVisible: false,
    wickUpColor: "#16a34a",
    wickDownColor: "#dc2626",
  });
  series.setData(candles);
  series.setMarkers(markers);
  chart.timeScale().fitContent();

  window.addEventListener("resize", () => {
    chart.resize(container.clientWidth, container.clientHeight);
  });
})();
</script>
</body>
</html>
"""


def _read_vendor_js() -> str:
    """Đọc nội dung thư viện Lightweight Charts đã vendor sẵn."""
    return _VENDOR_JS_PATH.read_text(encoding="utf-8")


def render_chart_html(
    candles: list[dict[str, Any]],
    markers: list[dict[str, Any]],
    *,
    symbol: str,
    tf: str,
    strategy_label: str,
) -> str:
    """
    Lắp ráp 1 file HTML hoàn chỉnh hiển thị nến + mũi tên BUY/SELL.

    Args:
        candles: List OHLC points, từ payload.candlestick_points().
        markers: List BUY/SELL markers, từ payload.signal_markers().
        symbol: Mã symbol hiển thị trên header (vd "US30").
        tf: Mã timeframe hiển thị trên header (vd "H1").
        strategy_label: Tên chiến lược hiển thị (vd "Combo").

    Returns:
        Chuỗi HTML hoàn chỉnh, tự chứa (JS thư viện nhúng thẳng vào file) —
        mở trực tiếp bằng trình duyệt, không cần server.
    """
    buy_count = sum(1 for m in markers if m["text"] == "BUY")
    sell_count = sum(1 for m in markers if m["text"] == "SELL")
    title = f"{strategy_label} · {symbol} {tf}"
    header = (
        f"{strategy_label} · {symbol} · {tf} · {len(candles)} bars · "
        f"{buy_count} BUY / {sell_count} SELL"
    )

    html = _TEMPLATE
    html = html.replace("__TITLE__", title)
    html = html.replace("__HEADER__", header)
    html = html.replace("__VENDOR_JS__", _read_vendor_js())
    html = html.replace("__CANDLES_JSON__", json.dumps(candles))
    html = html.replace("__MARKERS_JSON__", json.dumps(markers))
    return html
