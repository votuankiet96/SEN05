"""Runtime state, local singleton lock, and auth preflight for live fetching."""

from __future__ import annotations

from collections.abc import Mapping
import json
import logging
import os
import socket
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import requests

from core_engine.util.coordination.locks import _local_pid_alive as is_pid_alive
from core_engine.shared.freshness import stale_after_minutes
from core_engine.settings import CACHE_DIR, OVERNIGHT_GAP_MINUTES, TF_MINUTES


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def future_cutoff_ts() -> float:
    return datetime.now(timezone.utc).timestamp() + 60.0


def as_utc_timestamp(ts: datetime) -> float:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    else:
        ts = ts.astimezone(timezone.utc)
    return ts.timestamp()


def is_market_expected_live(
    symbol_meta_by_id: Mapping[int, Mapping[str, object]],
    symbol_id: int,
    now_utc: datetime,
) -> bool:
    meta = symbol_meta_by_id.get(symbol_id, {})
    if meta.get("asset_type") == "Crypto":
        return True

    weekday = now_utc.weekday()
    if weekday == 5:
        return False
    if weekday == 6 and now_utc.hour < 22:
        return False
    if weekday == 4 and now_utc.hour >= 22:
        return False
    return True


def freshness_threshold_minutes(
    symbol_meta_by_id: Mapping[int, Mapping[str, object]],
    symbol_overnight_mins: Mapping[str, int],
    symbol_id: int,
    tf_code: str,
) -> int:
    tf_min = int(TF_MINUTES.get(tf_code, 60))
    meta = symbol_meta_by_id.get(symbol_id, {})
    asset_type = meta.get("asset_type")
    tv_symbol = meta.get("tv_symbol")

    if asset_type == "Crypto":
        overnight_min = 0
    elif tv_symbol in symbol_overnight_mins:
        overnight_min = int(symbol_overnight_mins.get(str(tv_symbol), 0))
    else:
        overnight_min = int(OVERNIGHT_GAP_MINUTES.get(str(asset_type), 0))

    return stale_after_minutes(tf_min, overnight_min)


def freshness_alert_threshold_minutes(
    symbol_meta_by_id: Mapping[int, Mapping[str, object]],
    symbol_overnight_mins: Mapping[str, int],
    symbol_id: int,
    tf_code: str,
) -> int:
    """Return the age that is serious enough to affect operator alerts.

    The normal freshness threshold is intentionally tight for internal
    diagnostics. Discord/operator alerts need more context: high timeframes do
    not print often, and non-crypto CFDs can be quiet outside their active
    session. This function only changes health severity; it does not change
    fetching, database writes, or backfill behavior.
    """

    base = freshness_threshold_minutes(
        symbol_meta_by_id,
        symbol_overnight_mins,
        symbol_id,
        tf_code,
    )
    meta = symbol_meta_by_id.get(symbol_id, {})
    asset_type = str(meta.get("asset_type") or "")
    tf_code = str(tf_code).upper()

    if asset_type == "Crypto":
        minimums = {
            "M5": 30,
            "M10": 45,
            "M15": 60,
            "M20": 90,
            "M30": 120,
            "M45": 180,
            "H1": 240,
            "M90": 300,
            "H2": 360,
            "H3": 480,
            "H4": 720,
            "H6": 900,
            "H8": 1080,
            "D1": 2160,
            "W": 12960,
        }
    elif tf_code == "W":
        minimums = {"W": 12960}  # 9 days; weekly bars should not warn mid-week.
    elif tf_code == "D1":
        minimums = {"D1": 7200}  # 5 days; tolerates weekend/holiday gaps.
    elif asset_type == "Indice":
        minimums = {
            "M5": 720,
            "M10": 720,
            "M15": 720,
            "M20": 720,
            "M30": 720,
            "M45": 720,
            "H1": 720,
            "M90": 720,
            "H2": 720,
            "H3": 960,
            "H4": 1080,
            "H6": 1440,
            "H8": 1440,
        }
    elif asset_type == "Metal":
        minimums = {
            "M5": 360,
            "M10": 360,
            "M15": 360,
            "M20": 360,
            "M30": 360,
            "M45": 360,
            "H1": 480,
            "M90": 480,
            "H2": 600,
            "H3": 720,
            "H4": 900,
            "H6": 1080,
            "H8": 1440,
        }
    else:
        minimums = {}

    return max(base, int(minimums.get(tf_code, 0)))


