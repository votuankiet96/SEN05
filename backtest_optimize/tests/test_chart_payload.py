from __future__ import annotations

import unittest

import pandas as pd

from backtest_optimize.analysis.chart_payload import (
    build_signal_chart_payload,
    render_signal_chart_html,
)
from backtest_optimize.contracts import Direction


class ChartPayloadTests(unittest.TestCase):
    def test_build_payload_truncates_bars_and_filters_signal_markers(self) -> None:
        bars = _bars()
        signals = pd.DataFrame(
            {
                "bartime": [bars.loc[2, "bartime"], bars.loc[12, "bartime"], bars.loc[15, "bartime"]],
                "direction": [Direction.BUY, "SELL", "BUY"],
                "symbol": ["US30", "US30", "US30"],
                "timeframe": ["H4", "H4", "H4"],
            }
        )

        payload = build_signal_chart_payload(
            bars=bars,
            signals=signals,
            symbol="US30",
            timeframe="H4",
            max_bars=20,
        )

        self.assertEqual(len(payload["candles"]), 20)
        self.assertEqual(payload["meta"]["bar_count"], 30)
        self.assertEqual(payload["meta"]["rendered_signal_count"], 2)
        self.assertEqual([marker["text"] for marker in payload["markers"]], ["SELL", "BUY"])
        self.assertEqual(payload["markers"][0]["position"], "aboveBar")
        self.assertEqual(payload["markers"][1]["position"], "belowBar")
        self.assertTrue(payload["sma20"])
        self.assertTrue(payload["macd"]["histogram"])
        self.assertTrue(payload["macd"]["line"])
        self.assertTrue(payload["macd"]["signal"])

    def test_render_html_embeds_assets_and_payload(self) -> None:
        payload = build_signal_chart_payload(
            bars=_bars(),
            signals=pd.DataFrame(columns=["bartime", "direction"]),
            symbol="US30",
            timeframe="H4",
            max_bars=10,
        )

        html = render_signal_chart_html(payload, height=500)

        self.assertIn("bt-signal-chart-frame", html)
        self.assertIn("bt-chart-root", html)
        self.assertIn("LightweightCharts", html)
        self.assertIn("&quot;symbol&quot;: &quot;US30&quot;", html)
        self.assertNotIn("__PAYLOAD_JSON__", html)
        self.assertNotIn("__SIGNAL_CHART_JS__", html)

    def test_build_payload_requires_positive_max_bars(self) -> None:
        with self.assertRaises(ValueError):
            build_signal_chart_payload(
                bars=_bars(),
                signals=pd.DataFrame(columns=["bartime", "direction"]),
                symbol="US30",
                timeframe="H4",
                max_bars=0,
            )


def _bars() -> pd.DataFrame:
    times = pd.date_range("2024-01-01", periods=30, freq="4h")
    rows = []
    for index, bartime in enumerate(times):
        close = 100.0 + index
        rows.append(
            {
                "bartime": bartime,
                "open": close - 0.5,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 1000 + index,
            }
        )
    return pd.DataFrame(rows)


if __name__ == "__main__":
    unittest.main()
