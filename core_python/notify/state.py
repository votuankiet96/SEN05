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
    → TTL_DAYS = 60 (đảm bảo đủ buffer cho tất cả TF).

Tính an toàn ghi file:
    Ghi vào file .tmp trước, sau đó rename nguyên tử sang file thật.
    Đảm bảo state.json không bị corrupt nếu tiến trình bị giết giữa chừng.

Phụ thuộc ngoài:
    Không có — chỉ dùng stdlib json, pathlib, và pandas cho timestamp.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


DEFAULT_STATE_PATH = Path(__file__).resolve().parent / "state.json"

# TTL phải lớn hơn cửa sổ bars dài nhất (H3/H4 ≈ 50 ngày).
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
        self._load()

    def _load(self) -> None:
        """
        Nạp state từ file JSON.

        Xử lý 2 định dạng:
        - Format cũ: {"sent": ["key1", "key2"]} (list) → migrate sang dict với timestamp hiện tại.
        - Format mới: {"sent": {"key1": "2024-01-01T...", ...}} (dict với timestamp).

        Nếu file không tồn tại hoặc JSON lỗi, bắt đầu với state rỗng.

        Side Effects:
            Gọi _prune() để xóa key hết TTL sau khi nạp.
        """
        if not self.path.exists():
            self.sent = {}
            return
        try:
            data: dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.sent = {}
            return
        raw_sent = data.get("sent", [])
        if isinstance(raw_sent, list):
            # Migrate format cũ (list) → dict với timestamp hiện tại
            now_str = pd.Timestamp.now("UTC").isoformat()
            self.sent = {str(k): now_str for k in raw_sent}
        else:
            self.sent = {str(k): str(v) for k, v in raw_sent.items()}
        self._prune()

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
        self.sent[key] = pd.Timestamp.now("UTC").isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = {"sent": self.sent}
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        # rename() là atomic trên Linux/Mac; trên Windows có thể raise nếu target locked
        tmp.replace(self.path)


def _safe_ts(value: str) -> pd.Timestamp:
    """
    Parse timestamp an toàn — trả về now() nếu lỗi (giữ key, không prune nhầm).

    Args:
        value: Chuỗi ISO timestamp.

    Returns:
        pd.Timestamp. Nếu parse thất bại, trả về now("UTC") để key không bị xóa.
    """
    try:
        return pd.Timestamp(value)
    except Exception:
        return pd.Timestamp.now("UTC")


def signal_key(strategy: str, symbol: str, tf: str, bartime: object, signal: int) -> str:
    """
    Xây dựng key định danh duy nhất cho một tín hiệu để chống gửi trùng.

    Format: "{strategy}|{symbol}|{tf}|{bartime}|{signal}"

    Args:
        strategy: Tên chiến lược ("combo", "ma_cross").
        symbol: Mã symbol (tự động uppercase).
        tf: Mã khung thời gian (tự động uppercase).
        bartime: Thời gian bar — dùng str() trực tiếp (datetime hoặc string).
        signal: +1 (BUY) hoặc -1 (SELL).

    Returns:
        Chuỗi key ổn định — cùng input luôn cho cùng output.

    Giả định giao dịch:
        Hai tín hiệu BUY và SELL trên cùng bar được coi là khác nhau (khác signal value).
        bartime phải nhất quán — không localize giữa các lần gọi hoặc key sẽ không match.
    """
    return f"{strategy}|{symbol.upper()}|{tf.upper()}|{bartime}|{int(signal)}"
