from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from core_engine.core.historical import pipeline
from core_engine.shared.tradingview.auth import core as auth_core


def _lock_sequence(*states: bool):
    remaining = iter(states)

    @contextmanager
    def _lock(_logger=None):
        yield next(remaining)

    return _lock


def test_bootstrap_waits_for_peer_token_instead_of_falling_back_to_guest(monkeypatch):
    token_reads = iter([("", ""), ("peer-token", "cached_token")])
    monkeypatch.setattr(auth_core, "AUTH_REFRESH_PEER_WAIT_SEC", 30)
    monkeypatch.setattr(auth_core, "AUTH_REFRESH_PEER_POLL_SEC", 0)
    monkeypatch.setattr(auth_core, "_auth_refresh_process_lock", _lock_sequence(False))
    monkeypatch.setattr(auth_core, "_load_reusable_cached_token", lambda *a, **k: next(token_reads))
    monkeypatch.setattr(auth_core.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(auth_core.time, "monotonic", lambda: 0.0)

    token, source = auth_core._bootstrap_credentials_coordinated()

    assert token == "peer-token"
    assert source == "cached_token"


def test_bootstrap_takes_over_when_peer_exits_without_token(monkeypatch):
    monkeypatch.setattr(auth_core, "AUTH_REFRESH_PEER_WAIT_SEC", 30)
    monkeypatch.setattr(auth_core, "AUTH_REFRESH_PEER_POLL_SEC", 0)
    monkeypatch.setattr(auth_core, "_auth_refresh_process_lock", _lock_sequence(False, True))
    monkeypatch.setattr(auth_core, "_load_reusable_cached_token", lambda *a, **k: ("", ""))
    monkeypatch.setattr(auth_core, "auth_refresh_lock_status", lambda: {"present": False})
    monkeypatch.setattr(auth_core, "_bootstrap_credentials", lambda _logger: ("new-token", "browser"))
    monkeypatch.setattr(auth_core.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(auth_core.time, "monotonic", lambda: 0.0)

    token, source = auth_core._bootstrap_credentials_coordinated()

    assert token == "new-token"
    assert source == "browser"


def test_historical_promotes_guest_token_from_runtime_cache(monkeypatch):
    tv = SimpleNamespace(token=auth_core.GUEST_TOKEN)
    captured: dict[str, object] = {}

    monkeypatch.setattr(pipeline.tv_auth, "resolve_auth_token", lambda _logger: ("fresh-token", "cached_token"))
    monkeypatch.setattr(pipeline.tv_auth, "set_current_token", lambda token: captured.update(global_token=token))

    def _fetch_history(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(df=None, status="timeout", returned=0, error="")

    monkeypatch.setattr(pipeline.tv_history, "fetch_history", _fetch_history)

    pipeline._fetch_history_frame(
        tv,
        {"tv_symbol": "US500", "tv_exchange": "CAPITALCOM"},
        "H6",
        20,
        allow_replay=False,
    )

    assert tv.token == "fresh-token"
    assert captured["global_token"] == "fresh-token"
    assert captured["token"] == "fresh-token"


def test_auth_http_requests_always_enforce_tls_verification(monkeypatch):
    captured = {}

    class Session:
        def request(self, method, url, **kwargs):
            captured.update(method=method, url=url, **kwargs)
            return SimpleNamespace(status_code=200)

    monkeypatch.setattr(auth_core, "_get_http_session", lambda: Session())

    response = auth_core._http_request_with_retry(
        "GET",
        "https://www.tradingview.com/",
        max_retries=0,
        verify=False,
    )

    assert response.status_code == 200
    assert captured["verify"] is True
