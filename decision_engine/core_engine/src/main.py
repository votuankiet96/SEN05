"""Lối vào mỏng của một tiến trình DE: ``python -m core_engine.src.main --strategy <tên>`` (tiến trình chiến lược) hoặc ``--trend <khối>`` (tiến trình trend).

Chỉ đọc tham số dòng lệnh, bắt SIGTERM rồi giao cho ``configuration.run`` / ``configuration.run_trend``: mọi điều phối (đọc de_config.yaml, dựng việc, vòng lặp sự kiện) nằm ở configuration.py.
Mỗi chiến lược một tiến trình (deploy/de-signal@.service) và mỗi khối trend một tiến trình (deploy/de-trend@.service): không có chế độ "một tiến trình chạy mọi thứ".
Thêm ``.<nguồn>`` sau tên (``--strategy combo.og8``) để tiến trình đọc nến từ nguồn nến thêm ``redis.input_sources.<nguồn>`` thay vì ``redis.input``: mỗi nguồn nến một nhóm tiến trình riêng,
nên một Redis nguồn chết chỉ làm nhóm của nó dừng.
"""

from __future__ import annotations

import argparse
import logging
import re
import signal
import sys
from collections.abc import Sequence

LOGGER = logging.getLogger("core_engine.src.main")


def _handle_shutdown_signal(signum: int, frame: object) -> None:
    """SIGTERM (systemctl stop gửi SIGTERM, không phải Ctrl+C): raise SystemExit để chạy các khối `finally` đóng kết nối, rồi configuration.run ghi dòng event=stopped."""
    LOGGER.info("event=shutdown_signal signal=%s", signal.Signals(signum).name)
    raise SystemExit(0)


def _strategy_name(value: str) -> str:
    """Kiểu của tham số --strategy / --trend: ``<tên>`` hoặc ``<tên>.<nguồn nến>`` (vd ``combo.og8``, nguồn khai ở redis.input_sources): chữ thường, tên gồm chữ/số/gạch dưới, nguồn gồm chữ/số
    (còn dùng làm tên file log nên không được chứa ký tự lạ)."""
    name = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9_]+(\.[a-z0-9]+)?", name):
        raise argparse.ArgumentTypeError(f"invalid strategy name '{value}'")
    return name


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="core_engine.src.main", description="DE: Redis db0 candles -> strategy / trend -> Redis db1 signals and trend timelines (one strategy or one trend source per process)."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--strategy", type=_strategy_name, help="chạy MỘT chiến lược (phải nằm trong live.enabled_strategies); thêm .<nguồn> để đọc nến từ redis.input_sources.<nguồn>")
    group.add_argument(
        "--trend", type=_strategy_name, help="chạy MỘT khối trend (phải khai ở redis.trend_outputs): tính hướng trend và ghi dòng thời gian; thêm .<nguồn> để đọc nến từ redis.input_sources.<nguồn>"
    )
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    from core_engine.src import configuration

    if args.trend:
        method, _, input_name = args.trend.partition(".")
        return configuration.run_trend(method=method, input_name=input_name)
    strategy, _, input_name = args.strategy.partition(".")
    return configuration.run(strategy=strategy, input_name=input_name)


if __name__ == "__main__":
    raise SystemExit(main())
