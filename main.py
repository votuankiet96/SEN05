"""
Điểm khởi động (entry point) cho dashboard biểu đồ chiến lược SEN05.

Mô tả:
    Phân tích tham số CLI rồi khởi động Flask server qua chart.server.run().
    Server lắng nghe tại địa chỉ mặc định 127.0.0.1:8516.

Cách chạy:
    python -m core_python.main
    python -m core_python.main --port 9000
    python -m core_python.main --debug
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Cho phép chạy trực tiếp bằng `python main.py` ngoài package context.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core_python.chart.server import DEFAULT_HOST, DEFAULT_PORT, run


def _parse_args() -> argparse.Namespace:
    """
    Phân tích tham số dòng lệnh cho server.

    Returns:
        Namespace với các trường: host (str), port (int), debug (bool).
    """
    parser = argparse.ArgumentParser(description="Run simplified SEN05 strategy chart.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def main() -> int:
    """
    Khởi động Flask server và in địa chỉ truy cập ra console.

    Side Effects:
        Chạy Flask web server — tiến trình sẽ block tại đây cho đến khi dừng (Ctrl+C).

    Returns:
        0 (mã thoát thành công, thực tế không bao giờ return vì server block).
    """
    args = _parse_args()
    print(f"SEN05 simplified chart: http://{args.host}:{args.port}")
    run(host=args.host, port=args.port, debug=args.debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
