# Redis Signal Pipeline — Implementation Plan v3.2

**Version:** 3.2 (post-audit revision 4)
**Date:** 2026-06-10
**Author:** SEN05 Team
**Status:** ✅ APPROVED Phase 1+2+3 (audit 5) — Ready for implementation. Phase 4/live chờ OF plan.

> **Revision history:**
> v1.0 (6×P1) → v2.0 (4×P1,5×P2) → v3.0 (1×P1,4×P2) → v3.1 (3×P1,3×P2) → v3.2 (audit 4: 0 P1).
> **Audit 5: KHÔNG còn P1. Approve Phase 1+2+3 để code** (Phase 3 staging only, không OF live). 2 P2 wording/test đã áp ở dưới.

---

## 0. Trạng Thái Approve

| Phase | Nội dung | Trạng thái |
|---|---|---|
| **Phase 1** | Foundation: `delivery_outbox.py`, `redis_publisher.py`, lock `state.py`, `atr`, `requirements.txt` | ✅ Approved (audit 5) |
| **Phase 2** | OG side: `signal_watcher.py` refactor + `ws_live.py` hook | ✅ Approved (audit 5) |
| **Phase 3** | Staging: bật `REDIS_ENABLED=true`, crash/delivery test — **staging/demo only, KHÔNG OF live** | ✅ Approved (audit 5) |
| **Phase 4** | OF side + live | ⛔ Chờ OF Implementation Plan riêng |

**Merge gate (bắt buộc pass trước khi bật staging):**
1. `REDIS_ENABLED=false` → KHÔNG tạo/sửa `delivery_outbox.json`
2. `redis_on=True` + notifier fail → outbox vẫn `add_pending`
3. Redis `MINID` integration thật (XADD OK, XLEN tăng)
4. Crash/restart → relay drain pending

**Nguyên tắc:** Phase 4 (OF live đặt lệnh thật) chờ OF plan có persistent `processed_signals`, duplicate test, stale reject, live enable flag, reconciliation.

---

## 1. Bối Cảnh & Phạm Vi

### Mục tiêu
Kết nối OG (signal_watcher) → OF (cTrader cBots) qua Redis, tối ưu latency từ data-vào-DB đến OF-nhận-signal, với **delivery guarantee** độc lập với alert delivery.

### Ngoài phạm vi
- OF/cBot implementation — plan riêng, trước E2E live
- Thay đổi batch design ws_live
- cTrader native execution path

### Hai deliverable song song
```
Plan này (v3.2):  OG side — ws_live → bar_ready → signal_watcher → outbox → Redis Streams
Plan riêng (TODO): OF side — Streams consumer, persistent processed_signals store, stale reject, idempotency
```

---

## 2. Kiến Trúc Mục Tiêu (v3.2)

```
VM-DP  ws_live.py — 3 hook sau MỖI _set_committed_watermark():
    line 1438 (direct) · 1481 (deferred main) · 1505 (deferred shutdown)
    └─→ _notify_bar_ready(tv_symbol, tf, max_ts) → PUBLISH bar_ready:{symbol}:{tf}

                │ Redis pub/sub (internal event bus OG-only)
                ▼

VM-OG  signal_watcher.py  (redis_on = REDIS_ENABLED truthy)
  ┌─────────────────────────────────────────────────────────────────────┐
  │  Thread A — BarReadySubscriber: psubscribe → _enqueue(event)         │
  │  Thread B — FallbackScanner (300s): MAX(bartime) → _enqueue(event)   │
  │                                                                     │
  │  _enqueue() — COALESCING:                                           │
  │    ck=(symbol,tf,normalize(bartime)); nếu in-flight → DROP          │
  │                                                                     │
  │  Worker thread (single) — DETECT, dedup duy nhất:                   │
  │    event = event_queue.get()                                        │
  │    try:                                                             │
  │      groups = _find_matching_groups(symbol,tf,runtime_scan_groups)  │
  │      for group in groups:                                           │
  │        dedup: event.bartime vs last_processed[gkey][symbol]        │
  │        → pipeline detect → với mỗi signal HỢP LỆ (new+current):    │
  │            ┌── DETECTED VALID SIGNAL ──────────────────────────┐  │
  │            │ if redis_on and not dry_run:                       │  │
  │            │   signal_id = _compute_signal_id(...+event_type)   │  │
  │            │   payload   = build(produced_at, ...)              │  │
  │            │   outbox.add_pending(signal_id, payload) ← persist │  │
  │            │   _wake_relay.set()                                │  │
  │            │ [ĐỘC LẬP với notifier — P1-2]                      │  │
  │            └────────────────────────────────────────────────────┘  │
  │            result = notifier.send(...)   ← best-effort alert        │
  │            if result.sent and not dry_run: state.add(key) ← alert  │
  │        last_processed[gkey][symbol] = event.bartime               │
  │    finally:                                                         │
  │      _release_inflight(event)   ← P2-2: luôn release               │
  │    [WORKER KHÔNG XADD]                                              │
  │                                                                     │
  │  Thread D — DeliveryRelay (SOLE XADD AUTHORITY):                    │
  │    while True:                                                      │
  │      # DRAIN TRƯỚC (vòng đầu = startup drain ngay) ← P2-1          │
  │      for (signal_id, entry) in outbox.get_pending():              │
  │        if not _should_retry(entry): continue   ← backoff          │
  │        payload = {**entry.payload, "delivered_at": now()} ←P2-3   │
  │        entry_id = redis_publisher.xadd_signal(payload)            │
  │        if entry_id: outbox.mark_delivered(signal_id)             │
  │        else: outbox.record_attempt(signal_id)                    │
  │      alert nếu oldest_pending > 5 phút                            │
  │      _wake_relay.wait(timeout=30); _wake_relay.clear()  ← cuối    │
  │                                                                     │
  │  Khi redis_on=False: outbox = NullDeliveryOutbox (no-op, KHÔNG    │
  │    ghi file); Thread A+D KHÔNG start ← P1-1                        │
  └─────────────────────────────────────────────────────────────────────┘
                │ Redis Streams (durable, MINID time-based)
                ▼

VM-OF1/OF2  cBot C# (OF plan riêng)
  XREADGROUP → persistent processed_signals store → stale check → process → XACK
```

