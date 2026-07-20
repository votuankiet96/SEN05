"""Tests for core_engine.warehouse.spool.LiveSpool - the disk-backed fallback
used when the live feed's DB queue and RAM overflow buffer are both full.
This is the last line of defense against dropping live bars, so its
encode/decode round trip and full/corrupt-row handling need to actually work.
"""

from __future__ import annotations

import logging
import pickle
import queue

import pandas as pd
import pytest

from core_engine.warehouse.spool import PAYLOAD_MARKER, LiveSpool


@pytest.fixture
def spool(tmp_path):
    log = logging.getLogger("test_spool")
    log.addHandler(logging.NullHandler())
    s = LiveSpool(tmp_path / "spool.db", max_rows=3, logger=log)
    s.init()
    return s


def _sample_item(batch_id=1, symbol_id=101, tf_code="M5"):
    df = pd.DataFrame({"open": [1.0], "close": [1.1]})
    return (batch_id, symbol_id, tf_code, "SEN.TF_M5", "EURUSD", df)


def test_init_is_idempotent(spool):
    spool.init()
    spool.init()
    assert spool.count() == 0


def test_write_then_flush_round_trips_the_dataframe(spool):
    item = _sample_item()
    assert spool.write(item) is True
    assert spool.count() == 1

    q: queue.Queue = queue.Queue(maxsize=10)
    flushed = spool.flush_to_queue(q)
    assert flushed == 1
    assert spool.count() == 0

    batch_id, symbol_id, tf_code, staging_table, tv_symbol, df = q.get_nowait()
    assert (batch_id, symbol_id, tf_code, staging_table, tv_symbol) == (1, 101, "M5", "SEN.TF_M5", "EURUSD")
    assert list(df["open"]) == [1.0]


def test_write_rejects_when_spool_is_full(spool):
    for i in range(3):
        assert spool.write(_sample_item(symbol_id=100 + i)) is True
    assert spool.count() == 3
    # max_rows=3: a 4th write must be rejected, not silently accepted.
    assert spool.write(_sample_item(symbol_id=999)) is False
    assert spool.count() == 3


def test_normalize_item_accepts_legacy_5_tuple_without_batch_id(spool):
    df = pd.DataFrame({"open": [2.0]})
    legacy_item = (202, "M15", "SEN.TF_M15", "GBPUSD", df)  # no batch_id
    assert spool.write(legacy_item) is True

    q: queue.Queue = queue.Queue(maxsize=10)
    spool.flush_to_queue(q)
    batch_id, symbol_id, tf_code, staging_table, tv_symbol, out_df = q.get_nowait()
    assert batch_id == 0
    assert symbol_id == 202
    assert tf_code == "M15"


def test_flush_to_queue_stops_when_target_queue_is_full(spool):
    for i in range(3):
        spool.write(_sample_item(symbol_id=100 + i))
    q: queue.Queue = queue.Queue(maxsize=1)
    flushed = spool.flush_to_queue(q)
    assert flushed == 1
    # The un-flushed rows must remain in the spool, not be lost.
    assert spool.count() == 2


def test_flush_to_queue_quarantines_corrupt_payload(spool, tmp_path):
    import sqlite3
    from contextlib import closing

    assert spool.write(_sample_item()) is True

    # Corrupt the stored payload directly, bypassing the public API, to
    # simulate on-disk corruption / an incompatible payload version.
    with closing(sqlite3.connect(spool.path)) as con:
        con.execute("UPDATE spool SET bar_data = ?", (b"not a pickle blob",))
        con.commit()

    q: queue.Queue = queue.Queue(maxsize=10)
    flushed = spool.flush_to_queue(q)
    assert flushed == 0
    assert spool.count() == 0  # corrupt row removed from the live table
    assert q.empty()

    with closing(sqlite3.connect(spool.path)) as con:
        quarantined = con.execute("SELECT COUNT(*) FROM spool_quarantine").fetchone()[0]
    assert quarantined == 1


def test_decode_payload_rejects_wrong_version(spool):
    bad_blob = pickle.dumps(
        {PAYLOAD_MARKER: True, "version": 999, "kind": "ohlcv_frame", "data": None},
        protocol=pickle.HIGHEST_PROTOCOL,
    )
    with pytest.raises(ValueError, match="unsupported spool payload version"):
        LiveSpool._decode_payload(bad_blob, payload_version=999)


def test_decode_payload_accepts_legacy_bare_object_at_version_0():
    df = pd.DataFrame({"open": [3.0]})
    bare_blob = pickle.dumps(df, protocol=pickle.HIGHEST_PROTOCOL)
    decoded = LiveSpool._decode_payload(bare_blob, payload_version=0)
    assert list(decoded["open"]) == [3.0]


def test_cleanup_old_removes_nothing_for_fresh_rows(spool):
    spool.write(_sample_item())
    deleted = spool.cleanup_old(hours=48)
    assert deleted == 0
    assert spool.count() == 1


def test_count_returns_none_when_db_file_is_unreachable(tmp_path):
    log = logging.getLogger("test_spool_missing")
    log.addHandler(logging.NullHandler())
    # A directory path where a file is expected makes sqlite3.connect fail.
    bogus_path = tmp_path / "not_a_real_dir" / "sub" / "spool.db"
    s = LiveSpool(bogus_path, max_rows=10, logger=log)
    assert s.count() is None
