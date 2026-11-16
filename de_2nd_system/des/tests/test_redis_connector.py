"""Test redis_connector.py: mọi việc nối Redis (tạo kết nối, nghe keyspace, kiểm cờ, nối lại) và ghi List–Hash nguyên tử dùng chung — hermetic bằng Redis giả."""

from __future__ import annotations

import json
import logging

import pytest
import redis
from engine_support import FakeConfigRedis, RecordingRedis, decode_eval

from des.src import redis_connector


class FakeRedis:
    def __init__(self) -> None:
        self.pinged = 0
        self.closed = False

    def ping(self) -> None:
        self.pinged += 1

    def close(self) -> None:
        self.closed = True


# =============================================================================
# 1. Kết nối, nghe keyspace, nối lại.
# =============================================================================


class _PubSub:
    def __init__(self, confirm: bool = True) -> None:
        self.patterns: list[str] = []
        self.queue: list[dict] = []
        self.confirm = confirm
        self.closed = False

    def psubscribe(self, *patterns: str) -> None:
        self.patterns.extend(patterns)
        if self.confirm:
            self.queue.extend({"type": "psubscribe", "pattern": p, "data": i + 1} for i, p in enumerate(patterns))

    def get_message(self, ignore_subscribe_messages: bool = False, timeout: float = 0) -> dict | None:
        while self.queue:
            message = self.queue.pop(0)
            if ignore_subscribe_messages and message["type"] == "psubscribe":
                continue
            return message
        return None

    def close(self) -> None:
        self.closed = True


class _ListenerRedis(FakeRedis):
    def __init__(self, flags: str = "hK", confirm: bool = True) -> None:
        super().__init__()
        self.flags = flags
        self.pubsub_obj = _PubSub(confirm)

    def config_get(self, name: str) -> dict[str, str]:
        return {name: self.flags}

    def pubsub(self) -> _PubSub:
        return self.pubsub_obj


def _listener(client: _ListenerRedis) -> redis_connector.KeyspaceListener:
    return redis_connector.KeyspaceListener(host="h", port=1, password="", db=0, client=client)


