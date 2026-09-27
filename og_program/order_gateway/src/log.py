"""Cấu hình logging tập trung, duy nhất cho order_gateway.

Chốt 2026-09-22: log chỉ ghi ra ĐÚNG 1 nơi -- file, không còn StreamHandler
ra stdout/stderr -- nên journald sẽ gần như trống (chỉ còn dòng systemd tự
in lúc start/stop unit), file dưới đây mới là nguồn sự thật duy nhất.

RotatingFileHandler maxBytes=5MB x backupCount=3 => tổng cứng tối đa
~20MB, không bao giờ vượt dù log dồn dập bất thường -- giới hạn cứng theo
dung lượng, không phải cơ chế xoá theo lịch riêng (20MB ở mức log hiện tại
-- đã hạ "no signal" xuống DEBUG -- đủ phủ nhiều tháng vận hành bình
thường, nên tự thoả luôn yêu cầu "không quá 30 ngày" mà không cần thêm cơ
chế thứ hai).
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parents[1] / "logs"
LOG_FILE = LOG_DIR / "og_run.log"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3


def configure_logging(level: str) -> None:
    """Thiết lập root logger: đúng 1 RotatingFileHandler, gọi 1 lần duy
    nhất lúc khởi động (live_worker.main()) trước khi log dòng nào khác.

    Gọi với level bootstrap TRƯỚC khi đọc og_config.yaml, rồi dùng
    set_log_level() để áp level thật — nhờ vậy lỗi config lúc khởi động
    (thiếu key) vẫn kịp vào file log thay vì chỉ ra stderr.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        LOG_FILE, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s[%(process)d]: %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(getattr(logging, str(level).upper()))
    for existing in root.handlers:
        existing.close()
    root.handlers.clear()
    root.addHandler(handler)


def set_log_level(level: str) -> None:
    """Áp level thật từ og_config.yaml lên handler đã dựng sẵn — không dựng
    lại handler, nên không mở thêm file descriptor nào.
    """
    logging.getLogger().setLevel(getattr(logging, str(level).upper()))
