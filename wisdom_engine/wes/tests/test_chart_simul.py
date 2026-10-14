"""Test chart_simul (trang chart sống của wes) -- hermetic: Redis giả, không SQL, không mạng.

Gồm: lớp đọc Redis chỉ-đọc (source), phép đối chiếu (verify), dựng JSON (payload),
server Flask (với source giả + chiến lược giả), trang multi-timeframe (/mtf) và 2 test
kiến trúc khoá quy tắc "wes không kéo chéo sang WE, mỗi file một vai trò".
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import redis
from wisdom_engine.wes.src.chart_simul import payload, server
from wisdom_engine.wes.src.chart_simul.live_page import render_page
from wisdom_engine.wes.src.chart_simul.mtf_page import render_mtf_page
from wisdom_engine.wes.src.chart_simul.source import DFS_TIMEFRAMES, RedisSource
from wisdom_engine.wes.src.chart_simul.verify import (
    STATUS_EXTRA,
    STATUS_MISSED,
    STATUS_OK,
    STATUS_PENDING,
    STATUS_UNCHECKED,
    STATUS_WRONG_SIDE,
    Check,
    classify,
    expected_fields,
    field_diffs,
)

STAMP = "%Y-%m-%d %H:%M:%S"
CHART_DIR = Path(__file__).resolve().parents[1] / "src" / "chart_simul"


# =============================================================================
# Redis giả: CHỈ có lệnh đọc -- thử ghi sẽ AttributeError.
# =============================================================================
class _FakePipeline:
    def __init__(self, target: _FakeRedis) -> None:
        self._target = target
        self._calls: list[tuple[Any, tuple, dict]] = []

    def __getattr__(self, name: str):
        method = getattr(self._target, name)

        def record(*args: Any, **kwargs: Any) -> _FakePipeline:
            self._calls.append((method, args, kwargs))
            return self

        return record

    def execute(self) -> list[Any]:
        return [method(*args, **kwargs) for method, args, kwargs in self._calls]


class _FakeRedis:
    def __init__(
        self,
        lists: dict[str, list[str]] | None = None,
        hashes: dict[str, dict[str, str]] | None = None,
        strings: dict[str, str] | None = None,
    ) -> None:
        self.lists = lists or {}
        self.hashes = hashes or {}
        self.strings = strings or {}

    def get(self, key: str) -> str | None:
        return self.strings.get(key)

    def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))

    def hmget(self, key: str, *fields: str) -> list[str | None]:
        row = self.hashes.get(key, {})
        return [row.get(field) for field in fields]

    def lindex(self, key: str, index: int) -> str | None:
        items = self.lists.get(key, [])
        return items[index] if -len(items) <= index < len(items) else None

    def llen(self, key: str) -> int:
        return len(self.lists.get(key, []))

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        items = self.lists.get(key, [])
        n = len(items)
        start = max(start + n, 0) if start < 0 else start
        end = end + n if end < 0 else min(end, n - 1)
        return items[start : end + 1] if start <= end else []

    def pipeline(self, **_: object) -> _FakePipeline:
        return _FakePipeline(self)


_CONFIG: dict[str, Any] = {
    "redis": {
        "host": "127.0.0.1",
        "input": {"db": 14, "key_prefix": "L_DFS", "snapshot_bars": 500, "process_on_startup": False},
        "output": {"db": 15, "key_prefix": "L_WES", "channel_prefix": "L_CHANNEL_SIM"},
    }
}
_PAIR = ("combo", "US30", "H1")


def _source(fake_in: _FakeRedis, fake_out: _FakeRedis) -> RedisSource:
    return RedisSource(_CONFIG, input_client=fake_in, output_client=fake_out)  # type: ignore[arg-type]


def _candle_redis(n: int) -> _FakeRedis:
    key = "L_DFS_US30_H1"
    start = pd.Timestamp("2022-03-01 00:00:00")
    stamps = [(start + pd.Timedelta(hours=i)).strftime(STAMP) for i in range(n)][::-1]
    hashes = {
        f"{key}:{stamp}": {"open": "10", "high": "12", "low": "9", "close": "11"}
        for stamp in stamps
    }
    return _FakeRedis(lists={key: stamps}, hashes=hashes)


# =============================================================================
# 1. source.py -- đọc Redis chỉ-đọc
# =============================================================================
def test_sim_clock_doc_dong_ho_va_trang_thai() -> None:
    fake = _FakeRedis(
        strings={"dfs:clock": "2022-07-30 08:20:00"},
        hashes={"dfs:state": {"run_id": "abc", "tick_seq": "60065", "status": "running"}},
    )
    assert _source(fake, _FakeRedis()).sim_clock() == {
        "simTime": "2022-07-30 08:20:00",
        "tick": 60065,
        "status": "running",
        "runId": "abc",
    }


def test_sim_clock_thieu_khoa_tra_none() -> None:
    clock = _source(_FakeRedis(), _FakeRedis()).sim_clock()
    assert clock == {"simTime": None, "tick": None, "status": None, "runId": None}


def test_nen_doc_dung_prefix_cau_hinh_va_tang_dan() -> None:
    source = _source(_candle_redis(10), _FakeRedis())
    assert source.head_stamp("US30", "H1") == "2022-03-01 09:00:00"
    assert source.head_stamp("US30", "H2") is None
    frame = source.read_candles("US30", "H1", 3)
    assert list(frame["bartime"].dt.strftime("%H:%M")) == ["07:00", "08:00", "09:00"]


def _signal_redis() -> _FakeRedis:
    key = "L_WES_COMBO_US30_H1"
    stamps = ["2022-03-01 02:00:00", "2022-03-01 05:00:00", "2022-03-01 07:00:00"]
    hashes = {f"{key}:{s}": {"tradeSide": "BUY", "symbol": "US30"} for s in stamps}
    hashes[f"{key}:2022-03-01 05:00:00"] = {}  # HASH đã mất giữa chừng -> bị bỏ qua
    return _FakeRedis(lists={key: stamps}, hashes=hashes)


def test_tin_hieu_dem_loc_theo_moc_va_bo_hash_rong() -> None:
    source = _source(_FakeRedis(), _signal_redis())
    assert source.signal_count(_PAIR) == 3
    assert source.signal_count(("combo", "US30", "H2")) == 0
    all_signals = source.read_signals(_PAIR)
    assert list(all_signals) == ["2022-03-01 02:00:00", "2022-03-01 07:00:00"]
    since = source.read_signals(_PAIR, since=pd.Timestamp("2022-03-01 06:00:00"))
    assert list(since) == ["2022-03-01 07:00:00"]


def test_moc_wes_bat_dau_la_stamp_som_nhat_giua_cac_cap() -> None:
    out = _FakeRedis(
        lists={
            "L_WES_COMBO_US30_H1": ["2022-03-02 00:00:00"],
            "L_WES_MACROSS_GOLD_M10": ["2022-03-01 03:10:00", "2022-03-01 09:00:00"],
        }
    )
    pairs = [_PAIR, ("ma_cross", "GOLD", "M10"), ("combo", "DE40", "H4")]
    assert _source(_FakeRedis(), out).first_signal_stamp(pairs) == "2022-03-01 03:10:00"
    assert _source(_FakeRedis(), _FakeRedis()).first_signal_stamp(pairs) is None


# =============================================================================
# 2. verify.py -- đối chiếu kỳ vọng với đã publish
# =============================================================================
def _frame(n: int = 10, signals: dict[int, int] | None = None) -> pd.DataFrame:
    times = pd.date_range("2022-03-01", periods=n, freq="h").astype("datetime64[us]")
    frame = pd.DataFrame({"bartime": times, "signal": 0})
    for index, side in (signals or {}).items():
        frame.loc[index, "signal"] = side
    return frame


def _published(frame: pd.DataFrame, index: int, side: str = "BUY") -> dict[str, dict[str, str]]:
    return {frame["bartime"].iloc[index].strftime(STAMP): {"tradeSide": side}}


def _by_time(checks: list[Check], frame: pd.DataFrame) -> dict[int, str]:
    positions = {pd.Timestamp(t): i for i, t in enumerate(frame["bartime"])}
    return {positions[check.time]: check.status for check in checks}


def _exact_from(frame: pd.DataFrame, sides: dict[int, int]):
    by_time = {pd.Timestamp(frame["bartime"].iloc[i]): side for i, side in sides.items()}
    return lambda time: by_time.get(time, 0)


def test_doi_chieu_khop_cung_huong_la_ok() -> None:
    frame = _frame(signals={5: 1})
    checks = classify(
        frame, _published(frame, 5), check_from=frame["bartime"].iloc[0],
        exact_signal=_exact_from(frame, {5: 1}),
    )
    assert _by_time(checks, frame) == {5: STATUS_OK}


def test_ky_vong_thieu_o_nen_cu_la_lo_con_nen_moi_nhat_la_cho() -> None:
    frame = _frame(signals={5: 1, 9: -1})
    checks = classify(
        frame, {}, check_from=frame["bartime"].iloc[0],
        exact_signal=_exact_from(frame, {5: 1, 9: -1}),
    )
    assert _by_time(checks, frame) == {5: STATUS_MISSED, 9: STATUS_PENDING}
    assert {c.side for c in checks} == {1, -1}


def test_wes_ghi_ma_chart_khong_ra_tin_hieu_la_thua() -> None:
    frame = _frame()
    checks = classify(
        frame, _published(frame, 3), check_from=frame["bartime"].iloc[0],
        exact_signal=_exact_from(frame, {}),
    )
    assert _by_time(checks, frame) == {3: STATUS_EXTRA}


def test_nhieu_hoa_sma_cua_so_500_nen_quyet_dinh_khong_bao_lo_hay_thua() -> None:
    frame = _frame(signals={5: 1})
    # Cửa sổ lớn có tín hiệu, cửa sổ 500 nến của wes không có -> wes không ghi là ĐÚNG.
    no_alarm = classify(
        frame, {}, check_from=frame["bartime"].iloc[0], exact_signal=_exact_from(frame, {}),
    )
    assert no_alarm == []
    # Ngược lại: cửa sổ lớn không có, cửa sổ của wes có, wes đã ghi -> OK, không phải thừa.
    frame = _frame()
    ok = classify(
        frame, _published(frame, 4), check_from=frame["bartime"].iloc[0],
        exact_signal=_exact_from(frame, {4: 1}),
    )
    assert _by_time(ok, frame) == {4: STATUS_OK}


def test_ngoai_vung_doi_chieu_chi_hien_khong_ket_luan() -> None:
    frame = _frame(signals={2: 1})
    published = {**_published(frame, 1), **_published(frame, 8)}
    later = (frame["bartime"].iloc[-1] + pd.Timedelta(hours=1)).strftime(STAMP)
    published[later] = {"tradeSide": "SELL"}  # tín hiệu của nến chart chưa đọc tới
    checks = classify(
        frame, published, check_from=frame["bartime"].iloc[5], exact_signal=_exact_from(frame, {}),
    )
    statuses = {check.time.strftime(STAMP): check.status for check in checks}
    assert statuses[frame["bartime"].iloc[1].strftime(STAMP)] == STATUS_UNCHECKED
    assert statuses[later] == STATUS_UNCHECKED
    assert statuses[frame["bartime"].iloc[8].strftime(STAMP)] == STATUS_EXTRA
    assert frame["bartime"].iloc[2].strftime(STAMP) not in statuses  # kỳ vọng ngoài vùng: im lặng


def test_chua_co_moc_wes_thi_moi_tin_hieu_deu_chua_doi_chieu() -> None:
    frame = _frame(signals={5: 1})
    checks = classify(
        frame, _published(frame, 2), check_from=None, exact_signal=_exact_from(frame, {5: 1}),
    )
    assert [c.status for c in checks] == [STATUS_UNCHECKED]


def test_nguoc_huong_la_wrong_side() -> None:
    frame = _frame(signals={5: 1})
    checks = classify(
        frame, _published(frame, 5, "SELL"), check_from=frame["bartime"].iloc[0],
        exact_signal=_exact_from(frame, {5: 1}),
    )
    assert _by_time(checks, frame) == {5: STATUS_WRONG_SIDE}
    assert checks[0].side == -1  # hiện theo hướng wes đã ghi


def test_tran_so_lan_kiem_lai_tin_ket_qua_cua_so_lon() -> None:
    frame = _frame(n=20, signals={2: 1, 4: 1, 6: 1})
    calls: list[pd.Timestamp] = []

    def exact(time: pd.Timestamp) -> int:
        calls.append(time)
        return 1

    checks = classify(
        frame, {}, check_from=frame["bartime"].iloc[0], exact_signal=exact, max_rechecks=1,
    )
    assert len(calls) == 1
    assert [c.status for c in checks] == [STATUS_MISSED] * 3


def _row(signal: int = 1, atr: float = 2.5) -> pd.Series:
    return pd.Series(
        {
            "bartime": pd.Timestamp("2022-03-01 10:00:00"),
            "signal": signal,
            "entry_price": 100.0,
            "atr": atr,
            "signal_reason": "ly do",
        }
    )


def test_field_ky_vong_giong_cach_wes_dung_hash() -> None:
    combo = expected_fields("combo", "US30", "H1", _row(), tf_minutes=60)
    assert combo is not None
    assert combo["tradeSide"] == "BUY"
    assert combo["orderType"] == "STOP"
    assert combo["stopPrice"] == "100.0"
    assert combo["atr"] == "2.5"
    assert combo["valid_from"] == "2022-03-01T11:00:00Z"
    assert combo["clientOrderId"] == "combo:H1:US30:20220301T100000Z"
    market = expected_fields("ma_cross", "US30", "M10", _row(-1), tf_minutes=10)
    assert market is not None
    assert market["tradeSide"] == "SELL"
    assert market["orderType"] == "MARKET"
    assert "stopPrice" not in market


def test_field_ky_vong_macd_break_la_lenh_market() -> None:
    fields = expected_fields("macd_break", "US30", "H1", _row(), tf_minutes=60)
    assert fields is not None
    assert fields["tradeSide"] == "BUY" and fields["orderType"] == "MARKET"
    assert "stopPrice" not in fields
    assert fields["strategy"] == "macd_break"
    assert fields["clientOrderId"] == "macd_break:H1:US30:20220301T100000Z"


def test_field_ky_vong_none_khi_khong_co_tin_hieu_hoac_thieu_atr() -> None:
    assert expected_fields("combo", "US30", "H1", _row(0), tf_minutes=60) is None
    assert expected_fields("combo", "US30", "H1", _row(atr=float("nan")), tf_minutes=60) is None


def test_field_lech_chi_bao_nen_ok_va_khac_gia_tri() -> None:
    time = pd.Timestamp("2022-03-01 10:00:00")
    chart = expected_fields("combo", "US30", "H1", _row(), tf_minutes=60)
    assert chart is not None
    same = {time.strftime(STAMP): dict(chart)}
    other = {time.strftime(STAMP): {**chart, "atr": "9.9", "comment": "khac"}}
    checks = [Check(time, 1, STATUS_OK)]
    assert field_diffs(checks, {time: chart}, same) == {}
    assert field_diffs(checks, {time: chart}, other) == {time: ["comment", "atr"]}
    assert field_diffs([Check(time, 1, STATUS_MISSED)], {time: chart}, other) == {}


# =============================================================================
# 3. payload.py -- dựng JSON
# =============================================================================
def _view_frame() -> pd.DataFrame:
    times = pd.date_range("2022-03-01", periods=4, freq="h").astype("datetime64[us]")
    return pd.DataFrame(
        {
            "bartime": times,
            "open": [10.0, 11.0, 12.0, 13.0],
            "high": [11.0, 12.0, 13.0, 14.0],
            "low": [9.0, 10.0, 11.0, 12.0],
            "close": [10.5, 11.5, 12.5, 13.5],
            "ma": [float("nan"), 10.0, 11.0, 12.0],
            "macd_h": [float("nan"), 0.5, -0.25, 0.0],
            "atr": [1.0, 1.0, 1.0, 1.0],
        }
    )


def test_diem_nen_duong_histogram_giu_du_diem_ke_ca_thieu_gia_tri() -> None:
    frame = _view_frame()
    candles = payload.candle_points(frame)
    assert candles[0] == {"time": 1646092800, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5}
    line = payload.line_points(frame, "ma")
    assert line[0] == {"time": 1646092800}  # điểm trống, vẫn đủ 4 điểm
    assert line[1] == {"time": 1646096400, "value": 10.0}
    assert len(line) == len(candles) == 4
    hist = payload.histogram_points(frame, "macd_h")
    assert hist[0] == {"time": 1646092800}
    assert hist[1]["color"] == payload.UP_COLOR
    assert hist[2]["color"] == payload.DOWN_COLOR
    assert hist[3]["color"] == payload.UP_COLOR  # 0 coi như không âm


def test_duong_chong_len_gia_theo_chien_luoc_voi_nhan_tham_so_that() -> None:
    frame = _view_frame()
    series = payload.overlay_series(frame, "combo", {"MA_PERIOD": 20})
    assert [(s["key"], s["label"]) for s in series] == [("ma", "MA 20")]
    assert payload.overlay_series(frame, "ma_cross", {"FAST_MA": 13, "SLOW_MA": 34}) == []  # thiếu cột
    assert payload.overlay_series(frame, "khong_co", {}) == []


def test_macd_break_ve_hai_duong_dinh_day_close_va_hien_gia_tri_luat() -> None:
    frame = _view_frame().assign(
        prior_high_close=[float("nan"), 12.0, 12.5, 13.0], prior_low_close=[float("nan"), 9.5, 10.0, 10.5]
    )
    series = payload.overlay_series(frame, "macd_break", {"LOOKBACK_BARS": 100})
    assert [(s["key"], s["label"]) for s in series] == [
        ("prior_high_close", "Prior high close (100)"),
        ("prior_low_close", "Prior low close (100)"),
    ]
    assert all(len(s["data"]) == len(frame) for s in series)  # đủ điểm như nến (NaN -> điểm trống)
    inputs = payload.rule_inputs(frame.iloc[2], "macd_break")
    assert [(i["label"], i["value"]) for i in inputs] == [
        ("Close", 12.5),
        ("Prior high close", 12.5),
        ("Prior low close", 10.0),
        ("MACD hist", -0.25),
        ("ATR", 1.0),
    ]


def test_dau_tin_hieu_chi_nam_trong_vung_ve_va_theo_thu_tu_thoi_gian() -> None:
    frame = _view_frame()
    visible = set(payload.unix_times(frame["bartime"]))
    outside = frame["bartime"].iloc[0] - pd.Timedelta(hours=5)
    checks = [
        Check(frame["bartime"].iloc[3], -1, STATUS_PENDING),
        Check(frame["bartime"].iloc[1], 1, STATUS_OK),
        Check(outside, 1, STATUS_OK),
    ]
    assert payload.marks(checks, visible) == [
        {"time": 1646096400, "side": "BUY", "status": STATUS_OK},
        {"time": 1646103600, "side": "SELL", "status": STATUS_PENDING},
    ]


def _payload_data() -> payload.ViewData:
    frame = _view_frame()
    t_ok, t_missed = frame["bartime"].iloc[1], frame["bartime"].iloc[2]
    row = frame.iloc[1].copy()
    row["signal"], row["entry_price"], row["signal_reason"] = 1, 100.0, "r"
    chart = expected_fields("combo", "US30", "H1", row, tf_minutes=60)
    assert chart is not None
    return payload.ViewData(
        strategy="combo",
        symbol="US30",
        tf="H1",
        tf_minutes=60,
        frame=frame,
        params={"MA_PERIOD": 20},
        checks=[Check(t_ok, 1, STATUS_OK), Check(t_missed, -1, STATUS_MISSED)],
        published={t_ok.strftime(STAMP): dict(chart)},
        expected={t_ok: chart},
        diffs={},
    )


def test_bang_tin_hieu_moi_nhat_truoc_va_phan_biet_da_ghi_voi_chua_ghi() -> None:
    rows = payload.signal_rows(_payload_data())
    assert [r["bar"] for r in rows] == ["2022-03-01 02:00", "2022-03-01 01:00"]
    missed, ok = rows
    assert missed["status"] == STATUS_MISSED and missed["published"] is False
    assert missed["wes"] is None and missed["side"] == "SELL"
    assert ok["status"] == STATUS_OK and ok["published"] is True
    assert ok["close"] == "2022-03-01 02:00"
    assert ok["orderType"] == "STOP"
    assert ok["stop"] == 100.0 and ok["atr"] == 1.0
    assert ok["wes"] == ok["chart"]
    assert {i["label"] for i in ok["inputs"]} >= {"Open", "Close", "MA", "MACD hist", "ATR"}


def test_build_payload_du_cac_khoi_va_thong_ke() -> None:
    result = payload.build_payload(_payload_data())
    assert set(result) == {"candles", "overlays", "panels", "marks", "signals", "stats"}
    assert result["panels"][0]["key"] == "macd" and len(result["panels"][0]["data"]) == 4
    assert result["stats"][STATUS_OK] == 1 and result["stats"][STATUS_MISSED] == 1
    assert result["stats"]["fieldDiff"] == 0
    json.dumps(result)  # phải serialize được (không NaN/numpy lọt vào)


# =============================================================================
# 4. server.py -- Flask với source giả + chiến lược giả
# =============================================================================
_N = 600
_TIMES = pd.date_range("2022-03-01", periods=_N, freq="h").astype("datetime64[us]")
_SIGNAL_AT = {_TIMES[540]: 1, _TIMES[570]: -1, _TIMES[599]: 1}


def _candles() -> pd.DataFrame:
    close = pd.Series(range(_N), dtype="float64") + 100.0
    return pd.DataFrame(
        {
            "bartime": _TIMES,
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": float("nan"),
        }
    )


def _fake_run_strategy(strategy: str, *, symbol: str, tf: str, bars: pd.DataFrame, **_: Any):
    out = bars.copy()
    out["ma"] = out["close"]
    out["macd_h"] = 0.5
    out["atr"] = 2.5
    out["prev_close"] = out["close"].shift(1)
    out["signal"] = out["bartime"].map(_SIGNAL_AT).fillna(0).astype(int)
    out["entry_price"] = out["high"] + 1.0
    out["signal_reason"] = "ly do thu"
    return out


class _FakeSource:
    snapshot_bars = 500

    def __init__(self, published: dict[str, dict[str, str]], *, first_stamp: str | None) -> None:
        self.candles = _candles()
        self.published = published
        self.first_stamp = first_stamp
        self.error: Exception | None = None
        self.clock = {"simTime": "2022-03-26 00:00:00", "tick": 7, "status": "running", "runId": "r1"}

    def sim_clock(self) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        return self.clock

    def head_stamp(self, symbol: str, timeframe: str) -> str:
        return self.candles["bartime"].iloc[-1].strftime(STAMP)

    def read_candles(self, symbol: str, timeframe: str, count: int = 1200) -> pd.DataFrame:
        return self.candles.tail(count).reset_index(drop=True)

    def signal_count(self, pair: tuple[str, str, str]) -> int:
        return len(self.published)

    def read_signals(self, pair: tuple[str, str, str], since: pd.Timestamp | None = None):
        floor = since.strftime(STAMP) if since is not None else ""
        return {s: f for s, f in self.published.items() if s >= floor}

    def first_signal_stamp(self, pairs: list[tuple[str, str, str]]) -> str | None:
        return self.first_stamp


def _fake_published() -> dict[str, dict[str, str]]:
    ok_row = _fake_run_strategy("combo", symbol="US30", tf="H1", bars=_candles()).iloc[540]
    fields = expected_fields("combo", "US30", "H1", ok_row, tf_minutes=60)
    assert fields is not None
    return {
        _TIMES[540].strftime(STAMP): fields,  # khớp -> OK
        _TIMES[580].strftime(STAMP): {"tradeSide": "SELL"},  # chart không ra tín hiệu -> THỪA
    }


@pytest.fixture
def client_and_source(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(server, "run_strategy", _fake_run_strategy)
    monkeypatch.setattr(server, "trend_reference_for", lambda _key: (False, "H4"))
    source = _FakeSource(_fake_published(), first_stamp=_TIMES[520].strftime(STAMP))
    app = server.create_app(source)  # type: ignore[arg-type]
    return app.test_client(), source


def _get(client, **params: Any) -> tuple[int, dict[str, Any]]:
    query = {"strategy": "combo", "symbol": "US30", "tf": "H1", **params}
    response = client.get("/api/view", query_string=query)
    return response.status_code, response.get_json()


def test_view_day_du_doi_chieu_ok_lo_cho_thua(client_and_source) -> None:
    client, _ = client_and_source
    status, body = _get(client)
    assert status == 200 and body["changed"] is True
    assert len(body["candles"]) == server.DEFAULT_BARS
    assert body["stats"][STATUS_OK] == 1
    assert body["stats"][STATUS_MISSED] == 1
    assert body["stats"][STATUS_PENDING] == 1
    assert body["stats"][STATUS_EXTRA] == 1
    assert body["stats"]["fieldDiff"] == 0
    by_status = {m["status"]: m for m in body["marks"]}
    assert by_status[STATUS_PENDING]["time"] == payload.unix_time(_TIMES[599])
    assert by_status[STATUS_OK]["side"] == "BUY"
    meta = body["meta"]
    assert meta["head"] == _TIMES[599].strftime(STAMP)
    assert meta["lastSignal"] == _TIMES[580].strftime(STAMP)
    assert meta["signalCount"] == 2
    assert meta["clock"]["runId"] == "r1"
    assert meta["checkFrom"] == _TIMES[520].strftime("%Y-%m-%d %H:%M")
    assert meta["paramsLabel"].startswith("MA ")
    json.dumps(body)


def test_dau_van_tay_khong_doi_va_het_cho_thi_khong_nap_lai(client_and_source) -> None:
    client, source = client_and_source
    base = {"head": _TIMES[599].strftime(STAMP), "nsig": 2, "run": "r1"}
    # Còn dấu "chờ" -> luôn tính lại (để chuyển được sang LỠ).
    assert _get(client, **base, pending=1)[1]["changed"] is True
    # Hết chờ + vân tay khớp -> chỉ trả đồng hồ, không đọc nến.
    status, same = _get(client, **base, pending=0)
    assert status == 200 and same["changed"] is False
    assert set(same) == {"changed", "meta"} and same["meta"]["clock"]["tick"] == 7
    # Có tín hiệu mới (nsig đổi) hoặc DFS chạy lần mới (run đổi) -> nạp lại.
    source.published = {**source.published, _TIMES[585].strftime(STAMP): {"tradeSide": "BUY"}}
    assert _get(client, **base, pending=0)[1]["changed"] is True
    source.published = _fake_published()
    assert _get(client, **{**base, "run": "r2"}, pending=0)[1]["changed"] is True


def test_nen_chua_co_trong_redis_tra_khung_rong(client_and_source) -> None:
    client, source = client_and_source
    source.candles = source.candles.iloc[0:0]
    status, body = _get(client)
    assert status == 200 and body["candles"] == [] and body["marks"] == []
    assert "No candles" in body["meta"]["message"]


def test_tham_so_sai_tra_400(client_and_source) -> None:
    client, _ = client_and_source
    assert _get(client, tf="H9")[0] == 400
    assert _get(client, tf="M10")[0] == 400  # M10 không thuộc combo
    assert _get(client, symbol="XYZ")[0] == 400
    assert _get(client, strategy="sma_trend")[0] == 400  # sma_trend chỉ có ở we_core, wes không có
    assert _get(client, bars="abc")[0] == 400


def test_bars_bi_ep_vao_khoang_cho_phep(client_and_source) -> None:
    client, _ = client_and_source
    assert len(_get(client, bars=1)[1]["candles"]) == server.BARS_MIN
    assert len(_get(client, bars=99999)[1]["candles"]) == _N


def test_redis_loi_tra_503_va_loi_khac_tra_500(client_and_source) -> None:
    client, source = client_and_source
    source.error = redis.ConnectionError("boom")
    status, body = _get(client)
    assert status == 503 and "boom" in body["error"]
    source.error = RuntimeError("hong")
    status, body = _get(client)
    assert status == 500 and body["error"] == "hong"


def test_bat_trend_filter_thi_bao_ro_khong_doan(client_and_source, monkeypatch) -> None:
    client, _ = client_and_source
    monkeypatch.setattr(server, "trend_reference_for", lambda _key: (True, "H4"))
    status, body = _get(client)
    assert status == 400 and "trend_filter_enabled" in body["error"]


def test_danh_muc_lay_tu_cau_hinh_wes() -> None:
    catalog = server.build_catalog(server.CONFIG)
    enabled = [str(k).lower() for k in server.CONFIG["live"]["enabled_strategies"]]
    assert list(catalog["strategies"]) == enabled
    for info in catalog["strategies"].values():
        minutes = [int(tf[1:]) * (60 if tf[0] == "H" else 1) for tf in info["timeframes"]]
        assert minutes == sorted(minutes)
        assert info["defaultTimeframe"] in info["timeframes"]
    assert catalog["symbols"] == sorted(server.COMBO_SYMBOL_X)
    assert len(server.all_pairs(catalog)) == sum(
        len(i["timeframes"]) for i in catalog["strategies"].values()
    ) * len(catalog["symbols"])


def test_nhan_va_tham_so_macd_break_hien_dung_va_toan_tieng_anh() -> None:
    import re

    from wisdom_engine.wes.src.configuration import normalize_macd_break_params

    vietnamese = re.compile(
        "[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]", re.IGNORECASE
    )
    params = normalize_macd_break_params(None, "US30", "H1")
    label = server._PARAMS_LABEL["macd_break"].format(**params)
    assert server.STRATEGY_LABELS["macd_break"] == "MACD Break"
    assert label == "Lookback 100 · MACD 5/25/5 · ATR 5"
    texts = [
        server.STRATEGY_LABELS["macd_break"],
        label,
        *(entry[1] for entry in payload.OVERLAYS["macd_break"]),  # (cột, nhãn, màu)
        *(entry[1] for entry in payload.RULE_INPUTS["macd_break"]),  # (cột, nhãn)
    ]
    for text in texts:
        assert not vietnamese.search(text), text


def test_view_macd_break_dung_nhan_va_dung_loai_lenh_market(client_and_source) -> None:
    client, _ = client_and_source
    status, body = _get(client, strategy="macd_break", tf="H1")
    assert status == 200 and body["changed"] is True
    assert body["meta"]["strategyLabel"] == "MACD Break"
    assert body["meta"]["paramsLabel"].startswith("Lookback ")
    # Dòng chưa có HASH của wes lấy field kỳ vọng từ chart: macd_break phải là MARKET, không có giá stop.
    expected_rows = [row for row in body["signals"] if not row["published"]]
    assert expected_rows, "mẫu phải có tín hiệu kỳ vọng mà wes chưa ghi"
    assert {row["orderType"] for row in expected_rows} == {"MARKET"}
    assert {row["stop"] for row in expected_rows} == {None}


def test_trang_chu_va_health(client_and_source) -> None:
    client, _ = client_and_source
    page = client.get("/")
    assert page.status_code == 200 and page.mimetype == "text/html"
    html = page.get_data(as_text=True)
    assert "__VENDOR_JS__" not in html and "__CONFIG_JSON__" not in html
    assert "LightweightCharts" in html
    assert client.get("/health").get_json() == {"status": "ok"}


def test_render_page_nhung_cau_hinh_cho_js() -> None:
    html = render_page(
        strategies={"combo": {"label": "Combo", "timeframes": ["H1"], "defaultTimeframe": "H1"}},
        symbols=["US30"],
        default_strategy="combo",
        default_symbol="US30",
        default_bars=300,
        bars_min=50,
        bars_max=1200,
        miss_after_seconds=5,
    )
    assert '"missAfterSeconds": 5' in html
    assert '"defaultSymbol": "US30"' in html


def test_giao_dien_chart_hoan_toan_tieng_anh(client_and_source) -> None:
    """Yêu cầu của người dùng: chữ hiển thị trên chart là tiếng Anh (comment/docstring vẫn tiếng Việt)."""
    import re

    vietnamese = re.compile(
        "[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]", re.IGNORECASE
    )
    client, _ = client_and_source
    pages: dict[str, str] = {}
    for route in ("/", "/mtf"):
        html = client.get(route).get_data(as_text=True)
        assert 'lang="en"' in html
        # Bỏ khối <script> đầu tiên (thư viện vendor) rồi bỏ comment JS/CSS: còn lại là chữ người dùng thấy.
        first = html.index("<script>")
        page = html[:first] + html[html.index("</script>", first) + len("</script>") :]
        pages[route] = re.sub(r"//[^\n]*|/\*.*?\*/", "", page, flags=re.DOTALL)
        assert not vietnamese.search(pages[route]), (route, vietnamese.search(pages[route]))
    assert "<th>Bar open (UTC)</th>" in pages["/"] and "MISSED" in pages["/"]  # đang quét đúng phần trang
    assert "NEW" in pages["/mtf"] and "last candle" in pages["/mtf"]
    errors = [
        _get(client, tf="H9")[1]["error"],
        _get(client, symbol="XYZ")[1]["error"],
        _get(client, strategy="x")[1]["error"],
        _get(client, bars="abc")[1]["error"],
    ]
    labels = [row["label"] for row in payload.rule_inputs(_view_frame().iloc[1], "combo")]
    for text in [*errors, *labels]:
        assert not vietnamese.search(text), text


# =============================================================================
# 5. Kiến trúc: wes độc lập với WE; mỗi file một vai trò, không kéo chéo
# =============================================================================
def _imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_chart_simul_khong_keo_cheo_sang_we() -> None:
    files = sorted(CHART_DIR.glob("*.py"))
    assert files, "không thấy file chart_simul"
    for path in files:
        for module in _imported_modules(path):
            # So theo ranh giới "." để không khớp nhầm wisdom_engine.wes (hệ của chính mình).
            for forbidden in ("wisdom_engine.we_core", "wisdom_engine.we", "wisdom_engine.src"):
                assert module != forbidden, f"{path.name} import {module}"
                assert not module.startswith(forbidden + "."), f"{path.name} import {module}"
        text = path.read_text(encoding="utf-8")
        assert "wisdom_engine/we_core/" not in text and "wisdom_engine/we/" not in text, path.name


def test_moi_file_mot_vai_tro_chi_server_noi_cac_tang() -> None:
    prefix = "wisdom_engine.wes.src.chart_simul."
    allowed = {
        "source": set(),
        "verify": set(),
        "live_page": set(),
        "mtf_page": set(),
        "payload": {"verify"},
        "server": {"source", "verify", "payload", "live_page", "mtf_page"},
    }
    assert {path.stem for path in CHART_DIR.glob("*.py")} == set(allowed)
    for stem, expected in allowed.items():
        internal = {
            module[len(prefix) :]
            for module in _imported_modules(CHART_DIR / f"{stem}.py")
            if module.startswith(prefix)
        }
        assert internal <= expected, f"{stem} import thừa: {internal - expected}"


# =============================================================================
# 6. Trang multi-timeframe (/mtf): một symbol, tối đa 4 khung, CHỈ nến
# =============================================================================
def _grid(start: str, minutes: int, n: int) -> pd.DataFrame:
    """n nến liên tiếp cách nhau `minutes` phút, giá tăng dần (nến thứ i: close = 100 + i)."""
    times = pd.date_range(start, periods=n, freq=f"{minutes}min")
    close = pd.Series(range(n), dtype="float64") + 100.0
    return pd.DataFrame(
        {
            "bartime": times,
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": float("nan"),
        }
    )


class _FakeMtfSource:
    """Source giả của trang /mtf: mỗi khung một chuỗi nến riêng, đếm số lần đọc nến."""

    def __init__(self) -> None:
        self.frames = {
            "H4": _grid("2022-03-01 00:00", 240, 40),
            "H1": _grid("2022-03-01 00:00", 60, 160),
            "M10": _grid("2022-03-01 00:00", 10, 300),
            "W": _grid("2022-03-01 00:00", 10080, 0),  # DFS chưa phát tới khung này
        }
        self.clock: dict[str, Any] = {"simTime": "2022-03-02 04:00:00", "tick": 9, "status": "running", "runId": "r1"}
        self.error: Exception | None = None
        self.reads: list[tuple[str, int]] = []

    def sim_clock(self) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        return self.clock

    def heads(self, symbol: str, timeframes: Any) -> dict[str, str | None]:
        out: dict[str, str | None] = {}
        for tf in timeframes:
            frame = self.frames.get(tf)
            out[tf] = None if frame is None or frame.empty else frame["bartime"].iloc[-1].strftime(STAMP)
        return out

    def read_candles(self, symbol: str, timeframe: str, count: int = 1200) -> pd.DataFrame:
        self.reads.append((timeframe, count))
        return self.frames.get(timeframe, _grid("2022-03-01", 60, 0)).tail(count).reset_index(drop=True)


@pytest.fixture
def mtf_client():
    source = _FakeMtfSource()
    app = server.create_app(source)  # type: ignore[arg-type]
    return app.test_client(), source


def _mtf(client, **params: Any) -> tuple[int, dict[str, Any]]:
    query = {"symbol": "US30", "tfs": "H4,H1,M10", **params}
    response = client.get("/api/mtf", query_string=query)
    return response.status_code, response.get_json()


def _heads_param(panes: list[dict[str, Any]]) -> str:
    return "|".join(f"{pane['tf']}={pane['head']}" for pane in panes)


def test_dfs_phat_du_15_khung_xep_tang_dan_theo_phut() -> None:
    assert len(DFS_TIMEFRAMES) == 15 and len(set(DFS_TIMEFRAMES)) == 15
    minutes = [server.timeframe_minutes(tf) for tf in DFS_TIMEFRAMES]
    assert minutes == sorted(minutes)  # M90 nằm giữa H1 và H2, D1/W ở cuối


def test_heads_doc_stamp_dau_list_nhieu_khung_va_khung_thieu_la_none() -> None:
    fake_in = _candle_redis(3)  # chỉ có L_DFS_US30_H1
    heads = _source(fake_in, _FakeRedis()).heads("US30", ["H1", "H4"])
    assert heads["H1"] == "2022-03-01 02:00:00"  # nến mới nhất ở đầu LIST
    assert heads["H4"] is None


def test_mtf_tham_so_hop_le_xep_lon_den_nho_va_bo_trung() -> None:
    catalog = server.build_catalog(server.CONFIG)
    req = server.parse_mtf_request({"symbol": "us30", "tfs": "m10, H4 ,h1,M10", "bars": "9999"}, catalog)
    assert req.symbol == "US30"
    assert req.tfs == ("H4", "H1", "M10")
    assert req.bars == server.BARS_MAX  # bị ép vào khoảng cho phép như trang chính
    # M90 phải nằm giữa H1 và H2: sắp theo số phút chứ không theo chữ cái.
    assert server.parse_mtf_request({"symbol": "US30", "tfs": "H1,M90,H2"}, catalog).tfs == ("H2", "M90", "H1")


@pytest.mark.parametrize(
    ("args", "needle"),
    [
        ({"symbol": "XYZ", "tfs": "H1"}, "symbol"),
        ({"symbol": "US30", "tfs": ""}, "choose 1 to 4"),
        ({"symbol": "US30"}, "choose 1 to 4"),
        ({"symbol": "US30", "tfs": "H9"}, "timeframe 'H9'"),
        ({"symbol": "US30", "tfs": "M5,M10,M15,M20,M30"}, "at most 4"),
        ({"symbol": "US30", "tfs": "H1", "bars": "abc"}, "bars"),
    ],
)
def test_mtf_tham_so_sai_bi_tu_choi_bang_loi_tieng_anh(args: dict[str, str], needle: str) -> None:
    catalog = server.build_catalog(server.CONFIG)
    with pytest.raises(ValueError, match=needle):
        server.parse_mtf_request(args, catalog)


def test_mtf_lan_dau_tra_du_khung_lon_den_nho_chi_co_nen(mtf_client) -> None:
    client, source = mtf_client
    status, body = _mtf(client, tfs="M10,H4,H1", bars="50")
    assert status == 200 and body["changed"] is True
    assert [pane["tf"] for pane in body["panes"]] == ["H4", "H1", "M10"]
    assert body["meta"]["tfs"] == ["H4", "H1", "M10"]
    assert body["meta"]["clock"]["runId"] == "r1" and body["meta"]["bars"] == 50
    for pane in body["panes"]:
        assert set(pane) == {"tf", "tfSeconds", "head", "lastCandle", "count", "candles"}
        expected_count = {"H4": 40, "H1": 50, "M10": 50}[pane["tf"]]  # H4 chỉ có 40 nến; còn lại bị cắt về bars=50
        assert pane["count"] == len(pane["candles"]) == expected_count
        assert set(pane["candles"][0]) == {"time", "open", "high", "low", "close"}
        assert pane["candles"][0]["time"] < pane["candles"][-1]["time"]
    h4 = body["panes"][0]
    assert h4["tfSeconds"] == 240 * 60 and h4["head"] == "2022-03-07 12:00:00"
    assert h4["lastCandle"] == "2022-03-07 12:00"
    # Chỉ nến: không tín hiệu, không chỉ báo, không đường chồng giá.
    assert not {"signals", "marks", "overlays", "panels"} & (set(body) | set(h4))
    assert len(source.reads) == 3


def test_mtf_dau_van_tay_khong_doi_thi_khong_doc_nen(mtf_client) -> None:
    client, source = mtf_client
    _, first = _mtf(client)
    reads_before = len(source.reads)
    status, body = _mtf(client, heads=_heads_param(first["panes"]), run="r1")
    assert status == 200 and body == {"changed": False, "meta": body["meta"]}
    assert len(source.reads) == reads_before  # hỏi nhẹ: không đọc cửa sổ nến nào


def test_mtf_chi_gui_lai_khung_co_nen_moi(mtf_client) -> None:
    client, source = mtf_client
    _, first = _mtf(client)
    grown = _grid("2022-03-01 00:00", 10, 301)  # DFS vừa phát thêm 1 nến M10
    source.frames["M10"] = grown
    status, body = _mtf(client, heads=_heads_param(first["panes"]), run="r1")
    assert status == 200 and body["changed"] is True
    assert [pane["tf"] for pane in body["panes"]] == ["M10"]
    assert body["panes"][0]["count"] == 300  # bars mặc định 300 -> cửa sổ trượt
    assert body["panes"][0]["head"] == grown["bartime"].iloc[-1].strftime(STAMP)


def test_mtf_run_id_doi_thi_nap_lai_moi_khung(mtf_client) -> None:
    client, source = mtf_client
    _, first = _mtf(client)
    source.clock = {**source.clock, "runId": "r2"}  # DFS chạy lại từ đầu
    _, body = _mtf(client, heads=_heads_param(first["panes"]), run="r1")
    assert body["changed"] is True
    assert [pane["tf"] for pane in body["panes"]] == ["H4", "H1", "M10"]


def test_mtf_khung_chua_co_nen_tra_khung_rong_khong_loi_va_khong_nap_lai(mtf_client) -> None:
    client, _ = mtf_client
    status, body = _mtf(client, tfs="W,H1")
    assert status == 200
    weekly = next(pane for pane in body["panes"] if pane["tf"] == "W")
    assert weekly["candles"] == [] and weekly["count"] == 0 and weekly["head"] == "" and weekly["lastCandle"] is None
    _, again = _mtf(client, tfs="W,H1", heads=_heads_param(body["panes"]), run="r1")
    assert again["changed"] is False  # khung rỗng giữ "" nên không bị coi là có gì mới


def test_mtf_loi_tham_so_tra_400_loi_redis_tra_503(mtf_client) -> None:
    client, source = mtf_client
    status, body = _mtf(client, tfs="H1,H2,H3,H4,H6")
    assert status == 400 and "at most 4" in body["error"]
    assert _mtf(client, symbol="XYZ")[0] == 400
    source.error = redis.ConnectionError("down")
    status, body = _mtf(client)
    assert status == 503 and "Redis is not responding" in body["error"]


def test_mtf_pane_payload_khung_rong_va_co_nen() -> None:
    empty = payload.mtf_pane("W", 10080, _grid("2022-03-01", 60, 0))
    assert empty == {"tf": "W", "tfSeconds": 604800, "head": "", "lastCandle": None, "count": 0, "candles": []}
    pane = payload.mtf_pane("H1", 60, _grid("2022-03-01 00:00", 60, 5))
    assert pane["count"] == 5 and pane["head"] == "2022-03-01 04:00:00" and pane["lastCandle"] == "2022-03-01 04:00"
    assert pane["candles"][-1] == {"time": 1646092800 + 4 * 3600, "open": 103.5, "high": 105.0, "low": 103.0, "close": 104.0}


def test_trang_mtf_nhung_cau_hinh_cho_js_va_co_link_qua_lai(mtf_client) -> None:
    client, _ = mtf_client
    page = client.get("/mtf")
    assert page.status_code == 200 and page.mimetype == "text/html"
    html = page.get_data(as_text=True)
    assert "__VENDOR_JS__" not in html and "__CONFIG_JSON__" not in html and "LightweightCharts" in html
    assert '"maxTimeframes": 4' in html and '"defaultTimeframes": ["H4", "H1", "M30", "M10"]' in html
    assert '"M90": 90' in html and '"W": 10080' in html  # đủ 15 khung kèm số phút cho JS
    assert 'href="/"' in html  # về trang chính
    assert 'href="/mtf"' in client.get("/").get_data(as_text=True)  # và từ trang chính sang


def test_render_mtf_page_khong_con_cho_trong() -> None:
    html = render_mtf_page(
        symbols=["US30"],
        timeframes=["H1"],
        timeframe_minutes={"H1": 60},
        default_symbol="US30",
        default_timeframes=["H1"],
        max_timeframes=4,
        default_bars=300,
        bars_min=50,
        bars_max=1200,
        visible_bars=120,
    )
    assert '"visibleBars": 120' in html and '"timeframeMinutes": {"H1": 60}' in html
