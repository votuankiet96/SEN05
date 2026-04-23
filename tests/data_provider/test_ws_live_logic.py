from __future__ import annotations

from datetime import datetime, timezone


def _bar(ts: int, open_p: float = 1.0, high_p: float = 2.0, low_p: float = 0.5, close_p: float = 1.5, volume: float = 10.0) -> dict:
    return {"v": [ts, open_p, high_p, low_p, close_p, volume]}


def _reset_ws_state(ws_live) -> None:
    ws_live._last_bar_ts.clear()
    ws_live._received_bar_ts.clear()
    ws_live._pending_agg.clear()
    ws_live._deferred_etl.clear()
    ws_live._overflow_buf.clear()
    while True:
        try:
            ws_live._db_queue.get_nowait()
            ws_live._db_queue.task_done()
        except Exception:
            break
    with ws_live._state_lock:
        ws_live._stats["queue_depth"] = 0


def test_handle_series_does_not_advance_watermark_when_enqueue_rejected(load_module, monkeypatch):
    ws_live = load_module("data_provider/02_ws_live.py", "ws_live")
    _reset_ws_state(ws_live)

    fetcher = ws_live.BatchFetcher(group_id=1, symbols=[])
    fetcher._cs_map["cs1"] = (10, "M5", ws_live.TF_STAGING["M5"], "US30")
    ws_live._last_bar_ts[(10, "M5")] = 150

    monkeypatch.setattr(ws_live, "_future_cutoff_ts", lambda: 10_000)
    monkeypatch.setattr(ws_live, "_enqueue_or_buffer", lambda *args, **kwargs: "rejected")

    series_data = {
        "sds_1": {
            "s": [
                _bar(100),
                _bar(200),
                _bar(300),
            ]
        }
    }

    fetcher._handle_series("cs1", series_data, ws=None)

    assert ws_live._received_bar_ts == {}
    assert fetcher._new_bars_count == 0
    assert fetcher._pair_new_bars == {}


def test_enqueue_or_buffer_updates_queue_depth_on_queue_success(load_module):
    ws_live = load_module("data_provider/02_ws_live.py", "ws_live")
    _reset_ws_state(ws_live)

    status = ws_live._enqueue_or_buffer(("dummy",), 1, "US30", "M5")

    assert status == "queued"
    assert ws_live._db_queue.qsize() == 1
    with ws_live._state_lock:
        assert ws_live._stats["queue_depth"] == 1


def test_enqueue_or_buffer_rejects_when_spool_write_fails(load_module, monkeypatch):
    ws_live = load_module("data_provider/02_ws_live.py", "ws_live")
    _reset_ws_state(ws_live)

    monkeypatch.setattr(ws_live._db_queue, "put_nowait", lambda item: (_ for _ in ()).throw(ws_live.queue.Full()))
    monkeypatch.setattr(ws_live, "OVERFLOW_BUFFER_MAX", 0)

    def _boom(item):
        raise RuntimeError("disk full")

    monkeypatch.setattr(ws_live, "_spool_write", _boom)

    status = ws_live._enqueue_or_buffer(("dummy",), 1, "US30", "M5")

    assert status == "rejected"
    with ws_live._state_lock:
        assert ws_live._stats["errors"] >= 1


def test_handle_series_enqueues_only_closed_new_non_future_bars(load_module, monkeypatch):
    ws_live = load_module("data_provider/02_ws_live.py", "ws_live")
    _reset_ws_state(ws_live)

    fetcher = ws_live.BatchFetcher(group_id=1, symbols=[])
    fetcher._cs_map["cs1"] = (81, "M5", ws_live.TF_STAGING["M5"], "BTCUSD")
    ws_live._last_bar_ts[(81, "M5")] = 150

    captured: list[tuple] = []

    def _capture(item, group_id, tv_symbol, tf_code):
        captured.append((item, group_id, tv_symbol, tf_code))
        return True

    monkeypatch.setattr(ws_live, "_future_cutoff_ts", lambda: 250)
    monkeypatch.setattr(ws_live, "_enqueue_or_buffer", _capture)

    series_data = {
        "sds_1": {
            "s": [
                _bar(100),
                _bar(200),
                _bar(260),
                _bar(300),
            ]
        }
    }

    fetcher._handle_series("cs1", series_data, ws=None)

    assert len(captured) == 1
    item, group_id, tv_symbol, tf_code = captured[0]
    df = item[-1]

    assert group_id == 1
    assert tv_symbol == "BTCUSD"
    assert tf_code == "M5"
    assert list(df.index) == [
        datetime.fromtimestamp(200, tz=timezone.utc).replace(tzinfo=None)
    ]
    assert ws_live._received_bar_ts[(81, "M5")] == 200
    assert fetcher._new_bars_count == 1
    assert fetcher._pair_new_bars[(81, "M5")] == 1
