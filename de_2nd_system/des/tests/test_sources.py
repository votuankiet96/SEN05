"""Test đọc nguồn nến của des_config.yaml (``redis.candles.sources``): mỗi nguồn một Redis, mỗi symbol đúng một nguồn, nguồn ``default`` bắt buộc — hermetic."""

from __future__ import annotations

import pytest
from engine_support import SERVERS, shared_config

from des.src import configuration


def _sources(**sources: dict) -> dict:
    return configuration._sources(shared_config(og8=True)["redis"]["candles"] | {"sources": sources}, SERVERS)


DEFAULT = {"server": "aen", "db": 10, "key_prefix": "L_DFS", "clock_key": "dfs:clock", "symbols": ["US30", "BTCUSD"]}
OG8 = {"server": "og8", "db": 10, "key_prefix": "L_DFS", "clock_key": "dfs:clock", "symbols": ["EURUSD"]}


def test_doc_du_nguon_voi_dia_chi_redis_cua_tung_nguon() -> None:
    sources = _sources(default=DEFAULT, og8=OG8)
    assert list(sources) == ["default", "og8"]
    assert sources["default"].endpoint == {"host": "127.0.0.1", "port": 6379, "password": ""} and sources["default"].symbols == ("US30", "BTCUSD")
    assert sources["og8"].endpoint == {"host": "10.11.12.8", "port": 6380, "password": "mat-khau-og8"} and sources["og8"].key_prefix == "L_DFS" and sources["og8"].clock_key == "dfs:clock" and sources["og8"].db == 10


def test_ten_nguon_la_chu_thuong_va_so_khong_trung_va_phai_co_nguon_default() -> None:
    with pytest.raises(KeyError, match="redis.candles.sources.default"):
        _sources(og8=OG8)
    with pytest.raises(ValueError, match="lowercase letters and digits only"):
        _sources(default=DEFAULT, Og_8=OG8)
    with pytest.raises(ValueError, match="twice"):
        configuration._sources({"sources": {"default": DEFAULT, "DEFAULT ": DEFAULT}}, SERVERS)  # hai tên viết khác nhau nhưng cùng tên sau chuẩn hoá


def test_moi_symbol_chi_co_mot_nguon_nen() -> None:
    with pytest.raises(ValueError, match="symbol EURUSD is listed by both"):
        _sources(default=DEFAULT | {"symbols": ["US30", "EURUSD"]}, og8=OG8)


def test_nguon_tro_toi_server_chua_khai_hoac_thieu_khoa_thi_loi_neu_dung_duong_dan() -> None:
    with pytest.raises(ValueError, match="redis.candles.sources.default.server.*not declared in redis.servers"):
        _sources(default=DEFAULT | {"server": "khong-co"})
    with pytest.raises(KeyError, match="redis.candles.sources.og8.symbols"):
        _sources(default=DEFAULT, og8={"server": "og8", "db": 10, "key_prefix": "L_DFS", "clock_key": "dfs:clock"})
    with pytest.raises(TypeError, match="non-empty list"):
        _sources(default=DEFAULT | {"symbols": []})
