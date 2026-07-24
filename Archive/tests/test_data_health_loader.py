from pathlib import Path

from modules import data_health_loader as loader


ROOT = Path(__file__).resolve().parents[1]


class DummyConnection:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_load_staging_health_reports_fact_backlog_with_legacy_aliases(monkeypatch):
    conn = DummyConnection()
    captured = {}

    def fake_read_sql(query, sql_conn, params=None):
        captured["query"] = query
        captured["conn"] = sql_conn
        captured["params"] = params
        return [
            {
                "TFCode": "M5",
                "StagingTable": "SEN.TF_M5",
                "TotalRows": 5,
                "LoadedRows": 3,
                "NotLoadedRows": 2,
                "OldestBarTime": None,
                "LatestBarTime": None,
                "LatestReceivedAt": None,
            }
        ]

    monkeypatch.setattr(loader, "TF_DISPLAY_ORDER", ["M5"])
    monkeypatch.setattr(loader, "TF_STAGING", {"M5": "SEN.TF_M5"})
    monkeypatch.setattr(loader, "get_connection", lambda: conn)
    monkeypatch.setattr(loader, "_read_sql", fake_read_sql)

    result = loader.load_staging_health()

    item = result["items"][0]
    assert item["LoadedRows"] == 3
    assert item["NotLoadedRows"] == 2
    assert item["PendingRows"] == 2
    assert item["ProcessedRows"] == 3
    assert item["error"] is None

    assert captured["conn"] is conn
    assert captured["params"] == ("M5", "SEN.TF_M5", "M5")
    assert "LEFT JOIN" in captured["query"]
    assert "DWH.Fact_OHLCV" in captured["query"]
    assert "IsProcessed = 0" not in captured["query"]
    assert conn.closed is True


def test_chart_staging_backlog_label_matches_fact_backlog():
    text = (ROOT / "data_provider" / "dashboard" / "chart.html").read_text(encoding="utf-8")

    assert "not in Fact</span>" in text
    assert " pending</span>" not in text
