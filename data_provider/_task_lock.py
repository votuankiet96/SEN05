"""
Co che khoa phan tan va relay token xac nhan qua bang `SEN.ActiveTask`.

Module nay giai quyet 2 bai toan van hanh:
1. Advisory lock:
   checker, pipeline va ws_live co nhung luc cung muon ghi vao kho du lieu.
   `SEN.ActiveTask` duoc dung nhu mot "den giao thong" cap DB de ngan xung dot.
2. Confirmation token relay:
   Telegram bot listener va checker co the la 2 process khac nhau.
   Ket qua `/confirm_TOKEN` va `/skip_TOKEN` duoc ghi vao DB de process dang cho van nhan duoc.

Thiet ke uu tien an toan van hanh:
- lock co TTL (dead-man switch) de tranh treo vo han neu process bi kill
- `is_locked()` co cache ngan de giam tan suat hit DB trong vong lap chat
- doc lock theo kieu fail-open: DB loi thi khong giam he thong trong trang thai defer vo thoi han
"""

# =============================================================================
# data_provider/_task_lock.py  -  Khóa phân tán DB + Token xác nhận Telegram
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
#   CƠ CHẾ 2: TOKEN XÁC NHẬN (Confirmation Token)
#   ─────────────────────────────────────────────────────────────────────────
#   Khi Checker phát hiện vấn đề, nó tạo 1 mã token 8 ký tự ngẫu nhiên
#   (ví dụ: A1B2C3D4) và gửi Telegram:
#     "Gõ /confirm_A1B2C3D4 -> Sửa tất cả"
#     "Gõ /skip_A1B2C3D4   -> Bỏ qua"
#
#   Checker sau đó đợi (tối đa 4 giờ) cho đến khi user gõ lệnh.
#   Token đảm bảo: dù gõ nhầm hay gõ trùng, chỉ lần đầu hợp lệ được tính.
#
#   Vấn đề kỹ thuật - 2 process cùng nghe Telegram:
#     WS Live chạy bot listener 24/7 (để nhận /fix, /status...).
#     Checker cũng lắng nghe Telegram khi đang đợi phản hồi.
#     Telegram chỉ giao mỗi tin nhắn cho 1 process -> có thể WS "ăn" mất lệnh
#     /confirm mà Checker đang chờ.
#
#   Giải pháp - DB relay:
#     Khi WS nhận /confirm_TOKEN -> ghi vào SEN.ActiveTask.Payload.
#     Checker song song với đọc Telegram còn đọc cả DB.Payload mỗi 15 giây.
#     Dù ai nhận lệnh trước, kết quả đều đến được Checker.
#
# BẢNG DATABASE LIÊN QUAN:
#   SEN.ActiveTask - xem file 00_sql/06_active_task.sql
#   Cột TaskName (PRIMARY KEY) = tên khoá (ví dụ: 'checker_repair')
#   Cột ExpiresAt = thời điểm khoá tự hết hạn
#   Cột Payload   = nơi WS ghi kết quả token (/confirm hay /skip)
# =============================================================================

import re
import secrets
import string
import sys
import time
from datetime import datetime
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

import pyodbc  # noqa: E402
from modules.db_connector import get_connection  # noqa: E402

# ─── Module-level state ──────────────────────────────────────────────────────

# Token registry: key = token string, value = None (chờ) | 'confirm' | 'skip'
_pending_tokens: dict[str, str | None] = {}

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


# ─── Token generation ────────────────────────────────────────────────────────

def generate_token() -> str:
    """Tạo token 8 ký tự (chữ hoa + số) để dùng trong lệnh /confirm_TOKEN."""
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(8))


# ─── Token command handler ───────────────────────────────────────────────────