class LiveStateWriter:
    def __init__(self, path: Path, logger: logging.Logger) -> None:
        self.path = path
        self.logger = logger
        self._lock = threading.Lock()
        self._last_warning_at = 0.0

    def write(self, **updates) -> None:
        current_pid = os.getpid()
        active_statuses = {
            "starting",
            "running",
            "waiting",
            "batch_running",
            "batch_stale_released",
            "network_blocked",
        }
        with self._lock:
            payload = {"pid": current_pid, "updated_at": utc_iso(), "status": "running"}
            try:
                if self.path.exists():
                    existing = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(existing, dict) and int(existing.get("pid") or 0) == current_pid:
                        payload.update(existing)
            except Exception:
                pass

            payload.update(updates)
            status_text = str(payload.get("status") or "")
            if status_text == "stopped":
                payload["previous_pid"] = current_pid
                payload["pid"] = None
            else:
                payload["pid"] = current_pid
            payload["updated_at"] = utc_iso()
            if status_text in active_statuses:
                for key in (
                    "stopped_at",
                    "stop_reason",
                    "previous_pid",
                    "lock_error",
                    "active_pid",
                    "active_started",
                    "handoff_wait_seconds",
                    "handoff_elapsed_seconds",
                ):
                    payload.pop(key, None)

            data = json.dumps(payload, ensure_ascii=True, sort_keys=True)
            last_error: Exception | None = None
            for attempt in range(6):
                tmp = self.path.with_name(f"{self.path.name}.{current_pid}.{time.time_ns()}.tmp")
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    tmp.write_text(data, encoding="utf-8")
                    tmp.replace(self.path)
                    return
                except OSError as exc:
                    last_error = exc
                    try:
                        tmp.unlink(missing_ok=True)
                    except OSError:
                        pass
                    if attempt < 5:
                        time.sleep(min(0.05 * (2**attempt), 0.8))
                except Exception as exc:
                    last_error = exc
                    break

            now = time.time()
            if now - self._last_warning_at >= 60:
                self._last_warning_at = now
                self.logger.warning(
                    "Could not write ws_live state heartbeat after retries: %s",
                    last_error,
                    exc_info=True,
                )

    def heartbeat_loop(self, stop_event: threading.Event, interval_sec: int) -> None:
        while not stop_event.wait(interval_sec):
            self.write(heartbeat="alive")


class TradingViewConnectivityProbe:
    def __init__(self, *, enabled: bool, timeout_sec: float, cooldown_sec: int) -> None:
        self.enabled = enabled
        self.timeout_sec = timeout_sec
        self.cooldown_sec = cooldown_sec
        self.block_until = 0.0
        self.last_error = ""

    def check(self) -> tuple[bool, str]:
        if not self.enabled:
            return True, "preflight disabled"
        now = time.time()
        if now < self.block_until:
            remaining = max(0.0, self.block_until - now)
            return False, f"cooldown active for {remaining:.0f}s after {self.last_error}"
        try:
            resp = requests.get(
                "https://www.tradingview.com/",
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
                    )
                },
                timeout=(2.0, self.timeout_sec),
                allow_redirects=True,
            )
            if resp.status_code < 500:
                self.last_error = ""
                self.block_until = 0.0
                return True, f"HTTP {resp.status_code}"
            reason = f"HTTP {resp.status_code}"
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
        self.last_error = reason[:240]
        self.block_until = time.time() + self.cooldown_sec
        return False, self.last_error


class LocalRuntimeLock:
    def __init__(self, path: Path, logger: logging.Logger) -> None:
        self.path = path
        self.logger = logger

    @staticmethod
    def _pid_matches_live_fetch(pid: int) -> bool:
        if pid <= 0:
            return False
        if os.name == "nt":
            try:
                script = (
                    "$p = Get-CimInstance Win32_Process -Filter \"ProcessId=%d\" "
                    "-ErrorAction SilentlyContinue; "
                    "if ($p) { $p.CommandLine }"
                ) % int(pid)
                result = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=5,
                    check=False,
                )
                cmd = (result.stdout or "").strip().lower()
                if not cmd:
                    return False
                return ("live_fetching" in cmd) or ("live" in cmd and "fetch" in cmd)
            except Exception:
                pass
            return is_pid_alive(pid)
        return is_pid_alive(pid)

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(str(os.getpid()))
                return True
            except FileExistsError:
                try:
                    existing_pid = int(self.path.read_text(encoding="utf-8").strip())
                except Exception:
                    existing_pid = 0
                if existing_pid and self._pid_matches_live_fetch(existing_pid):
                    self.logger.error(
                        "[LOCK] Local ws_live process is already running (pid=%d). Startup aborted.",
                        existing_pid,
                    )
                    return False
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    continue
                except Exception as exc:
                    self.logger.error("[LOCK] Could not remove stale local runtime lock: %s", exc)
                    return False

    def release(self) -> None:
        try:
            existing_pid = int(self.path.read_text(encoding="utf-8").strip())
        except Exception:
            existing_pid = 0
        if existing_pid == os.getpid():
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass


