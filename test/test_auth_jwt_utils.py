"""Tests for core_engine.tradingview.auth.jwt_utils - the JWT decoding and
token-usability checks extracted from auth.py. Pure functions, no shared
auth state, no network - a good target for real coverage.
"""

from __future__ import annotations

import base64
import json
import logging
import time

import pytest

from core_engine.tradingview.auth import jwt_utils


@pytest.fixture
def logger():
    log = logging.getLogger("test_jwt_utils")
    log.addHandler(logging.NullHandler())
    return log


def _make_jwt(exp_offset_sec: float) -> str:
    """Build a minimal unsigned JWT with an `exp` claim `exp_offset_sec` from now."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload_obj = {"exp": time.time() + exp_offset_sec}
    payload = base64.urlsafe_b64encode(json.dumps(payload_obj).encode()).rstrip(b"=").decode()
    return f"{header}.{payload}.sig"


def test_jwt_expires_in_positive_for_future_token():
    token = _make_jwt(3600)
    remaining = jwt_utils._jwt_expires_in(token)
    assert 3595 < remaining <= 3600


def test_jwt_expires_in_negative_for_expired_token():
    token = _make_jwt(-100)
    remaining = jwt_utils._jwt_expires_in(token)
    assert remaining < 0


def test_jwt_expires_in_minus_one_for_undecodable_token():
    assert jwt_utils._jwt_expires_in("not-a-jwt") == -1.0
    assert jwt_utils._jwt_expires_in("") == -1.0


def test_is_refreshed_token_usable_rejects_guest_token(logger):
    assert jwt_utils._is_refreshed_token_usable(jwt_utils.GUEST_TOKEN, "test", logger) is False


def test_is_refreshed_token_usable_rejects_empty_token(logger):
    assert jwt_utils._is_refreshed_token_usable("", "test", logger) is False


def test_is_refreshed_token_usable_rejects_undecodable_token(logger):
    assert jwt_utils._is_refreshed_token_usable("garbage", "test", logger) is False


def test_is_refreshed_token_usable_rejects_token_expiring_too_soon(logger):
    token = _make_jwt(30)  # below the default min_remaining_sec=60
    assert jwt_utils._is_refreshed_token_usable(token, "test", logger) is False


def test_is_refreshed_token_usable_accepts_healthy_token(logger):
    token = _make_jwt(3600)
    assert jwt_utils._is_refreshed_token_usable(token, "test", logger) is True


def test_token_needs_proactive_refresh_true_for_guest_and_empty():
    assert jwt_utils._token_needs_proactive_refresh(jwt_utils.GUEST_TOKEN) is True
    assert jwt_utils._token_needs_proactive_refresh("") is True


def test_token_needs_proactive_refresh_true_when_below_threshold():
    token = _make_jwt(60)
    assert jwt_utils._token_needs_proactive_refresh(token, threshold_sec=900) is True


def test_token_needs_proactive_refresh_false_when_well_within_threshold():
    token = _make_jwt(3600)
    assert jwt_utils._token_needs_proactive_refresh(token, threshold_sec=900) is False


def test_is_token_reusable_for_startup_accepts_undecodable_as_is(logger):
    # Cannot decode expiry -> assume usable rather than block startup.
    assert jwt_utils._is_token_reusable_for_startup("garbage", "test", logger) is True


def test_is_token_reusable_for_startup_rejects_expired(logger):
    token = _make_jwt(-10)
    assert jwt_utils._is_token_reusable_for_startup(token, "test", logger) is False


def test_is_token_reusable_for_startup_rejects_too_close_to_expiry(logger):
    token = _make_jwt(60)  # below default min_remaining_sec=STARTUP_MIN_TOKEN_TTL_SEC (600)
    assert jwt_utils._is_token_reusable_for_startup(token, "test", logger) is False


def test_is_token_reusable_for_startup_accepts_healthy_token(logger):
    token = _make_jwt(3600)
    assert jwt_utils._is_token_reusable_for_startup(token, "test", logger) is True


def test_token_status_summary_missing():
    assert jwt_utils._token_status_summary("")["state"] == "missing"
    assert jwt_utils._token_status_summary("")["present"] is False


def test_token_status_summary_guest():
    summary = jwt_utils._token_status_summary(jwt_utils.GUEST_TOKEN)
    assert summary["state"] == "guest"
    assert summary["present"] is True


def test_token_status_summary_unknown_for_undecodable():
    summary = jwt_utils._token_status_summary("garbage")
    assert summary["state"] == "unknown"
    assert summary["seconds_remaining"] is None


def test_token_status_summary_expired():
    token = _make_jwt(-10)
    summary = jwt_utils._token_status_summary(token)
    assert summary["state"] == "expired"
    assert summary["seconds_remaining"] < 0


def test_token_status_summary_expiring_soon_vs_valid():
    near = _make_jwt(jwt_utils.TOKEN_PROACTIVE_REFRESH_SEC - 60)
    healthy = _make_jwt(jwt_utils.TOKEN_PROACTIVE_REFRESH_SEC + 3600)
    assert jwt_utils._token_status_summary(near)["state"] == "expiring_soon"
    assert jwt_utils._token_status_summary(healthy)["state"] == "valid"