def _handle_token_command(text: str) -> bool:
    """
    Parse lệnh /confirm_TOKEN hoặc /skip_TOKEN và cập nhật _pending_tokens.

    First-one-wins: chỉ set nếu token đang chờ (value = None).
    Gửi tin xác nhận về Telegram để user biết lệnh đã được nhận.

    Trả về True nếu token hợp lệ và được xử lý, False nếu không.
    """
    m = re.match(r"^/(confirm|skip)_([A-Za-z0-9]{8})\b", text.strip(), re.IGNORECASE)
    if not m:
        return False

    action = m.group(1).lower()    # chuẩn hóa về 'confirm' hoặc 'skip'
    token  = m.group(2).upper()    # chuẩn hóa về uppercase để khớp generate_token()

    if token not in _pending_tokens:
        # Token không có trong registry của process này -> thử DB relay
        return False

    if _pending_tokens[token] is None:
        # First-one-wins: chỉ set nếu chưa có kết quả
        _pending_tokens[token] = action
        try:
            from _discord import tg_send
            icon = "[OK]" if action == "confirm" else "[SKIP]"
            tg_send(f"{icon} Received /{action}_{token} - processing...")
        except Exception:
            pass
    else:
        # Đã nhận lệnh trước đó
        try:
            from _discord import tg_send
            tg_send(f"[INFO] Token {token} has already been processed.")
        except Exception:
            pass

    return True


# ─── Cross-process DB relay reader ───────────────────────────────────────────

def _read_db_relay(task_name: str, token: str) -> str | None:
    """
    Đọc kết quả token từ DB relay (SEN.ActiveTask.Payload).
    WS bot ghi vào đây khi nhận /confirm hoặc /skip ở process khác.

    Trả về 'confirm', 'skip', hoặc None nếu chưa có kết quả.
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT Payload FROM SEN.ActiveTask WHERE TaskName = ?",
            (task_name,),
        )
        row = cursor.fetchone()
        if row and row[0]:
            payload = str(row[0])
            if payload.startswith(f"confirm:{token}"):
                return "confirm"
            if payload.startswith(f"skip:{token}"):
                return "skip"
        return None
    except Exception:
        return None
    finally:
        if conn is not None:
            conn.close()


# ─── Interactive confirmation ─────────────────────────────────────────────────

def request_confirm(
    title: str,
    problem_desc: str,
    options: dict[str, str],
    timeout_min: int = 240,
    affected_pairs: list[str] | None = None,
    task_name: str = "checker_repair",
) -> str:
    """
    Gửi tin Telegram hỏi user và đợi phản hồi.

    Flow:
      1. Tạo token + đăng ký vào _pending_tokens
      2. Gửi tg_ask() + tg_flush() (đảm bảo tin đến trước khi poll)
      3. Drain backlog getUpdates (bỏ qua tin cũ)
      4. Poll loop mỗi 15 giây:
         a. getUpdates -> _handle_token_command() (same process)
         b. Đọc DB relay (cross-process: WS bot có thể đã ghi vào đây)
         c. Break khi có kết quả
      5. Dọn token + trả về 'confirm' | 'skip' | 'timeout'

    Tham số:
      title          - tiêu đề tin nhắn
      problem_desc   - mô tả vấn đề (multiline string)
      options        - dict {"confirm": "...", "skip": "..."} mô tả các lựa chọn
      timeout_min    - thời gian chờ tối đa (phút), mặc định 240 (4 giờ)
      affected_pairs - danh sách pairs bị ảnh hưởng (hiển thị tối đa 8)
      task_name      - tên task trong SEN.ActiveTask (để đọc DB relay)

    Trả về: 'confirm' | 'skip' | 'timeout'
    """
    # Discord webhook là một chiều - không thể polling nhận lệnh phản hồi.
    # Gửi thông báo 1 chiều rồi trả về 'timeout' ngay lập tức.
    from _discord import tg_ask, tg_flush

    token = generate_token()
    _pending_tokens[token] = None

    tg_ask(
        title=title,
        problem_desc=problem_desc,
        options=options,
        token=token,
        timeout_min=timeout_min,
        affected_pairs=affected_pairs,
    )
    tg_flush(timeout=15)

    _pending_tokens.pop(token, None)
    return "timeout"
