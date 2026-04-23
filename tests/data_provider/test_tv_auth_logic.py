from __future__ import annotations

import base64
import json
import time


def _make_jwt(exp_offset_sec: float) -> str:
    header = base64.b64encode(b'{"alg":"HS256","typ":"JWT"}').decode().rstrip("=")
    payload = base64.b64encode(
        json.dumps({"exp": time.time() + exp_offset_sec}).encode()
    ).decode().rstrip("=")
    return f"{header}.{payload}.sig"


class _DummyLogger:
    def info(self, *args, **kwargs) -> None:
        return None

    def warning(self, *args, **kwargs) -> None:
        return None

    def error(self, *args, **kwargs) -> None:
        return None


def test_startup_rejects_nearly_expired_tokens(load_module):
    tv_auth = load_module("data_provider/_tv_auth.py", "tv_auth")

    token = _make_jwt(60)

    assert not tv_auth._is_token_reusable_for_startup(token, "cached_token", _DummyLogger())


def test_startup_accepts_healthy_tokens(load_module):
    tv_auth = load_module("data_provider/_tv_auth.py", "tv_auth")

    token = _make_jwt(3600)

    assert tv_auth._is_token_reusable_for_startup(token, "static_token", _DummyLogger())