### Tách biệt 3 concern (cốt lõi v3.2)

| Concern | Cơ chế | Phụ thuộc |
|---|---|---|
| **Execution delivery** (→OF) | outbox.add_pending khi signal hợp lệ + redis_on | **KHÔNG phụ thuộc notifier** (P1-2) |
| **Alert** (Telegram/Discord) | notifier.send() best-effort | độc lập |
| **Alert dedup** | state.add(key) sau khi send thành công | chỉ phục vụ alert |

---

## 3. Thay Đổi so với v3.1 (Post Audit 4)

| Finding | Vấn đề v3.1 | Giải pháp v3.2 |
|---|---|---|
| **P1-1** | `REDIS_ENABLED=false` vẫn gọi `outbox.add_pending()` vô điều kiện → tích pending, replay khi bật Redis | `NullDeliveryOutbox` khi off + guard `if redis_on and not dry_run` trong worker. Test: off → không tạo/sửa file |
| **P1-2** | Outbox chỉ chạy trong `if result.sent` → Telegram lỗi thì OF miss signal | Tách: outbox theo signal hợp lệ (độc lập notifier). `state.add` vẫn chỉ sau send thành công |
| **P1-3** | `xadd(minid="~{ms}-0")` sai redis-py API → XADD fail liên tục | `xadd(minid="{ms}-0", approximate=True)` + integration test thật |
| P2-1 | Relay `wait()` đầu loop → startup drain trễ 30s | Drain trước, wait ở cuối loop |
| P2-2 | `_release_inflight()` không trong finally → exception kẹt coalescing | Đưa vào `finally` |
| P2-3 | `created_at` nói "đổi mỗi retry" nhưng persist 1 lần → sai | `produced_at` (cố định) + relay inject `delivered_at` (mỗi XADD) |

---

## 4. Signal Payload v3.2 — LOCKED

```json
{
  "signal_id":       "a3f1c2e4b5d6a7b8c9d0e1f2a3b4c5d6",
  "schema_version":  "3.2",
  "produced_at":     "2026-06-10T10:02:35.123Z",
  "delivered_at":    "2026-06-10T10:02:36.001Z",
  "expires_at":      "2026-06-10T11:07:35.123Z",
  "producer_run_id": "a1b2c3d4-e5f6-...",

  "strategy":        "combo",
  "event_type":      "",
  "symbol":          "US30",
  "timeframe":       "H1",
  "direction":       1,
  "bar_time":        "2026-06-10T10:00:00Z",
  "signal_time":     "2026-06-10T10:02:35Z",
  "atr":             45.2
}
```

| Field | Ý nghĩa | Khi nào set |
|---|---|---|
| `produced_at` | Thời điểm worker detect signal | 1 lần, lúc build payload (cố định) — P2-3 |
| `delivered_at` | Thời điểm relay XADD | Inject mỗi lần XADD (đổi khi retry) — đo delivery lag |
| `signal_id` | Deterministic, dedup key | `sha256(strategy\|event_type\|symbol\|tf\|bar_time\|direction\|overrides_hash)[:32]` |

