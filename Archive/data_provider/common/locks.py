"""
Co che khoa phan tan qua bang `SEN.ActiveTask`.

Module nay giai quyet 2 bai toan van hanh:
1. Advisory lock:
   checker, pipeline va ws_live co nhung luc cung muon ghi vao kho du lieu.
   `SEN.ActiveTask` duoc dung nhu mot "den giao thong" cap DB de ngan xung dot.
2. Payload signal:
   Payload can carry small runtime signals between processes, for example
   ws_live shutdown requests and heartbeat metadata. Interactive confirmation
   tokens are intentionally not supported in the current one-way notifier path.

Thiet ke uu tien an toan van hanh:
- lock co TTL (dead-man switch) de tranh treo vo han neu process bi kill
- `is_locked()` co cache ngan de giam tan suat hit DB trong vong lap chat
- doc lock theo kieu fail-open: DB loi thi khong giam he thong trong trang thai defer vo thoi han
"""

# =============================================================================
# data_provider/common/locks.py  -  Khóa phân tán DB + runtime payload signal
# =============================================================================
#
# FILE NÀY LÀM GÌ-
#   Cung cấp 2 cơ chế an toàn cho hệ thống:
#
#   ─────────────────────────────────────────────────────────────────────────
#   CƠ CHẾ 1: KHÓA (Advisory Lock)
#   ─────────────────────────────────────────────────────────────────────────
#   Khi Checker đang sửa dữ liệu, nó "khóa" lại bằng cách INSERT 1 hàng vào
#   bảng SEN.ActiveTask. WS Live đọc trạng thái khóa này trước khi ghi DB.
#
#   Tại sao cần khóa-
#     WS Live và Checker đều ghi vào cùng bảng Fact_OHLCV. Nếu chạy đồng thời:
#       - Checker xóa nến sai -> đúng lúc WS đẩy nến mới -> Checker ghi đè nến
#         mới của WS = dữ liệu bị mất.
#     Giải pháp: Checker khóa -> WS phát hiện -> WS tạm hoãn ghi Fact (giữ trong
#     staging) -> sau khi Checker xong, WS ghi lại -> không mất dữ liệu.
#
#   Dead-man switch (khoá tự hết hạn):
#     Nếu Checker bị tắt đột ngột giữa chừng, khoá có ExpiresAt.
#     Sau 90 phút, khoá tự hết hạn -> WS tự động tiếp tục ghi bình thường.
#     Không bao giờ bị treo vô hạn.
#
#   ─────────────────────────────────────────────────────────────────────────
#   CƠ CHẾ 2: PAYLOAD / RUNTIME SIGNAL
#   ─────────────────────────────────────────────────────────────────────────
#   Cột Payload tồn tại để mang tín hiệu vận hành nhỏ giữa các process, ví dụ
#   heartbeat metadata hoặc shutdown_requested=1 cho ws_live. Luồng confirm
#   tương tác cũ đã bị loại bỏ vì notifier hiện tại là webhook một chiều.
#
# BẢNG DATABASE LIÊN QUAN:
#   SEN.ActiveTask - xem file data_provider/sql/06_active_task.sql
#   Cột TaskName (PRIMARY KEY) = tên khoá (ví dụ: 'checker_repair')
#   Cột ExpiresAt = thời điểm khoá tự hết hạn
#   Cột Payload   = nơi ghi tín hiệu vận hành nhỏ giữa các process
# =============================================================================

import sys
import time
from pathlib import Path

_PROJ = Path(__file__).resolve().parents[2]
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

import pyodbc  # noqa: E402
from modules.db_connector import get_connection  # noqa: E402

# ─── Module-level state ──────────────────────────────────────────────────────

# Cache is_locked() để tránh query DB mỗi lần trong _db_worker() tight loop
_lock_cache: dict = {"task_name": None, "locked": False, "checked_at": 0.0}
_LOCK_CACHE_TTL = 30.0   # giây - refresh mỗi 30s

# ─── Advisory lock ───────────────────────────────────────────────────────────

