"""Tests for the Medium-18 fix in core_engine.shared.tradingview.auth.core:
_headless_refresh() and _headless_login_fresh() used to call browser.close()
only on their explicit success/early-return paths. Any exception raised in
between launch() and that call (e.g. page.goto() timing out, a selector
never appearing) skipped cleanup entirely, leaking a headless Chromium
process per occurrence - significant on a 24/7 service where these run
repeatedly over weeks. Both now wrap the browser's lifetime in try/finally.

A fake playwright.sync_api.sync_playwright is installed so this runs
without a real Chromium binary (the sandbox has the `playwright` Python
package but not necessarily the browser download).
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from core_engine.shared.tradingview.auth import core as auth_core


class _FakePage:
    def __init__(self, *, fail_at_goto=False):
        self.fail_at_goto = fail_at_goto

    def goto(self, *a, **k):
        if self.fail_at_goto:
            raise TimeoutError("page.goto timed out")

    def evaluate(self, *a, **k):
        return None

    def wait_for_selector(self, *a, **k):
        raise Exception("selector not found")

    def wait_for_timeout(self, *a, **k):
        pass

    def wait_for_url(self, *a, **k):
        pass

    def wait_for_load_state(self, *a, **k):
        pass

    def fill(self, *a, **k):
        pass

    def click(self, *a, **k):
        pass

    @property
    def keyboard(self):
        class _KB:
            def press(self, *a, **k):
                pass
        return _KB()


class _FakeContext:
    def __init__(self, page):
        self._page = page

    def new_page(self):
        return self._page

    def add_cookies(self, *a, **k):
        pass

    def cookies(self):
        return []


class _FakeBrowser:
    def __init__(self, page):
        self.closed = False
        self.close_calls = 0
        self._page = page

    def new_context(self, *a, **k):
        return _FakeContext(self._page)

    def close(self):
        self.close_calls += 1
        self.closed = True


class _FakeChromium:
    def __init__(self, browser):
        self._browser = browser

    def launch(self, *a, **k):
        return self._browser


class _FakePlaywright:
    def __init__(self, browser):
        self.chromium = _FakeChromium(browser)


def _install_fake_sync_playwright(monkeypatch, browser):
    @contextmanager
    def _fake_sync_playwright_cm():
        yield _FakePlaywright(browser)

    def _fake_sync_playwright():
        return _fake_sync_playwright_cm()

    monkeypatch.setattr("playwright.sync_api.sync_playwright", _fake_sync_playwright)


@pytest.fixture(autouse=True)
def _no_stealth(monkeypatch):
    # _apply_playwright_stealth tries to import playwright_stealth, which
    # may not be installed - make it a no-op for these tests either way.
    monkeypatch.setattr(auth_core, "_apply_playwright_stealth", lambda page: None)


def test_headless_refresh_closes_browser_even_when_goto_raises(monkeypatch):
    page = _FakePage(fail_at_goto=True)
    browser = _FakeBrowser(page)
    _install_fake_sync_playwright(monkeypatch, browser)

    token, cookie = auth_core._headless_refresh("")

    assert browser.close_calls == 1, "browser.close() must run even though page.goto() raised"
    assert token == auth_core.GUEST_TOKEN


def test_headless_refresh_closes_browser_on_success_path_too(monkeypatch):
    page = _FakePage(fail_at_goto=False)
    browser = _FakeBrowser(page)
    _install_fake_sync_playwright(monkeypatch, browser)

    auth_core._headless_refresh("")

    assert browser.close_calls == 1


def test_headless_login_fresh_closes_browser_even_when_goto_raises(monkeypatch):
    page = _FakePage(fail_at_goto=True)
    browser = _FakeBrowser(page)
    _install_fake_sync_playwright(monkeypatch, browser)

    token, cookie = auth_core._headless_login_fresh("user@example.com", "hunter2")

    assert browser.close_calls == 1, "browser.close() must run even though page.goto() raised"
    assert token == auth_core.GUEST_TOKEN


def test_headless_login_fresh_closes_browser_when_username_field_not_found(monkeypatch):
    # fail_at_goto=False but wait_for_selector always raises -> "filled"
    # stays False -> the early-return branch, which used to have its own
    # explicit browser.close() call (now handled uniformly by finally).
    page = _FakePage(fail_at_goto=False)
    browser = _FakeBrowser(page)
    _install_fake_sync_playwright(monkeypatch, browser)

    token, cookie = auth_core._headless_login_fresh("user@example.com", "hunter2")

    assert browser.close_calls == 1
    assert token == auth_core.GUEST_TOKEN


def test_headless_refresh_never_calls_close_twice(monkeypatch):
    page = _FakePage(fail_at_goto=False)
    browser = _FakeBrowser(page)
    _install_fake_sync_playwright(monkeypatch, browser)

    auth_core._headless_refresh("")

    assert browser.close_calls == 1, "must not double-close"