### Deterministic `signal_id` (gồm event_type)

```python
def _compute_signal_id(strategy, event_type, symbol, tf,
                        bar_time, direction, overrides_hash) -> str:
    key = "|".join([
        strategy.lower(),
        (event_type or "").upper(),
        symbol.upper(),
        tf.upper(),
        pd.Timestamp(bar_time).strftime("%Y%m%dT%H%M%S"),
        str(int(direction)),
        overrides_hash,
    ])
    return hashlib.sha256(key.encode()).hexdigest()[:32]
```

---

## 5. Chi Tiết Thay Đổi Từng File

### 5.1 `core_python/notify/redis_publisher.py` — TẠO MỚI

```
publish_bar_ready(symbol, tf, bartime) → PUBLISH bar_ready:{symbol}:{tf}
xadd_signal(payload) → str|None  (XADD signal_stream:{strategy}, MINID time-based)
_get_client() → lazy init, timeout 2s ; _enabled() → REDIS_ENABLED
```

**MINID — redis-py API ĐÚNG (P1-3 fix):**
```python
STREAM_RETENTION_DAYS = int(os.getenv("REDIS_STREAM_RETENTION_DAYS", "7"))

def xadd_signal(payload: dict) -> str | None:
    if not _enabled():
        return None
    r = _get_client()
    if r is None:
        return None
    stream = f"signal_stream:{payload['strategy']}"
    minid_ms = int((time.time() - 86400 * STREAM_RETENTION_DAYS) * 1000)
    try:
        # ĐÚNG: minid là stream-ID form "{ms}-0", '~' truyền qua approximate=True.
        # SAI (v3.1): minid=f"~{minid_ms}-0" → '~' không được nằm trong ID.
        return r.xadd(
            stream,
            _flatten(payload),               # dict[str,str] — Streams chỉ nhận field phẳng
            minid=f"{minid_ms}-0",
            approximate=True,
        )
    except Exception as exc:
        logger.warning("[Redis] XADD failed (%s): %s", stream, exc)
        return None
```

`_flatten()`: convert payload dict → flat `dict[str, str]` (JSON-encode nested nếu có; ở đây toàn scalar).

**Integration test bắt buộc (P1-3):** với Redis thật — `XADD ... MINID ~ <ms>-0` thành công, entry xuất hiện trong stream, `XLEN` tăng.

**Ước lượng:** ~110 dòng

---

### 5.2 `core_python/notify/delivery_outbox.py` — TẠO MỚI

**`DeliveryOutbox`** (file: `{WATCHER_STATE_DIR}/delivery_outbox.json`):
```python
class DeliveryOutbox:
    def __init__(self, path):
        self._path = path
        self._lock = threading.Lock()
        self._data = self._load()   # {"pending":{}, "delivered":{}}

    def add_pending(self, signal_id, payload):   # idempotent
        with self._lock:
            if signal_id in self._data["delivered"]:
                return
            if signal_id not in self._data["pending"]:
                self._data["pending"][signal_id] = {
                    "payload": payload, "produced_at": _utcnow_iso(),
                    "attempts": 0, "last_attempt": None,
                }
                self._write()

    def record_attempt(self, signal_id):  # attempts++, last_attempt (backoff)
        ...
    def mark_delivered(self, signal_id):  # pop pending → delivered[id]=now
        ...
    def get_pending(self) -> dict: ...
    def oldest_pending_age_seconds(self) -> float | None: ...
    def prune_delivered(self, older_than_days=7): ...
    def _write(self):  # atomic tmp → rename
        ...
```

**`NullDeliveryOutbox` (P1-1 fix) — dùng khi `REDIS_ENABLED=false`, KHÔNG đụng file:**
```python
class NullDeliveryOutbox:
    """No-op — đảm bảo REDIS_ENABLED=false không tạo/sửa delivery_outbox.json."""
    def add_pending(self, *a, **k): pass
    def record_attempt(self, *a, **k): pass
    def mark_delivered(self, *a, **k): pass
    def get_pending(self): return {}
    def oldest_pending_age_seconds(self): return None
    def prune_delivered(self, *a, **k): pass
```

**Ước lượng:** ~150 dòng

---

### 5.3 `data_provider/apps/ws_live.py` — HOOK 3 VỊ TRÍ (đã xác nhận)

Quy tắc: `_notify_bar_ready()` sau **MỖI** `_set_committed_watermark()` — đúng 3 call site (`defer_key` giữ `tv_symbol` nên `sym_nm == tv_symbol`):