def acquire(task_name: str, duration_min: int = 90, payload: str | None = None) -> bool:
    """
    Cố gắng lấy lock cho task_name bằng cách INSERT vào SEN.ActiveTask.

    Trả về:
      True  - thành công, lock đã được lấy
      False - thất bại vì task đã bị locked bởi process khác (PK violation)

    Gọi release() trong finally block để đảm bảo lock luôn được giải phóng.
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        if payload is None:
            cursor.execute(
                """
                INSERT INTO SEN.ActiveTask (TaskName, ExpiresAt)
                VALUES (?, DATEADD(minute, ?, SYSUTCDATETIME()))
                """,
                (task_name, duration_min),
            )
        else:
            cursor.execute(
                """
                INSERT INTO SEN.ActiveTask (TaskName, ExpiresAt, Payload)
                VALUES (?, DATEADD(minute, ?, SYSUTCDATETIME()), ?)
                """,
                (task_name, duration_min, payload),
            )
        conn.commit()
        return True
    except pyodbc.IntegrityError:
        # PK violation = đã có row với TaskName này -> lock đang bị giữ
        return False
    except Exception:
        return False
    finally:
        if conn is not None:
            conn.close()


def release(task_name: str) -> None:
    """
    Giải phóng lock bằng cách DELETE row khỏi SEN.ActiveTask.
    Silent no-op nếu row không tồn tại (an toàn khi gọi nhiều lần).
    Luôn gọi trong finally block.
    """
    conn = None
    try:
        conn = get_connection()
        conn.cursor().execute(
            "DELETE FROM SEN.ActiveTask WHERE TaskName = ?",
            (task_name,),
        )
        conn.commit()
    except Exception:
        pass  # không để release() gây lỗi cascade
    finally:
        if conn is not None:
            conn.close()
    # Invalidate cache ngay lập tức sau khi release
    if _lock_cache["task_name"] == task_name:
        _lock_cache["checked_at"] = 0.0


def renew(task_name: str, duration_min: int = 90) -> bool:
    """
    Gia hạn lock đang giữ bằng cách đẩy ExpiresAt ra xa hơn.
    Dùng cho các task dài như checker repair để tránh TTL hết giữa chừng.
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE SEN.ActiveTask
            SET ExpiresAt = DATEADD(minute, ?, SYSUTCDATETIME())
            WHERE TaskName = ? AND ExpiresAt > SYSUTCDATETIME()
            """,
            (duration_min, task_name),
        )
        conn.commit()
        updated = cursor.rowcount > 0
    except Exception:
        updated = False
    finally:
        if conn is not None:
            conn.close()
    if _lock_cache["task_name"] == task_name:
        _lock_cache["checked_at"] = 0.0
    return updated


def update_payload(task_name: str, payload: str) -> bool:
    """
    Ghi tín hiệu vào cột Payload của lock đang tồn tại.
    Không thay đổi ExpiresAt hay bất kỳ cột nào khác.
    Trả về True nếu row tìm thấy và cập nhật, False nếu không.
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE SEN.ActiveTask
            SET Payload = ?
            WHERE TaskName = ? AND ExpiresAt > SYSUTCDATETIME()
            """,
            (payload, task_name),
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception:
        return False
    finally:
        if conn is not None:
            conn.close()


def is_locked(task_name: str) -> bool:
    """
    Kiểm tra xem task_name có đang bị lock không (row tồn tại VÀ chưa expire).

    Có cache 30 giây để tránh DB hammering - được gọi trong WS _db_worker()
    mỗi queue item. Fail-open: nếu DB lỗi -> trả về False (không defer vô hạn).
    """
    now = time.monotonic()
    if (
        _lock_cache["task_name"] == task_name
        and now - _lock_cache["checked_at"] < _LOCK_CACHE_TTL
    ):
        return _lock_cache["locked"]

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT 1 FROM SEN.ActiveTask
            WHERE TaskName = ? AND ExpiresAt > SYSUTCDATETIME()
            """,
            (task_name,),
        )
        result = cursor.fetchone() is not None
    except Exception:
        result = False  # fail-open: DB down -> assume unlocked
    finally:
        if conn is not None:
            conn.close()

    _lock_cache.update({"task_name": task_name, "locked": result, "checked_at": now})
    return result


def cleanup_expired() -> int:
    """
    Xóa các lock row đã hết hạn.
    Gọi khi khởi động mỗi script để dọn dẹp lock cũ từ process bị kill trước đó.
    Trả về số row đã xóa.
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM SEN.ActiveTask WHERE ExpiresAt <= SYSUTCDATETIME()"
        )
        deleted = cursor.rowcount
        conn.commit()
        return deleted
    except Exception:
        return 0
    finally:
        if conn is not None:
            conn.close()


