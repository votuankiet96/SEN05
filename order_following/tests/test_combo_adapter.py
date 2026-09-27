"""Test logic thuần của strategies/combo/adapter.py — dùng đúng dữ liệu thật đã lấy từ Redis DB1
(L_SIGNAL_US30_H1_COMBO:2026-09-18 11:00:00) để không kiểm bằng số bịa."""

import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from configuration import ComboConfig, ComboInstrument
from strategies.combo.adapter import (
    _is_registered_instrument,
    _parse_signal,
    _parse_trigger,
)

_REAL_HASH = {
    "symbol": "US30",
    "timeframe": "H1",
    "strategy": "combo",
    "bartime": "2026-09-18T11:00:00Z",
    "tradeSide": "SELL",
    "orderType": "STOP",
    "expirationTimestamp": "1789743600",
    "clientOrderId": "combo:H1:US30:20260918T110000Z",
    "comment": "bearish candle crossing below MA, MACD histogram < 0",
    "atr": "85.32",
    "valid_from": "2026-09-18T12:00:00Z",
    "stopPrice": "51712.7",
    "stopLoss": "51850.75",
    "takeProfit": "51489.33",
    "ksl": "1.618",
    "ktp": "2.618",
}


class _StubRisk:
    fallback_ksl = 1.0
    fallback_ktp = 2.618


class _StubConfig:
    risk = _StubRisk()


def _parse(raw):
    return _parse_signal(raw, _StubConfig(), signal_id="test-key")


def test_parse_trigger_valid():
    raw = '{"symbol": "US30", "timeframe": "H1", "strategy": "combo", "stamp": "2026-09-18 11:00:00"}'
    result = _parse_trigger(raw)
    assert result == ("US30", "H1", "combo", "2026-09-18 11:00:00")


def test_parse_trigger_malformed_returns_none():
    assert _parse_trigger("not json") is None
    assert _parse_trigger('{"symbol": "US30"}') is None  # thiếu key bắt buộc


def test_is_registered_instrument():
    config = ComboConfig(
        pubsub_channel="og:events:signals",
        instruments=[
            ComboInstrument(symbol="US30", timeframe="H1", broker_symbol="#US30"),
            ComboInstrument(symbol="GOLD", timeframe="H4", broker_symbol="XAUUSD"),
        ],
    )

    class _Cfg:
        combo = config

    assert _is_registered_instrument(_Cfg(), "US30", "H1") is True
    assert _is_registered_instrument(_Cfg(), "US30", "H4") is False  # đúng symbol sai timeframe
    assert _is_registered_instrument(_Cfg(), "BTCUSD", "H1") is False  # symbol chưa đăng ký


def test_parse_signal_real_data_with_ksl_ktp_present():
    signal = _parse(_REAL_HASH)
    assert signal is not None
    assert signal.symbol == "US30"
    assert signal.ksl == 1.618
    assert signal.ktp == 2.618
    assert signal.stop_price == 51712.7
    assert signal.atr == 85.32
    assert signal.client_order_id == "combo:H1:US30:20260918T110000Z"
    # valid_from/expiration phải là epoch giây, đúng thứ tự thời gian valid_from < expiration
    assert signal.valid_from_epoch < signal.expiration_epoch


def test_parse_signal_falls_back_when_ksl_ktp_absent():
    hash_without_ksl = {k: v for k, v in _REAL_HASH.items() if k not in ("ksl", "ktp")}
    signal = _parse(hash_without_ksl)
    assert signal is not None
    assert signal.ksl == 1.0  # fallback từ config, không phải giá trị Redis
    assert signal.ktp == 2.618


def test_parse_signal_rejects_non_stop_order_type(caplog):
    with caplog.at_level(logging.WARNING):
        assert _parse(dict(_REAL_HASH, orderType="MARKET")) is None
    assert "event=SIGNAL_SKIPPED" in caplog.text
    assert "MARKET" in caplog.text


def test_parse_signal_rejects_missing_required_field(caplog):
    # Schema OG đổi (mất field) mà OF im lặng ngừng vào lệnh là rủi ro thật — phải có log.
    bad = {k: v for k, v in _REAL_HASH.items() if k != "stopPrice"}
    with caplog.at_level(logging.WARNING):
        assert _parse(bad) is None
    assert "event=SIGNAL_SKIPPED" in caplog.text
    assert "KeyError" in caplog.text