```python
# Hook 1 (line ~1438, direct):           _notify_bar_ready(tv_symbol, tf_code, max_committed_ts)
# Hook 2 (line ~1481, deferred main):    _notify_bar_ready(sym_nm, tf_c, max_ts)
# Hook 3 (line ~1505, deferred shutdown):_notify_bar_ready(sym_nm, tf_c, max_ts)
# KHÔNG hook line ~1416 (chỉ stage — Fact chưa có data)
```

```python
def _notify_bar_ready(symbol, tf, bartime_ts):
    if not _REDIS_ENABLED:
        return
    try:
        bartime_iso = datetime.utcfromtimestamp(bartime_ts).isoformat() + "Z"
        redis_publisher.publish_bar_ready(symbol, tf, bartime_iso)
    except Exception as exc:
        logger.warning("[Redis] bar_ready publish failed: %s", exc)
```

**Ước lượng:** +20-25 dòng

---

### 5.4 `core_python/notify/signal_watcher.py` — REFACTOR LỚN

#### 5.4.1 Giữ nguyên
`check_once()`/`check_ai_trend_once()` pipeline logic (chỉ chèn block emit), `all_new_signal_rows()`, `run_strategy_frame()`, `run_ai_trend_alerts()`, `--dry-run/--once/--warm-up`, fault isolation.

#### 5.4.2 Loại bỏ
`_next_bar_close_utc()`, `_schedule_next_run()`, `_db_bar_has_not_advanced()`, `next_run_at`, `retry_until`.

#### 5.4.3 Coalescing enqueue

```python
_inflight: set[tuple] = set()
_inflight_lock = threading.Lock()

def _normalize_bartime(bartime: str) -> str:
    if not bartime:
        return ""   # rỗng → không coalesce (luôn enqueue)
    try:
        return pd.Timestamp(bartime).strftime("%Y%m%dT%H%M%S")
    except Exception:
        return bartime

def _enqueue(event_queue, event) -> bool:
    norm = _normalize_bartime(event.bartime)
    ck = (event.symbol.upper(), event.tf.upper(), norm)
    if norm:
        with _inflight_lock:
            if ck in _inflight:
                return False     # coalesce
            _inflight.add(ck)
    try:
        event_queue.put(event, timeout=5.0)
        return True
    except queue.Full:
        with _inflight_lock:
            _inflight.discard(ck)
        logger.error("[Queue] FULL — drop %s %s", event.symbol, event.tf)
        return False

def _release_inflight(event) -> None:
    norm = _normalize_bartime(event.bartime)
    if not norm:
        return
    with _inflight_lock:
        _inflight.discard((event.symbol.upper(), event.tf.upper(), norm))
```

#### 5.4.4 Worker loop — `_release_inflight` trong finally (P2-2 fix)

```python
def _worker_loop(state, notifier, outbox, scan_groups, args, redis_on):
    while True:
        try:
            event = event_queue.get(timeout=1.0)
        except queue.Empty:
            continue
        try:
            _handle_event(event, state, notifier, outbox, scan_groups, args, redis_on)
        except Exception as exc:
            logger.error("Worker error: %s", exc, exc_info=True)
        finally:
            _release_inflight(event)     # LUÔN release dù exception
            event_queue.task_done()
```

#### 5.4.5 last_processed bartime + Thread B không đọc state + runtime scan_groups
(Giữ từ v3.0/v3.1)
```python
# Dedup bằng bartime:
last = last_processed.get(gkey, {}).get(symbol)
if last is not None and event.bartime and pd.Timestamp(event.bartime) <= last:
    return
# ... pipeline ...
last_processed.setdefault(gkey, {})[symbol] = pd.Timestamp(event.bartime)

# Thread B luôn _enqueue, không đọc last_processed
# _find_matching_groups(symbol, tf, scan_groups)  ← runtime param
```

#### 5.4.6 Emit signal — ĐỘC LẬP với notifier (P1-1 + P1-2 fix)

**Helper dùng chung cho cả combo và ai_trend:**
```python
def _emit_to_outbox(redis_on, dry_run, outbox, *, strategy, event_type,
                    symbol, tf, row_or_alert, event_close, overrides_hash):
    """Đưa signal hợp lệ vào outbox — ĐỘC LẬP với notifier success.
       No-op khi redis off hoặc dry-run (P1-1)."""
    if not redis_on or dry_run:
        return
    signal_id = _compute_signal_id(
        strategy, event_type, symbol, tf,
        _bar_time_of(row_or_alert), _direction_of(row_or_alert), overrides_hash)
    payload = _build_signal_payload(
        signal_id, strategy, event_type, symbol, tf, row_or_alert, event_close)
    outbox.add_pending(signal_id, payload)   # idempotent (deterministic id)
    _wake_relay.set()
```

