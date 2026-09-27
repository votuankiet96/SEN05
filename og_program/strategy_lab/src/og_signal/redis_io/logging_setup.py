"""Cấu hình log có cấu trúc cho luồng 24/7 (redis_io/: client.py, listener.py,
publisher.py, worker.py) -- chốt 2026-09-22, KHÔNG áp dụng cho export_cli.py
hay signal_display/ (dùng chung với luồng CSV/dashboard, log ở đó sẽ lẫn cả
vào lúc export/mở dashboard tay, không đúng ý "chỉ luồng 24/7").

Mỗi dòng log: <timestamp UTC> <level> component=<module>.<hàm> event=<tên
sự kiện> pid=<tiến trình> result=<ok/fail/skip> <field riêng của event đó>.
`component` lấy thẳng từ `record.module`/`record.funcName` (logging tự biết,
không cần tự gắn tay ở từng nơi gọi) nên luôn khớp đúng code thật, không sợ
quên cập nhật khi sửa hàm.

2 đường ghi song song (đăng ký trên logger tổ tiên chung "strategy_lab",
mọi module con trong redis_io/ tự động kế thừa qua propagate mặc định):
- Console (-> journald): chỉ INFO trở lên -- dòng DEBUG (vd trigger bị bỏ
  qua ngoài phạm vi) không làm loãng `journalctl -f`.
- File `strategy_lab/logs/st_run.log`: DEBUG trở lên (đầy đủ, kể cả
  dòng bị lọc khỏi console) -- xoay vòng qua RotatingFileHandler, tổng
  dung lượng chặn cứng ~20MB (_MAX_BYTES_PER_FILE x (_BACKUP_COUNT+1)).
  Cộng thêm dọn theo tuổi file (>30 ngày) lúc start + mỗi lần heartbeat,
  đảm bảo cả 2 điều kiện "30 ngày HOẶC 20MB", cái nào tới trước chặn trước.
"""

from __future__ import annotations

import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

DEFAULT_LOG_DIR = Path(__file__).resolve().parents[3] / "logs"
LOG_FILENAME = "st_run.log"

# 700_000 bytes x 29 file dự phòng + 1 file đang ghi = 30 file, ~20.4MB --
# xấp xỉ đúng "tối đa 20MB" đã chốt, không cần chính xác tuyệt đối.
_MAX_BYTES_PER_FILE = 700_000
_BACKUP_COUNT = 29
_MAX_AGE_DAYS = 30

_ROOT_LOGGER_NAME = "strategy_lab"


class LineFormatter(logging.Formatter):
    """Format 1 dòng: timestamp UTC, level canh đều, component=module.funcName,
    event=..., pid=..., result=..., rồi tới các field riêng (key=value, giá
    trị có khoảng trắng thì tự bọc ngoặc kép).
    """

    def format(self, record: logging.LogRecord) -> str:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(record.created))
        ts = f"{ts}.{int(record.msecs):03d} UTC"
        level = record.levelname.ljust(8)
        component = f"{record.module}.{record.funcName}".ljust(30)
        raw_event = getattr(record, "event_name", None)
        if raw_event is None:
            # Bản ghi KHÔNG đi qua log_event() -- vd logger của db_connector.py
            # ("strategy_lab.src.db_connector" là hậu duệ của logger gốc nên vẫn
            # rơi vào formatter này). Trước 2026-09-22 nó ra
            # `event=<cả câu tiếng Anh có dấu cách> result=ok`: vừa phá cấu trúc
            # key=value, vừa báo "ok" cho cả lỗi mất kết nối SQL Server. Giờ
            # message được quote, result suy từ level thật.
            event_name = _quote(record.getMessage())
            result = "fail" if record.levelno >= logging.WARNING else "ok"
        else:
            event_name = str(raw_event)
            result = str(getattr(record, "result", "ok"))
        event_name = event_name.ljust(22)
        result = result.ljust(4)
        fields: dict[str, object] = getattr(record, "fields", {})
        field_str = " ".join(f"{key}={_quote(value)}" for key, value in fields.items())

        line = (
            f"{ts} {level} component={component} event={event_name} "
            f"pid={record.process} result={result}"
        )
        if field_str:
            line += " " + field_str
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def _quote(value: object) -> str:
    text = str(value)
    if " " in text or "=" in text:
        return '"' + text.replace('"', "'") + '"'
    return text


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    *,
    result: str = "ok",
    exc_info: bool = False,
    **fields: object,
) -> None:
    """Ghi 1 dòng log có cấu trúc -- điểm gọi duy nhất mà client.py/
    listener.py/publisher.py/worker.py dùng, để mọi dòng log trong luồng
    24/7 luôn cùng 1 hình dạng (event/result/field), không lệch nhau tuỳ
    người viết.
    """
    logger.log(
        level,
        event,
        extra={"event_name": event, "result": result, "fields": fields},
        exc_info=exc_info,
        # stacklevel=2: bỏ qua đúng 1 tầng (chính hàm log_event() này) khi
        # logging tự suy ra module/funcName -- không có dòng này, mọi
        # component sẽ hiện sai thành "logging_setup.log_event" (nơi
        # logger.log() thật sự được gọi) thay vì nơi gọi log_event() thật.
        stacklevel=2,
    )


def configure_logging(log_dir: Path | str = DEFAULT_LOG_DIR) -> Path:
    """Dựng 2 handler (console INFO+, file DEBUG+) trên logger tổ tiên
    "strategy_lab" -- mọi logger con (client/listener/publisher/worker, đều
    tạo bằng logging.getLogger(__name__) nên tự động là con cháu của logger
    này) kế thừa qua propagate mặc định, không cần cấu hình riêng lẻ từng
    file. Idempotent -- gọi lại (vd test) không nhân đôi handler.

    Trả về đường dẫn thư mục log thật đã dùng, để main() log lại cho biết.
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    formatter = LineFormatter()

    root_logger = logging.getLogger(_ROOT_LOGGER_NAME)
    root_logger.setLevel(logging.DEBUG)
    root_logger.handlers.clear()

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)
    root_logger.addHandler(console)

    file_handler = RotatingFileHandler(
        log_dir / LOG_FILENAME,
        maxBytes=_MAX_BYTES_PER_FILE,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    _cleanup_old_logs(log_dir)
    return log_dir


def _cleanup_old_logs(log_dir: Path, *, max_age_days: int = _MAX_AGE_DAYS) -> None:
    """Xoá file log (kể cả file .1/.2/... do RotatingFileHandler xoay ra)
    cũ hơn max_age_days, dựa vào mtime -- điều kiện "30 ngày" độc lập với
    chặn dung lượng 20MB của RotatingFileHandler, cái nào tới trước chặn
    trước.
    """
    cutoff = time.time() - max_age_days * 86400
    for path in log_dir.glob(f"{LOG_FILENAME}*"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue
