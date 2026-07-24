"""
Lưu trữ trạng thái tín hiệu đã gửi để ngăn gửi trùng lặp.

Mô tả:
    SignalState duy trì một dict {key → timestamp} lưu trong file JSON.
    Mỗi tín hiệu được định danh bởi "signal key" gồm: strategy|symbol|tf|bartime|direction.
    Khi key đã tồn tại, watcher sẽ bỏ qua tín hiệu đó, không gửi lại Telegram.

Cơ chế TTL:
    Mỗi lần nạp state, các key cũ hơn TTL_DAYS ngày sẽ bị xóa.
    TTL phải lớn hơn cửa sổ bars dài nhất để tránh re-alert:
        H2: 500 bars × 120min ≈ 41.7 ngày
        H3: 400 bars × 180min ≈ 50.0 ngày
        H4: 300 bars × 240min ≈ 50.0 ngày
        AI Trend M45 production: 1000 bars × 45min ≈ 31.3 ngày
    → TTL_DAYS = 60 (đảm bảo đủ buffer cho tất cả TF).

Tính an toàn ghi file:
    Ghi vào file .tmp trước, sau đó rename nguyên tử sang file thật.
    Đảm bảo state.json không bị corrupt nếu tiến trình bị giết giữa chừng.

Phụ thuộc ngoài:
    Không có — chỉ dùng stdlib json, pathlib, và pandas cho timestamp.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pandas as pd


DEFAULT_STATE_PATH = Path(__file__).resolve().parents[1] / "runtime" / "state.json"
SCAN_FINGERPRINT_KEY = "scan_fingerprint"

# TTL phải lớn hơn cửa sổ bars dài nhất trong production watcher (H3/H4 ≈ 50 ngày).
# Nếu TTL < window, keys bị prune trong khi bar vẫn còn trong cửa sổ load
# → tín hiệu cũ xuất hiện lại như "mới" và gây flood Telegram.
TTL_DAYS = 60


class SignalState:
    """
    Quản lý tập hợp signal key đã gửi với TTL-based pruning.

    State:
        self.path: Đường dẫn tới file JSON lưu state.
        self.sent: Dict {key: str → iso_timestamp: str} các key đã gửi.

    Lifecycle:
        __init__ → _load() → _prune() → sẵn sàng dùng.
        Mỗi lần add() sẽ ghi lại file ngay lập tức (atomic write).

    Invariant:
        Sau _prune(), tất cả key trong self.sent đều có timestamp
        trong vòng TTL_DAYS ngày gần nhất.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_STATE_PATH
        self.sent: dict[str, str] = {}  # key → ISO timestamp lúc gửi
        self.meta: dict[str, str] = {}
        # Bảo vệ has()/add()/fingerprint khi Worker thread và main (warm-up) cùng
        # truy cập state. _load() chạy 1 lần trong __init__ (đơn luồng) nên không khoá.
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        """
        Nạp state từ file JSON.

        Xử lý 2 định dạng:
        - Format cũ: {"sent": ["key1", "key2"]} (list) → migrate sang dict với timestamp hiện tại.
        - Format mới: {"sent": {"key1": "2024-01-01T...", ...}} (dict với timestamp).

        Nếu file không tồn tại, thử migrate từ các path cũ:
        - core_python/notify/state.json (combo watcher cũ, trước khi chuyển sang runtime/)
        - core_python/runtime/ai_trend_state.json (ai_trend watcher riêng, trước khi hợp nhất)

        Nếu không có path cũ nào, bắt đầu với state rỗng.

        Side Effects:
            Gọi _prune() để xóa key hết TTL sau khi nạp.
        """
        if not self.path.exists():
            _migrate_legacy_states(self.path)
            if not self.path.exists():
                self.sent = {}
                self.meta = {}
                return
        try:
            data: dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.sent = {}
            self.meta = {}
            return
        raw_meta = data.get("meta", {})
        self.meta = {str(k): str(v) for k, v in raw_meta.items()} if isinstance(raw_meta, dict) else {}
        legacy_fingerprint = data.get(SCAN_FINGERPRINT_KEY)
        if legacy_fingerprint and SCAN_FINGERPRINT_KEY not in self.meta:
            self.meta[SCAN_FINGERPRINT_KEY] = str(legacy_fingerprint)
        raw_sent = data.get("sent", [])
        if isinstance(raw_sent, list):
            # Migrate format cũ (list) → dict với timestamp hiện tại
            now_str = pd.Timestamp.now("UTC").isoformat()
            self.sent = {str(k): now_str for k in raw_sent}
        else:
            self.sent = {str(k): str(v) for k, v in raw_sent.items()}
        legacy_entries = _read_legacy_state_entries(self.path)
        changed = False
        for key, value in legacy_entries.items():
            if key not in self.sent:
                self.sent[key] = value
                changed = True
        self._prune()
        if changed:
            self._write()

    def _prune(self) -> None:
        """
        Xóa các key đã hết TTL để giữ state.json ở kích thước hợp lý.

        Cutoff = now_UTC - TTL_DAYS ngày.
        Key với timestamp không parse được sẽ được giữ lại (conservative).
        """
        cutoff = pd.Timestamp.now("UTC") - pd.Timedelta(days=TTL_DAYS)
        self.sent = {
            k: v for k, v in self.sent.items()
            if _safe_ts(v) > cutoff
        }

    def has(self, key: str) -> bool:
        """
        Kiểm tra xem key đã được ghi nhận là "đã gửi" chưa.

        Args:
            key: Signal key từ signal_key().

        Returns:
            True nếu key đã tồn tại trong state (tín hiệu đã gửi).
        """
        with self._lock:
            return key in self.sent

    def add(self, key: str) -> None:
        """
        Ghi nhận key và lưu state ra file JSON ngay lập tức.

        Ghi atomic: viết vào .tmp trước, rename sang file thật.
        Đảm bảo file không bị corrupt nếu tiến trình bị interrupt.

        Args:
            key: Signal key cần ghi nhận.

        Side Effects:
            Sửa self.sent. Ghi file state.json (qua .tmp → rename).
            Tạo thư mục cha nếu chưa tồn tại.
        """
        with self._lock:
            self.sent[key] = pd.Timestamp.now("UTC").isoformat()
            self._write()

    def get_scan_fingerprint(self) -> str | None:
        """Return the warm-up scan fingerprint stored with this state, if any."""
        with self._lock:
            value = self.meta.get(SCAN_FINGERPRINT_KEY)
        return value if value else None

    def set_scan_fingerprint(self, fingerprint: str) -> None:
        """Persist the scan fingerprint after a fully successful warm-up."""
        with self._lock:
            self.meta[SCAN_FINGERPRINT_KEY] = str(fingerprint)
            self._write()

    def _write(self) -> None:
        """Persist current state atomically."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = {"sent": self.sent, "meta": self.meta}
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        # rename() là atomic trên Linux/Mac; trên Windows có thể raise nếu target locked
        tmp.replace(self.path)


def _safe_ts(value: str) -> pd.Timestamp:
    """
    Parse timestamp an toàn — trả về now() nếu lỗi (giữ key, không prune nhầm).

    Luôn trả về UTC-aware timestamp để so sánh được với cutoff UTC-aware trong _prune().
    Timestamp naive (không có timezone) được localize về UTC.

    Args:
        value: Chuỗi ISO timestamp.

    Returns:
        pd.Timestamp UTC-aware. Nếu parse thất bại, trả về now("UTC") để key không bị xóa.
    """
    try:
        ts = pd.Timestamp(value)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        return ts
    except Exception:
        return pd.Timestamp.now("UTC")


def _migrate_legacy_states(target: Path) -> None:
    """
    Merge các state file cũ vào path mới nếu target chưa tồn tại.

    Legacy paths được kiểm tra (theo thứ tự):
    1. core_python/notify/state.json — vị trí cũ của combo watcher
       (DEFAULT_STATE_PATH đã được đổi từ parent/ sang parents[1]/runtime/).
    2. core_python/runtime/ai_trend_state.json — state riêng của AI Trend watcher
       trước khi hợp nhất vào unified watcher.

    Nếu tìm thấy ít nhất một file cũ, merge toàn bộ key vào target.
    Nếu không tìm thấy gì, không làm gì (caller sẽ bắt đầu với state rỗng).

    Args:
        target: Path của state file mới cần tạo.
    """
    if target.resolve() != DEFAULT_STATE_PATH.resolve():
        return
    here = Path(__file__).resolve().parent
    runtime_dir = here.parent / "runtime"
    legacy_paths = [
        here / "state.json",                          # combo watcher trước khi đổi path
        runtime_dir / "ai_trend_state.json",          # ai_trend watcher riêng
    ]
    merged: dict[str, str] = {}
    found_any = False
    for lp in legacy_paths:
        if not lp.exists() or lp.resolve() == target.resolve():
            continue
        try:
            raw: dict[str, Any] = json.loads(lp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        raw_sent = raw.get("sent", {})
        now_str = pd.Timestamp.now("UTC").isoformat()
        if isinstance(raw_sent, list):
            merged.update({str(k): now_str for k in raw_sent})
        else:
            merged.update({str(k): str(v) for k, v in raw_sent.items()})
        found_any = True
        print(f"[state] Migrating legacy state: {lp} -> {target}", flush=True)

    if not found_any:
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps({"sent": merged}, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(target)


def _read_legacy_state_entries(target: Path) -> dict[str, str]:
    """Read legacy state files that should be merged into the current target."""
    if target.resolve() != DEFAULT_STATE_PATH.resolve():
        return {}
    here = Path(__file__).resolve().parent
    runtime_dir = here.parent / "runtime"
    legacy_paths = [
        here / "state.json",
        runtime_dir / "ai_trend_state.json",
    ]
    merged: dict[str, str] = {}
    for lp in legacy_paths:
        if not lp.exists() or lp.resolve() == target.resolve():
            continue
        try:
            raw: dict[str, Any] = json.loads(lp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        raw_sent = raw.get("sent", {})
        now_str = pd.Timestamp.now("UTC").isoformat()
        if isinstance(raw_sent, list):
            merged.update({str(k): now_str for k in raw_sent})
        else:
            merged.update({str(k): str(v) for k, v in raw_sent.items()})
        print(f"[state] Migrating legacy state: {lp} -> {target}", flush=True)
    return merged


def signal_key(strategy: str, symbol: str, tf: str, bartime: object, signal: int) -> str:
    """
    Xây dựng key định danh duy nhất cho một tín hiệu để chống gửi trùng.

    Format: "{strategy}|{symbol}|{tf}|{bartime}|{signal}"

    Args:
        strategy: Tên chiến lược ("combo", "ma_cross", "ai_trend", ...).
        symbol: Mã symbol (tự động uppercase).
        tf: Mã khung thời gian (tự động uppercase).
        bartime: Thời gian bar. Timestamp timezone-aware được chuẩn hóa về UTC-naive.
        signal: +1 (BUY) hoặc -1 (SELL).

    Returns:
        Chuỗi key ổn định — cùng input luôn cho cùng output.

    Giả định giao dịch:
        Hai tín hiệu BUY và SELL trên cùng bar được coi là khác nhau (khác signal value).
        bartime được format cố định "YYYY-MM-DD HH:MM:SS" để DB driver trả naive
        hay UTC-aware đều tạo cùng key cho cùng thời điểm UTC.
    """
    return f"{strategy}|{symbol.upper()}|{tf.upper()}|{_key_time(bartime)}|{int(signal)}"


def _key_time(value: object) -> str:
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        return str(value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts.strftime("%Y-%m-%d %H:%M:%S")