**Tích hợp trong `check_once()` (combo) — sau check is_historical, TRƯỚC notifier.send():**
```python
for row in new_rows:                      # new_rows: đã lọc dedup qua state (line 429)
    key = signal_key(strategy, symbol, tf, row["bartime"], int(row["signal"]))
    event_close = _event_close_from_bar_open(row["bartime"], tf)
    is_historical, age, age_limit = _is_historical_alert(event_close, tf, ...)
    if is_historical:
        _mark_historical_seen(state, key, dry_run=notifier.dry_run)
        continue

    # ── DETECTED VALID SIGNAL (new + current + non-historical) ──
    _emit_to_outbox(redis_on, notifier.dry_run, outbox,
                    strategy=strategy, event_type="",
                    symbol=symbol, tf=tf, row_or_alert=row,
                    event_close=event_close, overrides_hash=_overrides_hash(overrides))

    if export_on_signal: ...               # export CSV
    message = format_combo_raw_signal_message(...) / format_signal_message(...)
    result = notifier.send(...)            # best-effort

    if result.sent and not notifier.dry_run:
        state.add(key)                     # alert dedup — chỉ sau send thành công
        ...sent_signals/events...
    elif result.sent: ...dry-run...
    else: ...FAILED...                     # ← OF KHÔNG miss: signal đã vào outbox
```

**Tích hợp trong `check_ai_trend_once()`** — sau check `_ai_trend_m45_matches_h3` + `is_historical`, trước `notifier.send()` (line ~573): cùng pattern, `event_type=normalized_event_type`.

> **Hệ quả P1-2 (wording chính xác — audit 5 P2-1):** Discord/Telegram down → `result.sent=False` → state KHÔNG add → signal vẫn "new". **Nhưng `last_processed[gkey][symbol]=event.bartime` đã set → cùng bar bị suppress trong cùng process** (không retry alert ngay). Alert là **best-effort**: chỉ retry khi (a) bar MỚI trigger pipeline và signal cũ chưa quá `max_alert_age` (historical limit), hoặc (b) process restart. Execution KHÔNG bị ảnh hưởng: outbox đã add (idempotent) → relay deliver tới OF. Nếu sau này cần alert retry chắc chắn → thêm alert retry queue riêng (ngoài scope plan này).

#### 5.4.7 Thread A subscriber (giữ nguyên)
```python
def _bar_ready_subscriber_loop(event_queue):
    r = redis_publisher._get_client()
    if r is None: return
    pubsub = r.pubsub(); pubsub.psubscribe("bar_ready:*")
    for msg in pubsub.listen():
        if msg["type"] != "pmessage": continue
        parts = msg["channel"].split(":")
        if len(parts) != 3: continue
        _, symbol, tf = parts
        _enqueue(event_queue, GroupTriggerEvent("bar_ready", symbol, tf, msg.get("data","")))
```

#### 5.4.8 Thread D — DeliveryRelay (drain trước, P2-1 + P2-3)

```python
_wake_relay = threading.Event()
RELAY_RETRY_INTERVAL = int(os.getenv("RELAY_RETRY_INTERVAL", "30"))
RELAY_ALERT_AGE      = int(os.getenv("RELAY_ALERT_AGE", "300"))

def _should_retry(entry) -> bool:
    last = entry.get("last_attempt")
    if last is None: return True
    backoff = min(2 ** entry["attempts"], 300)
    return (datetime.utcnow() - _parse(last)).total_seconds() >= backoff

def _delivery_relay_loop(outbox):
    alerted = False
    while True:
        try:
            # DRAIN TRƯỚC — vòng đầu = startup drain ngay (không đợi 30s) — P2-1
            for signal_id, entry in outbox.get_pending().items():
                if not _should_retry(entry):
                    continue
                payload = {**entry["payload"], "delivered_at": _utcnow_iso()}  # P2-3
                entry_id = redis_publisher.xadd_signal(payload)
                if entry_id:
                    outbox.mark_delivered(signal_id)
                else:
                    outbox.record_attempt(signal_id)
            age = outbox.oldest_pending_age_seconds()
            if age is not None and age > RELAY_ALERT_AGE and not alerted:
                _tg_alert("ERROR", f"Redis delivery stuck — oldest pending {age:.0f}s")
                alerted = True
            elif age is None:
                alerted = False
        except Exception as exc:
            logger.error("[Relay] loop error: %s", exc, exc_info=True)
        # WAIT Ở CUỐI — P2-1
        _wake_relay.wait(timeout=RELAY_RETRY_INTERVAL)
        _wake_relay.clear()
```

#### 5.4.9 Main startup — NullDeliveryOutbox khi off (P1-1)

