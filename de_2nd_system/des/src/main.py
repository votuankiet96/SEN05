"""Lối vào mỏng của một tiến trình DE: ``python -m des.src.main --runner --lane <module>.<nguồn> [--symbols A,B]`` (chạy MỘT chiến lược hoặc MỘT yếu tố trend đã đăng ký ở config trên nến của MỘT nguồn, ghi kết quả vào db11), ``--publisher [--lane <nguồn>]
[--symbols A,B]`` (đọc kết quả module và nến của nguồn đó, ra tín hiệu cuối vào db11 và báo kênh) hoặc ``--load-config`` (nạp des_config.yaml lên Redis rồi thoát).

Chỉ đọc tham số dòng lệnh, bắt SIGTERM rồi giao cho ``configuration.run_runner`` / ``run_publisher`` / ``run_load_config``: mọi điều phối nằm ở configuration.py. Mỗi tiến trình khi khởi động nạp des_config.yaml
lên Redis (chỉ tạo phiên bản config mới khi nội dung khác) rồi đọc config từ Redis, nên sửa des_config.yaml rồi chạy ``--load-config`` là mọi tiến trình đang chạy dùng ngay, không cần restart.
``--lane`` chia VAI TRÒ cho nhiều tiến trình cùng việc: ở runner là ``<tên>.<nguồn>`` (tên là khoá ở ``strategies:`` hoặc ``trends:`` của config, nguồn là tên khai ở redis.candles.sources, ``default`` là nguồn mặc định, vd ``ma_cross.og8``,
``macd.default``); ở publisher là ``<nguồn>`` (bỏ đi = nguồn ``default``). ``--symbols`` chia việc theo symbol cho nhiều tiến trình cùng vai trò (mỗi tiến trình chỉ làm các symbol nêu tên); bỏ đi thì làm mọi symbol đã đăng ký.
"""

from __future__ import annotations

import argparse
import logging
import re
import signal
import sys
from collections.abc import Sequence

LOGGER = logging.getLogger("des.src.main")
DEFAULT_SOURCE = "default"


def _handle_shutdown_signal(signum: int, frame: object) -> None:
    """SIGTERM (systemctl stop gửi SIGTERM, không phải Ctrl+C): raise SystemExit để chạy các khối `finally` đóng kết nối, rồi configuration.run_* ghi dòng event=stopped."""
    LOGGER.info("event=shutdown_signal signal=%s", signal.Signals(signum).name)
    raise SystemExit(0)


def _symbol_list(value: str) -> tuple[str, ...]:
    """Kiểu của tham số --symbols: danh sách symbol cách nhau bằng dấu phẩy (chữ và số), chuẩn hoá chữ hoa, không rỗng, không trùng."""
    names = tuple(item.strip().upper() for item in value.split(",") if item.strip())
    if not names or any(not re.fullmatch(r"[A-Z0-9]+", name) for name in names) or len(set(names)) != len(names):
        raise argparse.ArgumentTypeError(f"invalid symbol list '{value}' (comma-separated, letters and digits, no repeats)")
    return names


def _lane_text(value: str) -> str:
    """Kiểu của tham số --lane: chữ thường, bỏ khoảng trắng hai đầu; hình dạng đúng (``<tên>.<nguồn>`` ở runner, ``<nguồn>`` ở publisher) do ``parse_args`` kiểm vì nó phụ thuộc vai trò."""
    return value.strip().lower()


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """Đọc tham số dòng lệnh. Sau khi đọc: ``lane`` là tên chiến lược hoặc yếu tố (runner), ``source`` là tên nguồn nến (rỗng = ``default``)."""
    parser = argparse.ArgumentParser(
        prog="des.src.main",
        description="DE: candles -> module results in Redis db11 (--runner), module results -> final signals in Redis db11 (--publisher), des_config.yaml -> Redis (--load-config).",
    )
    role = parser.add_mutually_exclusive_group(required=True)
    role.add_argument("--runner", action="store_true", help="chạy MỘT chiến lược hoặc MỘT yếu tố trend (khoá ở strategies: hoặc trends: của config) trên nến của MỘT nguồn và ghi kết quả vào db đầu ra của DES (db11)")
    role.add_argument("--publisher", action="store_true", help="đọc kết quả module và nến của một nguồn, ra tín hiệu cuối vào db đầu ra của DES (db11) và báo kênh")
    role.add_argument("--load-config", action="store_true", help="nạp des_config.yaml lên Redis (phiên bản mới nếu nội dung đổi) rồi thoát")
    parser.add_argument("--symbols", type=_symbol_list, default=(), help="giới hạn tiến trình vào các symbol này (cách nhau bằng dấu phẩy); bỏ đi = mọi symbol đã đăng ký")
    parser.add_argument(
        "--lane", type=_lane_text, default="",
        help="chia vai trò: runner = '<tên>.<nguồn>' (vd ma_cross.og8, macd.default); publisher = '<nguồn>' (bỏ đi = default)",
    )
    args = parser.parse_args(list(argv))
    args.source = ""
    if args.runner:
        match = re.fullmatch(r"([a-z0-9_]+)\.([a-z0-9]+)", args.lane)
        if match is None:
            parser.error(f"--runner needs --lane <name>.<source> (e.g. ma_cross.default, macd.og8), got '{args.lane}'")
        args.lane, args.source = match.group(1), "" if match.group(2) == DEFAULT_SOURCE else match.group(2)
    elif args.publisher:
        if args.lane and not re.fullmatch(r"[a-z0-9]+", args.lane):
            parser.error(f"invalid publisher lane '{args.lane}' (use a candle source name, e.g. default, og8)")
        args.source = "" if args.lane in ("", DEFAULT_SOURCE) else args.lane
        args.lane = ""
    elif args.lane or args.symbols:
        parser.error("--load-config takes no other option")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    from des.src import configuration

    if args.load_config:
        return configuration.run_load_config()
    if args.publisher:
        return configuration.run_publisher(source_name=args.source, symbols=args.symbols)
    return configuration.run_runner(source_name=args.source, symbols=args.symbols, lane=args.lane)


if __name__ == "__main__":
    raise SystemExit(main())
