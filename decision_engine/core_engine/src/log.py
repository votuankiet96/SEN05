"""Cấu hình logging tập trung, duy nhất cho core_engine.

Chốt 2026-09-22: log chỉ ghi ra ĐÚNG 1 nơi -- file, không còn StreamHandler
ra stdout/stderr -- nên journald sẽ gần như trống (chỉ còn dòng systemd tự
in lúc start/stop unit), file dưới đây mới là nguồn sự thật duy nhất.

RotatingFileHandler maxBytes=5MB x backupCount=3 => tổng cứng tối đa
~20MB, không bao giờ vượt dù log dồn dập bất thường -- giới hạn cứng theo
dung lượng, không phải cơ chế xoá theo lịch riêng (20MB ở mức log hiện tại
-- đã hạ "no signal" xuống DEBUG -- đủ phủ nhiều tháng vận hành bình
thường, nên tự thoả luôn yêu cầu "không quá 30 ngày" mà không cần thêm cơ
chế thứ hai).

Mỗi dòng log: ``thời gian MỨC tiến-trình[pid] module.hàm:dòng: nội dung``, ví dụ
``2026-11-04 03:30:09,675 INFO    combo_og8[53166] strategy_runner.publish_latest:262: event=signal_published ...``.
``tiến-trình`` là tên file log của tiến trình (combo, combo_og8, trend_knn...); ``module.hàm:dòng`` cho biết ngay file ``src/<module>.py`` nào phát dòng đó
(mỗi module log bằng logger mang tên chính nó, xem tests/test_log.py); lỗi bất ngờ kèm traceback đầy đủ.
"""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parents[1] / "logs"
LOG_FILE = LOG_DIR / "de_run.log"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3


def log_file(name: str | None = None) -> Path:
    """File log của một tiến trình: ``de_run.log`` khi một tiến trình chạy mọi chiến lược; ``de_run_<tên>.log`` khi mỗi chiến lược một tiến trình.
    Phải tách file vì RotatingFileHandler KHÔNG an toàn khi nhiều tiến trình cùng xoay vòng một file. ``name`` chỉ gồm chữ thường, số và gạch dưới."""
    if not name:
        return LOG_FILE
    if not re.fullmatch(r"[a-z0-9_]+", name):
        raise ValueError(f"Invalid log name '{name}': use lowercase letters, digits and underscore only.")
    return LOG_DIR / f"de_run_{name}.log"


class _LineFormatter(logging.Formatter):
    """Định dạng một dòng log: tiến trình (nhãn + pid) và nơi phát (module.hàm:dòng, bỏ tiền tố ``core_engine.src.``)."""

    PREFIX = "core_engine.src."

    def __init__(self, lane: str) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(lane)s[%(process)d] %(where)s: %(message)s")
        self._lane = lane

    def format(self, record: logging.LogRecord) -> str:
        record.lane = self._lane
        record.where = f"{record.name.removeprefix(self.PREFIX)}.{record.funcName}:{record.lineno}"
        return super().format(record)


def configure_logging(level: str, name: str | None = None) -> None:
    """Thiết lập root logger: đúng 1 RotatingFileHandler, gọi 1 lần duy
    nhất lúc khởi động (configuration.run / run_trend, gọi từ main.py) trước khi log dòng nào khác.
    ``name`` (tên chiến lược của tiến trình) chọn file log riêng, xem ``log_file``, và là nhãn tiến trình trong mỗi dòng.

    Gọi với level bootstrap TRƯỚC khi đọc de_config.yaml, rồi dùng
    set_log_level() để áp level thật — nhờ vậy lỗi config lúc khởi động
    (thiếu key) vẫn kịp vào file log thay vì chỉ ra stderr.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_file(name), maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    handler.setFormatter(_LineFormatter(name or "de"))
    root = logging.getLogger()
    root.setLevel(getattr(logging, str(level).upper()))
    for existing in root.handlers:
        existing.close()
    root.handlers.clear()
    root.addHandler(handler)


def set_log_level(level: str) -> None:
    """Áp level thật từ de_config.yaml lên handler đã dựng sẵn — không dựng
    lại handler, nên không mở thêm file descriptor nào.
    """
    logging.getLogger().setLevel(getattr(logging, str(level).upper()))