```python
def main():
    global _PRODUCER_RUN_ID
    _PRODUCER_RUN_ID = str(uuid.uuid4())
    state    = SignalState(STATE_PATH)
    notifier = ...
    scan_groups = _load_scan_groups(args)
    event_queue = queue.Queue(maxsize=1000)

    redis_on = redis_publisher._enabled() and not args.dry_run
    # P1-1: off → NullDeliveryOutbox, KHÔNG mở/ghi delivery_outbox.json
    outbox = DeliveryOutbox(OUTBOX_PATH) if redis_on else NullDeliveryOutbox()

    if redis_on:
        Thread(target=_delivery_relay_loop, args=(outbox,), daemon=True).start()   # D
        Thread(target=_bar_ready_subscriber_loop, args=(event_queue,), daemon=True).start()  # A
    Thread(target=_fallback_scanner_loop,
           args=(event_queue, FALLBACK_INTERVAL, scan_groups), daemon=True).start()  # B
    Thread(target=_worker_loop,
           args=(state, notifier, outbox, scan_groups, args, redis_on), daemon=True).start()  # Worker

    while True:
        time.sleep(60)
        logger.info("[Metric] queue=%d inflight=%d oldest_pending=%s",
                    event_queue.qsize(), len(_inflight),
                    outbox.oldest_pending_age_seconds())
        outbox.prune_delivered(older_than_days=7)
```

**Ước lượng:** Refactor ~250 dòng, thêm ~320 dòng

---

### 5.5 `core_python/notify/state.py` — THÊM LOCK
```python
class SignalState:
    def __init__(self, path):
        self._lock = threading.Lock(); ...
    def add(self, key):
        with self._lock:
            self.sent[key] = pd.Timestamp.now("UTC").isoformat(); self._write()
    def has(self, key):
        with self._lock:
            return key in self.sent
```

### 5.6 `core_python/strategies/ai_trend/alerts.py` — THÊM `atr`
Thêm `atr` vào alert/row output để `_build_signal_payload()` đọc.

### 5.7 `requirements.txt`
```
redis>=5.0.0
```

### 5.8 `.env`
```bash
REDIS_ENABLED=true
REDIS_HOST=10.11.12.8
REDIS_PORT=6379
REDIS_PASSWORD=<REPLACE_WITH_ACTUAL_PASSWORD>
REDIS_STREAM_RETENTION_DAYS=7
WATCHER_FALLBACK_INTERVAL=300
RELAY_RETRY_INTERVAL=30
RELAY_ALERT_AGE=300
```

---

## 6. Redis Streams — Consumer Side (OF Plan Required)

| Yêu cầu | Mô tả |
|---|---|
| Consumer group | XGROUP CREATE — mỗi OF 1 consumer |
| XREADGROUP / XACK | Đọc chưa ACK / ACK sau order submit |
| **Persistent processed_signals store** | **cBot/sidecar lưu signal_id đã xử lý — KHÔNG dựa broker dedup (P1-4 audit2)** |
| Stale rejection | Check `expires_at` vs clock NTP |
| Idempotency | signal_id trong store → XACK + skip |
| Pending recovery | XAUTOCLAIM sau timeout |
| Live enable flag | Gate trước đặt lệnh thật |
| Duplicate test | Cùng signal_id 2 lần → OF xử lý 1 lần — bắt buộc trước live |

**Prerequisite:** OF plan approve + implement trước khi bật `REDIS_ENABLED=true` production.

---

## 7. Dedup — Bốn Lớp (v3.2)

```
Lớp 0 — outbox: signal_id → pending/delivered. Đảm bảo XADD ≥1 lần. Relay 30s+backoff. Lock.
Lớp 1 — in-flight set: (symbol,tf,bartime) coalescing. _release trong finally.
Lớp 2 — last_processed: (strategy,event_type,symbol,tf,overrides_hash)→bartime. Per-bar.
Lớp 3 — state.json: alert dedup. Lock. CHỈ phục vụ alert (P1-2 tách khỏi delivery).
Lớp 4 — OF processed_signals: signal_id persistent + duplicate test (OF plan).
```

---

## 8. Kịch Bản Lỗi & Xử Lý (v3.2)