def test_link_ping_va_close_di_qua_client_va_make_client_la_noi_tao_ket_noi(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeRedis()
    link = redis_connector.Link(host="h", port=1, password="", db=0, client=client)
    link.ping()
    link.close()
    assert link.client is client and client.pinged == 1 and client.closed
    seen: dict = {}
    monkeypatch.setattr(redis_connector.redis, "Redis", lambda **kwargs: seen.update(kwargs) or FakeRedis())
    redis_connector.Link(host="10.0.0.5", port="6379", password="pw", db="0", socket_timeout=4, connect_timeout=3, healthcheck_interval=20)
    assert seen == {
        "host": "10.0.0.5", "port": 6379, "db": 0, "password": "pw", "decode_responses": True,
        "socket_timeout": 4.0, "socket_connect_timeout": 3.0, "health_check_interval": 20,
    }
    seen.clear()
    redis_connector.Link(host="h", port=1, password="", db=0)
    assert seen["password"] is None  # mật khẩu rỗng = không xác thực


def test_keyspace_listener_kiem_co_k_va_h() -> None:
    assert _listener(_ListenerRedis("hK")).check_flags() == "hK"
    with pytest.raises(RuntimeError, match=r"thiếu lớp \['h'\]"):
        _listener(_ListenerRedis("K")).check_flags()
    with pytest.raises(RuntimeError, match=r"thiếu lớp \['K', 'h'\]"):
        _listener(_ListenerRedis("")).check_flags()


def test_keyspace_listener_dang_ky_cho_xac_nhan_du_roi_hut_het_message_dang_cho() -> None:
    client = _ListenerRedis()
    listener = _listener(client)
    assert not listener.subscribed
    with pytest.raises(RuntimeError, match="poll before subscribe"):
        listener.poll(0.0)
    assert listener.subscribe(["a:*", "b:*"]) == 2 and listener.subscribed
    assert client.pubsub_obj.patterns == ["a:*", "b:*"]
    client.pubsub_obj.queue.extend({"type": "pmessage", "channel": f"c{i}", "data": "hset"} for i in range(3))
    assert [m["channel"] for m in listener.poll(0.0)] == ["c0", "c1", "c2"] and listener.poll(0.0) == []


def test_keyspace_listener_khong_du_xac_nhan_trong_han_thi_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(redis_connector, "SUBSCRIBE_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(redis.exceptions.TimeoutError, match="0/2 patterns"):
        _listener(_ListenerRedis(confirm=False)).subscribe(["a:*", "b:*"])


def test_keyspace_listener_close_dong_ca_kenh_nghe_va_ket_noi() -> None:
    client = _ListenerRedis()
    listener = _listener(client)
    listener.subscribe(["a:*"])
    listener.close()
    assert client.pubsub_obj.closed and client.closed and not listener.subscribed


class _Part:
    def __init__(self, name: str, log: list[str]) -> None:
        self.name, self.log = name, log

    def close(self) -> None:
        self.log.append(f"close:{self.name}")


def test_keep_connected_mat_redis_thi_ghi_log_doi_dong_het_roi_noi_lai(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    log: list[str] = []
    slept: list[float] = []
    monkeypatch.setattr(redis_connector.time, "sleep", slept.append)
    attempts = {"n": 0}

    def connect(register):
        attempts["n"] += 1
        first = register(_Part(f"a{attempts['n']}", log))
        if attempts["n"] == 1:
            raise redis.ConnectionError("rớt lúc nối")  # phần đã tạo vẫn phải được đóng
        return first, register(_Part(f"b{attempts['n']}", log))

    def run(parts):
        log.append("run")
        raise SystemExit(0)

    with caplog.at_level(logging.ERROR, logger="des.src.redis_connector"), pytest.raises(SystemExit):
        redis_connector.keep_connected(connect, run, retry_delay_seconds=3.0)
    assert log == ["close:a1", "run", "close:a2", "close:b2"] and slept == [3.0] and attempts["n"] == 2
    assert [r.getMessage() for r in caplog.records] == ["event=redis_connection_lost error=rớt lúc nối retry_in_s=3.0"]
    assert {r.name for r in caplog.records} == {"des.src.redis_connector"}  # dòng log của lớp nối Redis mang tên chính file đó


def test_keep_connected_loi_khac_redis_noi_len_va_van_dong_ket_noi() -> None:
    log: list[str] = []

    def connect(register):
        return register(_Part("a", log))

    def run(part):
        raise ValueError("cấu hình sai")

    with pytest.raises(ValueError, match="cấu hình sai"):
        redis_connector.keep_connected(connect, run, retry_delay_seconds=1.0)
    assert log == ["close:a"]


# =============================================================================
# 2. Ghi List–Hash nguyên tử dùng chung.
# =============================================================================

KWARGS = {"list_key": "L_X_US30_H1", "stamp": "2026-11-03 04:00:00", "fields": {"a": "1", "b": "hai"}, "retention_seconds": 604800, "max_entries": 500}


def test_write_list_hash_goi_eval_dung_thu_tu_doi_so_va_tra_ket_qua_cua_redis() -> None:
    client = RecordingRedis()
    assert redis_connector.write_list_hash(client, channel="L_CH_X", message='{"a": "1"}', **KWARGS) is True
    assert decode_eval(client.calls[0]) == {
        "hash_key": "L_X_US30_H1:2026-11-03 04:00:00", "list_key": "L_X_US30_H1", "stamp": "2026-11-03 04:00:00", "ttl": "604800", "max": "500",
        "channel": "L_CH_X", "message": '{"a": "1"}', "fields": {"a": "1", "b": "hai"},
    }
    assert redis_connector.write_list_hash(RecordingRedis(created=0), **KWARGS) is False  # đã có sẵn: Redis báo 0


def test_write_list_hash_khong_kenh_thi_kenh_rong() -> None:
    client = RecordingRedis()
    redis_connector.write_list_hash(client, **KWARGS)
    call = decode_eval(client.calls[0])
    assert call["channel"] == "" and call["message"] == ""


def test_script_ghi_chi_publish_khi_co_kenh_va_bo_qua_khi_hash_da_co() -> None:
    script = redis_connector._WRITE_LIST_HASH_SCRIPT
    assert script.index("EXISTS") < script.index("HSET") < script.index("EXPIRE") < script.index("RPUSH") < script.index("LPOP") < script.index("DEL") < script.index("PUBLISH")
    assert "return 0" in script and "ARGV[4] ~= ''" in script  # đã có thì không làm gì; kênh rỗng thì không publish


def test_write_list_hash_many_gui_mot_luot_dung_thu_tu_va_dem_so_moi() -> None:
    client = RecordingRedis(emulate=True)
    rows = [{**KWARGS, "stamp": f"2026-11-03 0{i}:00:00"} for i in range(3)]
    assert redis_connector.write_list_hash_many(client, rows) == 3
    assert [decode_eval(c)["stamp"] for c in client.calls] == ["2026-11-03 00:00:00", "2026-11-03 01:00:00", "2026-11-03 02:00:00"]
    assert redis_connector.write_list_hash_many(client, [*rows, {**KWARGS, "stamp": "2026-11-03 03:00:00"}]) == 1  # ba mốc đã có, chỉ mốc mới được tính


def test_trim_lists_before_goi_script_bo_mot_luot_cho_moi_list_va_cong_so_mot_da_bo() -> None:
    client = RecordingRedis(emulate=True)
    for stamp in ("2026-11-03 01:00:00", "2026-11-03 02:00:00", "2026-11-03 03:00:00"):
        redis_connector.write_list_hash(client, **{**KWARGS, "stamp": stamp})
    other = {**KWARGS, "list_key": "L_X_US30_H2"}
    redis_connector.write_list_hash(client, **{**other, "stamp": "2026-11-03 01:00:00"})
    removed = redis_connector.trim_lists_before(client, [("L_X_US30_H1", "2026-11-03 03:00:00"), ("L_X_US30_H2", "2026-11-03 02:00:00"), ("L_X_CHUA_CO", "2026-11-03 02:00:00")])
    assert removed == 3  # H1 bỏ 01:00 và 02:00, H2 bỏ 01:00, LIST chưa có thì không có gì để bỏ
    assert client.lists["L_X_US30_H1"] == ["2026-11-03 03:00:00"] and client.lists["L_X_US30_H2"] == []
    assert "L_X_US30_H1:2026-11-03 01:00:00" not in client.hashes and "L_X_US30_H1:2026-11-03 03:00:00" in client.hashes  # HASH của mốc đã bỏ cũng bị xoá, mốc còn lại giữ nguyên
    assert client.trims == [("L_X_US30_H1", "2026-11-03 03:00:00"), ("L_X_US30_H2", "2026-11-03 02:00:00"), ("L_X_CHUA_CO", "2026-11-03 02:00:00")]


def test_trim_lists_before_rong_thi_khong_gui_gi() -> None:
    client = RecordingRedis()
    assert redis_connector.trim_lists_before(client, []) == 0 and client.trims == [] and client.calls == []


def test_script_bo_mot_cu_lay_dan_tu_dau_list_dung_o_mot_dau_khong_cu_hon_va_xoa_hash() -> None:
    script = redis_connector._TRIM_LIST_BEFORE_SCRIPT
    assert script.index("LINDEX") < script.index("LPOP") < script.index("DEL")
    assert "head >= ARGV[1]" in script and "not head" in script  # dừng ở mốc đầu không cũ hơn, hoặc khi LIST hết/không có
    assert "LTRIM" not in redis_connector._WRITE_LIST_HASH_SCRIPT  # script ghi cắt bằng LPOP + DEL (xoá cả HASH), không dùng LTRIM để HASH khỏi mồ côi


def test_write_list_hash_many_rong_thi_khong_gui_gi() -> None:
    client = RecordingRedis()
    assert redis_connector.write_list_hash_many(client, []) == 0 and client.calls == []


def test_message_kenh_la_json_cua_cac_truong() -> None:
    client = RecordingRedis()
    fields = {"symbol": "HK50", "comment": "tiếng Việt"}
    redis_connector.write_list_hash(client, channel="L_CH", message=json.dumps(fields, ensure_ascii=False), **{**KWARGS, "fields": fields})
    assert json.loads(decode_eval(client.calls[0])["message"]) == fields


def test_ghi_qua_max_entries_thi_xoa_ca_hash_cua_mot_bi_cat_khong_de_hash_mo_coi() -> None:
    """LIST chỉ giữ ``max_entries`` mốc mới nhất; HASH của mốc bị cắt khỏi LIST cũng phải bị xoá (số HASH của một chuỗi luôn bằng số mốc đang giữ)."""
    client = RecordingRedis(emulate=True)
    for hour in range(5):
        redis_connector.write_list_hash(client, **{**KWARGS, "stamp": f"2026-11-03 0{hour}:00:00", "max_entries": 3})
    assert client.lists["L_X_US30_H1"] == ["2026-11-03 02:00:00", "2026-11-03 03:00:00", "2026-11-03 04:00:00"]
    assert sorted(k for k in client.hashes if k.startswith("L_X_US30_H1:")) == [f"L_X_US30_H1:2026-11-03 0{h}:00:00" for h in (2, 3, 4)]


# =============================================================================
# Config có phiên bản: LIST các số phiên bản (cũ đứng trước) + HASH ``{key_prefix}:{phiên bản}`` giữ văn bản trong field ``config``, không hết hạn.
# =============================================================================


def test_read_config_chua_co_phien_ban_nao_thi_none() -> None:
    assert redis_connector.read_config(FakeConfigRedis(), key_prefix="L_CONFIG") is None


def test_write_config_phien_ban_dau_la_1_noi_dung_trung_thi_khong_ghi_noi_dung_doi_thi_phien_ban_moi() -> None:
    client = FakeConfigRedis()
    assert redis_connector.write_config(client, key_prefix="L_CONFIG", text="a", keep_versions=20) == (1, True)
    assert redis_connector.write_config(client, key_prefix="L_CONFIG", text="a", keep_versions=20) == (1, False)  # khởi động lại mà config không đổi
    assert client.evals == 1
    assert redis_connector.write_config(client, key_prefix="L_CONFIG", text="b", keep_versions=20) == (2, True)
    assert redis_connector.read_config(client, key_prefix="L_CONFIG") == (2, "b")
    assert client.lists == {"L_CONFIG": ["1", "2"]} and client.hashes["L_CONFIG:1"] == {"config": "a"}


def test_write_config_chi_giu_so_phien_ban_toi_da_va_xoa_hash_cua_phien_ban_bi_bo() -> None:
    client = FakeConfigRedis()
    for text in "abcde":
        redis_connector.write_config(client, key_prefix="L_CONFIG", text=text, keep_versions=3)
    assert client.lists["L_CONFIG"] == ["3", "4", "5"]
    assert sorted(client.hashes) == ["L_CONFIG:3", "L_CONFIG:4", "L_CONFIG:5"]  # không để HASH mồ côi
    assert redis_connector.read_config(client, key_prefix="L_CONFIG") == (5, "e")


def test_write_config_hai_ben_cung_tinh_ra_mot_so_phien_ban_thi_mot_ben_thang() -> None:
    class _Racing(FakeConfigRedis):
        def eval(self, script: str, numkeys: int, *args: object) -> int:
            if not self.hashes:  # bên kia ghi phiên bản 1 ngay trước lệnh ghi của ta
                self.hashes["L_CONFIG:1"] = {"config": "a"}
                self.lists["L_CONFIG"] = ["1"]
            return super().eval(script, numkeys, *args)

    assert redis_connector.write_config(_Racing(), key_prefix="L_CONFIG", text="a", keep_versions=20) == (1, False)


def test_script_ghi_config_khong_het_han_va_ghi_hash_truoc_list() -> None:
    script = redis_connector._WRITE_CONFIG_SCRIPT
    assert "EXPIRE" not in script and "PUBLISH" not in script
    assert script.index("HSET") < script.index("RPUSH")  # bên nghe keyspace thấy HASH được ghi thì LIST đã có phiên bản mới
