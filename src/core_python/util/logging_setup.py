"""
Cấu hình logging dùng chung cho core_python.

Mô tả:
    Trước đây mỗi chương trình tự gọi logging.basicConfig() riêng, chỉ ghi
    ra console — chạy nền qua systemd/service thì log không lưu lại được ở
    đâu ngoài journal. Module này thêm 1 rotating file handler ghi vào
    runtime/logs/ ở gốc repo, giữ console output như cũ.

Đầu vào:
    log_filename: tên file trong runtime/logs/ (vd "core_python.log") —
    mỗi chương trình 1 file riêng, không trộn lẫn.

Lưu ý:
    Dashboard chạy qua gunicorn nên gọi setup_logger() cấu hình root logger
    1 lần/tiến trình là an toàn. Gunicorn nhiều worker (nhiều tiến trình
    con) cùng ghi vào 1 file là chấp nhận được ở quy mô hiện tại — rotation
    giữa nhiều tiến trình có thể hiếm khi lệch nhịp, không phải vấn đề tại
    lưu lượng log thấp của dashboard nội bộ này.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNTIME_LOG_DIR = REPO_ROOT / "runtime" / "logs"

_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 3


def setup_logger(log_filename: str) -> None:
    """
    Cấu hình root logger: console + rotating file trong runtime/logs/.

    Args:
        log_filename: Tên file log riêng cho chương trình gọi hàm này.
    """
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"

    RUNTIME_LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        RUNTIME_LOG_DIR / log_filename,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(fmt))

    logging.basicConfig(
        level=level,
        format=fmt,
        handlers=[logging.StreamHandler(), file_handler],
    )