| Kịch bản | v3.1 | v3.2 |
|---|---|---|
| **Telegram/Discord down** | **OF miss signal (P1-2)** | **Outbox đã add (độc lập) → relay deliver. Alert best-effort (retry ở bar mới/restart, trong historical limit)** |
| **REDIS_ENABLED=false** | **Vẫn ghi outbox, replay khi bật (P1-1)** | **NullDeliveryOutbox + guard → KHÔNG ghi file** |
| **XADD MINID sai API** | **Fail liên tục (P1-3)** | **minid="{ms}-0", approximate=True + integration test** |
| Redis down mid-run nhiều ngày | Thread D retry 30s | Thread D retry 30s |
| Startup có pending | Đợi 30s mới drain | Drain ngay vòng đầu (P2-1) |
| Exception trong worker | inflight kẹt | finally release (P2-2) |
| Crash sau add_pending | relay drain | relay drain |
| Thread B trùng bar | coalesce | coalesce |
| bar xử lý trễ | bartime dedup | bartime dedup |
| OF duplicate cùng signal_id | OF store | OF store |

---

## 9. Backward Compatibility (v3.2)

| Config | Behavior |
|---|---|
| `REDIS_ENABLED=false` (hoặc `--dry-run`) | `outbox = NullDeliveryOutbox`. Thread A+D **không start**. Worker emit block **bị guard** (`redis_on=False`). **KHÔNG tạo/sửa `delivery_outbox.json`**. Thread B + Worker chạy. **Telegram/Discord không đổi**. |
| `REDIS_ENABLED=true` | Full A+B+D+Worker. Outbox active. |

**Phase 2 test ghi rõ:** "Telegram unchanged. `delivery_outbox.json` KHÔNG được tạo/sửa khi REDIS_ENABLED=false. Thread A+D không start."

---

## 10. Thứ Tự Triển Khai (v3.2)

```
Phase 1 — Foundation (approved có điều kiện)
  delivery_outbox.py (+ NullDeliveryOutbox) · redis_publisher.py (minid đúng)
  requirements.txt · state.py lock · alerts.py atr

Phase 2 — OG side (REDIS_ENABLED=false, safe)
  signal_watcher.py refactor (A/B/D/Worker, coalescing, emit độc lập notifier)
    → test --dry-run + REDIS_ENABLED=false: KHÔNG tạo outbox file (P1-1)
    → test last_processed bartime, coalescing, relay backoff/drain
  ws_live.py 3 hook (1438/1481/1505)
  [Lưu ý audit 5 P2-2: test "notifier fail → outbox vẫn add" CẦN redis_on=True,
   KHÔNG chạy được ở Phase 2 (REDIS off). Nó là UNIT test
   (test_emit_independent_of_notifier, dùng fake outbox) — xem mục 11 — hoặc Phase 3.]

Phase 3 — Integration (staging)
  Bật REDIS_ENABLED=true trên VM-OG
    → minid integration test thật (P1-3): XADD MINID ~ <ms>-0 OK, XLEN tăng
    → REDIS DOWN mid-run → outbox pending → start Redis → relay XADD ≤30s
    → CRASH test → restart → relay drain
    → DUPLICATE test → OF stub dedup
    → coalesce dưới tải → queue depth ổn định

Phase 4 — OF side (OF plan riêng)
  OF persistent store + duplicate test + E2E + live enable
```

---

## 11. Test Plan (v3.2)

### Unit
| Test | Scenario | Expected |
|---|---|---|
| `test_redis_disabled_no_outbox_file` | REDIS_ENABLED=false, trigger signal | `delivery_outbox.json` KHÔNG tồn tại/đổi (P1-1) |
| `test_null_outbox_noop` | NullDeliveryOutbox.add_pending | Không I/O, get_pending={} |
| `test_emit_independent_of_notifier` | redis_on, notifier.send fail | outbox.add_pending vẫn được gọi (P1-2) |
| `test_emit_skipped_dry_run` | dry_run=True | outbox không add |
| `test_state_add_only_on_sent` | notifier fail | state KHÔNG add |
| `test_xadd_minid_api` | mock redis, check kwargs | `minid="{ms}-0"`, `approximate=True` (P1-3) |
| `test_signal_id_includes_event_type` | khác event_type | khác id |
| `test_signal_id_deterministic` | same inputs | same id |
| `test_outbox_idempotent` | add 2× | 1 entry |
| `test_relay_drain_before_wait` | 1 pending, start relay | XADD trước khi wait (P2-1) |
| `test_relay_backoff` | attempts=3, last 2s | _should_retry=False |
| `test_relay_injects_delivered_at` | XADD payload | có delivered_at, khác produced_at (P2-3) |
| `test_release_inflight_on_exception` | worker raise | inflight được release (P2-2) |
| `test_coalesce_duplicate` | enqueue 2×(US30,H1,10:00) | 1 enqueued |
| `test_last_processed_bartime` | bar 10:00 xử lý 11:10 | last=10:00 |
| `test_find_matching_groups_runtime` | CLI override | dùng runtime groups |

