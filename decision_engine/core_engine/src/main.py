"""Lối vào mỏng của một tiến trình DE: ``python -m core_engine.src.main --runner [--source <nguồn>] [--lane <làn>.<nguồn>] [--symbols A,B]`` (tính cấu phần từ nến, ghi db0/db1) hoặc
``--publisher [--lane <chiến lược>] [--symbols A,B]`` (đọc db0/db1, lắp bảng theo luật nến đóng, quyết định tín hiệu, ghi db2).

Chỉ đọc tham số dòng lệnh, bắt SIGTERM rồi giao cho ``configuration.run_runner`` / ``configuration.run_publisher``: mọi điều phối (đọc de_config.yaml, dựng việc, vòng lặp sự kiện) nằm ở configuration.py.
Mỗi nguồn nến một nhóm runner (``--source og8`` đọc ``redis.input_sources.og8``; bỏ ``--source`` hoặc ``--source default`` đọc ``redis.input``), nên một Redis nguồn chết chỉ làm nhóm của nó dừng.
``--lane`` chia VAI TRÒ cho nhiều tiến trình cùng việc: ở runner là ``<chiến lược>.<nguồn>`` (chỉ TRIGGER và SETUP của chiến lược đó, vd ``combo.og8``) hoặc ``trend.<nguồn>`` (chỉ TREND và CONTEXT), nguồn ``default`` = ``redis.input``
(nên không đi chung với ``--source``); ở publisher là khoá một chiến lược (chỉ các ô của chiến lược đó). Bỏ ``--lane`` thì một tiến trình làm trọn mọi vai trò (dùng để chạy tay / thử; systemd chạy theo làn).
``--symbols`` chia việc theo symbol cho nhiều tiến trình cùng vai trò (mỗi tiến trình chỉ làm các symbol nêu tên); bỏ đi thì làm mọi symbol có kế hoạch. Không có chế độ "một tiến trình chạy cả runner lẫn publisher".
"""

from __future__ import annotations

import argparse
import logging
import re
import signal
import sys
from collections.abc import Sequence

LOGGER = logging.getLogger("core_engine.src.main")
DEFAULT_SOURCE = "default"


def _handle_shutdown_signal(signum: int, frame: object) -> None:
    """SIGTERM (systemctl stop gửi SIGTERM, không phải Ctrl+C): raise SystemExit để chạy các khối `finally` đóng kết nối, rồi configuration.run_* ghi dòng event=stopped."""
    LOGGER.info("event=shutdown_signal signal=%s", signal.Signals(signum).name)
    raise SystemExit(0)


def _source_name(value: str) -> str:
    """Kiểu của tham số --source: tên nguồn nến thêm (chữ thường và số, khai ở redis.input_sources) hoặc ``default`` (= redis.input). Còn dùng làm tên file log nên không được chứa ký tự lạ."""
    name = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9]+", name):
        raise argparse.ArgumentTypeError(f"invalid source name '{value}'")
    return "" if name == DEFAULT_SOURCE else name


def _symbol_list(value: str) -> tuple[str, ...]:
    """Kiểu của tham số --symbols: danh sách symbol cách nhau bằng dấu phẩy (chữ và số), chuẩn hoá chữ hoa, không rỗng, không trùng."""
    names = tuple(item.strip().upper() for item in value.split(",") if item.strip())
    if not names or any(not re.fullmatch(r"[A-Z0-9]+", name) for name in names) or len(set(names)) != len(names):
        raise argparse.ArgumentTypeError(f"invalid symbol list '{value}' (comma-separated, letters and digits, no repeats)")
    return names


def _lane_text(value: str) -> str:
    """Kiểu của tham số --lane: chữ thường, bỏ khoảng trắng hai đầu; hình dạng đúng (``<làn>.<nguồn>`` ở runner, ``<chiến lược>`` ở publisher) do ``parse_args`` kiểm vì nó phụ thuộc vai trò."""
    return value.strip().lower()


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="core_engine.src.main",
        description="DE: candles -> components in Redis db0 (--runner), components -> signals in Redis db1 (--publisher).",
    )
    role = parser.add_mutually_exclusive_group(required=True)
    role.add_argument("--runner", action="store_true", help="tính TRIGGER / CONTEXT / TREND từ nến của MỘT nguồn nến và ghi vào db0 của DE")
    role.add_argument("--publisher", action="store_true", help="đọc db0, lắp bảng theo luật nến đóng, chạy chiến lược của từng kế hoạch và ghi tín hiệu vào db1")
    parser.add_argument("--source", type=_source_name, default=DEFAULT_SOURCE, help="(chỉ --runner) nguồn nến: tên khai ở redis.input_sources, hoặc 'default' (mặc định) cho redis.input")
    parser.add_argument("--symbols", type=_symbol_list, default=(), help="giới hạn tiến trình vào các symbol này (cách nhau bằng dấu phẩy); bỏ đi = mọi symbol có kế hoạch")
    parser.add_argument(
        "--lane", type=_lane_text, default="",
        help="chia vai trò: runner = '<chiến lược>.<nguồn>' hoặc 'trend.<nguồn>' (nguồn 'default' = redis.input; không đi chung --source); publisher = khoá một chiến lược; bỏ đi = trọn mọi vai trò",
    )
    args = parser.parse_args(list(argv))
    if args.publisher and args.source:
        parser.error("--source only applies to --runner (the publisher reads db0 of DE, not a candle source)")
    if args.lane and args.runner:
        if args.source:
            parser.error("--lane already names the candle source after the dot (e.g. combo.og8); do not combine it with --source")
        match = re.fullmatch(r"([a-z0-9_]+)\.([a-z0-9]+)", args.lane)
        if match is None:
            parser.error(f"invalid runner lane '{args.lane}' (use <strategy>.<source> or trend.<source>, e.g. combo.og8, trend.default)")
        args.lane, args.source = match.group(1), "" if match.group(2) == DEFAULT_SOURCE else match.group(2)
    elif args.lane and not re.fullmatch(r"[a-z0-9_]+", args.lane):
        parser.error(f"invalid publisher lane '{args.lane}' (use a strategy key, e.g. combo)")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    from core_engine.src import configuration

    if args.publisher:
        return configuration.run_publisher(symbols=args.symbols, strategy=args.lane)
    return configuration.run_runner(input_name=args.source, symbols=args.symbols, lane=args.lane)


if __name__ == "__main__":
    raise SystemExit(main())