def runtime_payload(*, started_at: str | None = None) -> str:
    now = datetime.utcnow().isoformat(timespec="seconds")
    return (
        f"host={socket.gethostname()};"
        f"pid={os.getpid()};"
        f"started={started_at or now};"
        f"heartbeat={now}"
    )


def parse_lock_payload(payload: str | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for part in str(payload or "").split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def cleanup_dead_runtime_lock(*, task_name: str, get_connection: Callable, logger: logging.Logger) -> bool:
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT Payload
            FROM SEN.ActiveTask
            WHERE TaskName = ? AND ExpiresAt > SYSUTCDATETIME()
            """,
            (task_name,),
        )
        row = cur.fetchone()
        if not row:
            return False
        meta = parse_lock_payload(row[0])
        host = meta.get("host", "")
        pid_text = meta.get("pid", "")
        if host and host.lower() != socket.gethostname().lower():
            return False
        try:
            pid = int(pid_text)
        except ValueError:
            return False
        if is_pid_alive(pid):
            return False
        cur.execute("DELETE FROM SEN.ActiveTask WHERE TaskName = ? AND Payload = ?", (task_name, row[0]))
        conn.commit()
        return cur.rowcount > 0
    except Exception as exc:
        logger.debug("[LOCK] Dead %s cleanup skipped: %s", task_name, exc)
        return False
    finally:
        if conn is not None:
            conn.close()


def playwright_browser_status() -> tuple[bool, str]:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        return False, "playwright package is not installed"

    browser_cache = CACHE_DIR / "playwright-browsers"
    old_browser_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    try:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_cache)
        with sync_playwright() as pw:
            executable = Path(pw.chromium.executable_path)
            if executable.exists():
                return True, str(executable)
            return False, f"chromium executable missing: {executable}"
    except Exception as exc:
        return False, str(exc)
    finally:
        if old_browser_path is None:
            os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
        else:
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = old_browser_path


def run_auth_preflight(
    *,
    tv_auth,
    logger: logging.Logger,
    token_source: str,
    static_cookie: str,
    username: str,
    password: str,
    guest_policy: str,
    require_headless: bool,
    alert: Callable[[str, str], None],
) -> bool:
    """Log live auth readiness before batches start without exposing secrets."""
    cache = tv_auth.load_token_cache()
    current_token = tv_auth.get_current_token()
    ttl = tv_auth.token_expires_in(current_token)
    ttl_text = "unknown" if ttl < 0 else f"{int(ttl)}s"
    has_cookie = bool(tv_auth.get_current_cookie() or static_cookie or cache.get("TV_COOKIE"))
    has_userpass = bool(username and password)
    headless_ok, headless_detail = playwright_browser_status()

    logger.info(
        "[PREFLIGHT] auth_source=%s token=%s ttl=%s cookie=%s userpass=%s "
        "headless=%s guest_policy=%s",
        token_source,
        "guest" if current_token == tv_auth.GUEST_TOKEN else "authenticated",
        ttl_text,
        "yes" if has_cookie else "no",
        "yes" if has_userpass else "no",
        "ready" if headless_ok else "not-ready",
        guest_policy,
    )
    if not headless_ok:
        logger.warning("[PREFLIGHT] Headless TradingView renewal is not ready: %s.", headless_detail)
        if require_headless:
            alert(
                "ERROR",
                "Live feed login check failed\n"
                "Reason: Playwright Chromium is not ready for automatic TradingView refresh.\n"
                "Suggested action: run initial setup to install Chromium.",
            )
            return False

    if tv_auth.has_2fa_secret():
        try:
            import pyotp  # noqa: F401
        except ImportError:
            logger.warning("[PREFLIGHT] TRADINGVIEW.two_fa_secret is set but pyotp is not installed.")

    if current_token == tv_auth.GUEST_TOKEN and guest_policy == "abort":
        alert(
            "ERROR",
            "Live feed login check failed\n"
            "Reason: TradingView is using limited access and the policy does not allow it.\n"
            "Suggested action: refresh TradingView login, then start live feed again.",
        )
        return False
    return True