### Integration (Redis thật — P1-3)
```
redis-cli FLUSHDB
python -m core_python.notify.signal_watcher   # REDIS_ENABLED=true
# Trigger signal → assert:
#   redis-cli XLEN signal_stream:combo == 1
#   redis-cli XRANGE signal_stream:combo - + → có signal_id, produced_at, delivered_at
#   MINID trim không lỗi
```

### Crash/Delivery (Phase 3)
| Test | Method | Assert |
|---|---|---|
| Telegram down | Block webhook, trigger | outbox pending→delivered; alert FAILED |
| Redis off mid-run | Stop Redis, trigger, đợi 2 phút | outbox pending; alert sau 5 phút |
| Redis up lại | Start Redis (không restart) | relay XADD ≤30s |
| Crash | kill -9 sau add_pending | restart → relay drain → XADD |
| Duplicate | XADD cùng id 2 lần | 2 entry; OF stub 1 lần |
| REDIS off file isolation | Chạy REDIS_ENABLED=false 1h | KHÔNG có delivery_outbox.json |

---

## 12. Open Questions

| # | Câu hỏi | Ảnh hưởng | Trạng thái |
|---|---|---|---|
| 1 | Giữ bar-close scheduling khi REDIS off hay dùng Thread B? | Mục 9 | Mở |
| 2 | MINID 7 ngày đủ? OF offline > 7 ngày? | redis_publisher | Mở |
| 3 | OF consumer group name convention? | OF plan | Mở |
| 4 | `expires_at` = 1 TF + 5 phút — OF nghĩ sao? | OF plan | Mở |
| 5 | Capital.com API hỗ trợ `clientOrderId` idempotent? | OF plan | Mở |
| ~~6~~ | ~~Deferred ETL hook~~ | — | ✅ Đóng (1438/1481/1505) |

---

## 13. Tóm Tắt Files

| File | Loại | Ghi chú |
|---|---|---|
| `core_python/notify/delivery_outbox.py` | Tạo mới | DeliveryOutbox + NullDeliveryOutbox |
| `core_python/notify/redis_publisher.py` | Tạo mới | bar_ready + xadd (minid đúng) |
| `core_python/notify/signal_watcher.py` | Refactor lớn | A/B/D/Worker, coalescing, emit độc lập notifier |
| `core_python/notify/state.py` | Sửa nhỏ | threading.Lock |
| `core_python/strategies/ai_trend/alerts.py` | Sửa nhỏ | atr field |
| `data_provider/apps/ws_live.py` | Sửa nhỏ | 3 hook _set_committed_watermark() |
| `requirements.txt` | Sửa nhỏ | redis>=5.0.0 |
| `.env` | Config | REDIS_* + RELAY_* |

**Chưa trong scope:** `cbot_calgo/` OF consumer + processed_signals store → **OF plan riêng**

---

## 14. Đối Chiếu Audit — Lịch Sử

### Audit 5 (v3.2) — ✅ APPROVED Phase 1+2+3
- KHÔNG còn P1 blocker. Approve để code (Phase 3 staging only, không OF live).
- P2-1 (wording notifier retry): sửa mục 5.4.6 — alert best-effort, suppress cùng bar bởi last_processed.
- P2-2 (test placement): chuyển "notifier fail → outbox add" sang unit test / Phase 3 (cần redis_on=True).
- Merge gate + Phase 4 conditions: xem mục 0.

### Audit 4 (v3.1 → v3.2)
| Finding | Giải pháp | Section |
|---|---|---|
| P1-1: REDIS off vẫn ghi outbox | NullDeliveryOutbox + guard redis_on | 5.2, 5.4.6, 5.4.9 |
| P1-2: delivery phụ thuộc notifier | Emit độc lập notifier, state.add chỉ cho alert | 5.4.6, mục 2 |
| P1-3: minid API sai | minid="{ms}-0" + approximate=True + test | 5.1 |
| P2-1: relay wait đầu loop | Drain trước, wait cuối | 5.4.8 |
| P2-2: release_inflight không finally | Đưa vào finally | 5.4.4 |
| P2-3: created_at hiểu nhầm | produced_at + delivered_at | 4, 5.4.8 |

### Closed trước đó
- Audit 3 (v3.0→v3.1): runtime relay · event_type · MINID unify · deferred hook · coalescing
- Audit 2 (v2.0→v3.0): outbox · contract · bartime · OF store · Thread B · runtime groups · REDIS flag · deterministic id · retention
- Audit 1 (v1.0→v2.0): Streams · atomic · single worker · per-group key · hook · OF plan tách

---

*v3.2 — Revised sau audit 4. Mục tiêu: approve Phase 1+2+3 mới bắt đầu code. Phase 4 chờ OF plan.*
