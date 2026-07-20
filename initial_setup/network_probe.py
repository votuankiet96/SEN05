"""
Standalone network probe for DP Program operators.

This file is intentionally not imported by core_engine. It only runs when called
directly and writes diagnostics to runtime/logs/system/network_probe.log.
"""

from __future__ import annotations

import argparse
import base64
import os
import socket
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
LOG_FILE = APP_ROOT / "runtime" / "logs" / "system" / "network_probe.log"


@dataclass
class ProbeResult:
    layer: str
    target: str
    status: str
    latency_ms: int | None
    detail: str


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def sanitize_detail(value: str, limit: int = 220) -> str:
    value = " ".join(str(value).replace("\r", " ").replace("\n", " ").split())
    if len(value) > limit:
        return value[: limit - 3] + "..."
    return value


def dns_probe(host: str, timeout: float) -> ProbeResult:
    del timeout
    start = time.perf_counter()
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        ips = []
        for item in infos:
            ip = item[4][0]
            if ip not in ips:
                ips.append(ip)
        return ProbeResult("DNS", host, "OK", elapsed_ms(start), ",".join(ips[:4]) or "-")
    except Exception as exc:
        return ProbeResult("DNS", host, "FAIL", elapsed_ms(start), type(exc).__name__ + ": " + str(exc))


def tcp_probe(host: str, port: int, timeout: float) -> ProbeResult:
    target = f"{host}:{port}"
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return ProbeResult("TCP", target, "OK", elapsed_ms(start), "connected")
    except Exception as exc:
        return ProbeResult("TCP", target, "FAIL", elapsed_ms(start), type(exc).__name__ + ": " + str(exc))


def https_probe(host: str, timeout: float) -> ProbeResult:
    target = f"https://{host}/"
    start = time.perf_counter()
    try:
        context = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=host) as sock:
                sock.settimeout(timeout)
                request = (
                    f"GET / HTTP/1.1\r\n"
                    f"Host: {host}\r\n"
                    "User-Agent: SEN05-DP-NetworkProbe/1.0\r\n"
                    "Accept: */*\r\n"
                    "Connection: close\r\n\r\n"
                )
                sock.sendall(request.encode("ascii"))
                data = sock.recv(4096)
        first = data.splitlines()[0].decode("latin1", errors="replace") if data else "no response"
        ok = first.startswith("HTTP/1.1 2") or first.startswith("HTTP/1.1 3") or first.startswith("HTTP/2 2")
        return ProbeResult("HTTPS", target, "OK" if ok else "WARN", elapsed_ms(start), first)
    except Exception as exc:
        return ProbeResult("HTTPS", target, "FAIL", elapsed_ms(start), type(exc).__name__ + ": " + str(exc))


def websocket_probe(host: str, timeout: float) -> ProbeResult:
    path = f"/socket.io/websocket?from=chart%2F&date={int(time.time())}"
    target = f"wss://{host}{path}"
    start = time.perf_counter()
    try:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        context = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=host) as sock:
                sock.settimeout(timeout)
                request = (
                    f"GET {path} HTTP/1.1\r\n"
                    f"Host: {host}\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    f"Sec-WebSocket-Key: {key}\r\n"
                    "Sec-WebSocket-Version: 13\r\n"
                    "Origin: https://www.tradingview.com\r\n"
                    "User-Agent: SEN05-DP-NetworkProbe/1.0\r\n\r\n"
                )
                sock.sendall(request.encode("ascii"))
                data = sock.recv(4096)
        first = data.splitlines()[0].decode("latin1", errors="replace") if data else "no response"
        ok = " 101 " in first or first.endswith(" 101 Switching Protocols")
        return ProbeResult("WS", f"wss://{host}", "OK" if ok else "WARN", elapsed_ms(start), first)
    except Exception as exc:
        return ProbeResult("WS", f"wss://{host}", "FAIL", elapsed_ms(start), type(exc).__name__ + ": " + str(exc))


def write_results(results: list[ProbeResult]) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(f"{utc_now()} | INFO    | NETWORK | Probe run started\n")
        for r in results:
            latency = "-" if r.latency_ms is None else f"{r.latency_ms}ms"
            fh.write(
                f"{utc_now()} | {r.status:<7} | NETWORK | {r.layer:<5} | "
                f"{r.target:<48} | latency={latency:<8} | detail={sanitize_detail(r.detail)}\n"
            )
        overall = "OK" if all(r.status == "OK" for r in results) else "WARN"
        failures = sum(1 for r in results if r.status == "FAIL")
        warnings = sum(1 for r in results if r.status == "WARN")
        fh.write(
            f"{utc_now()} | {overall:<7} | NETWORK | Summary | "
            f"checks={len(results)} | warnings={warnings} | failures={failures}\n"
        )


def run_once(timeout: float) -> list[ProbeResult]:
    results: list[ProbeResult] = []
    for host in ["www.tradingview.com", "data.tradingview.com", "prodata.tradingview.com"]:
        results.append(dns_probe(host, timeout))
        results.append(tcp_probe(host, 443, timeout))
    results.append(https_probe("www.tradingview.com", timeout))
    results.append(websocket_probe("data.tradingview.com", timeout))
    results.append(websocket_probe("prodata.tradingview.com", timeout))
    write_results(results)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Standalone TradingView network probe.")
    parser.add_argument("--watch", action="store_true", help="run continuously until Ctrl+C")
    parser.add_argument("--interval-sec", type=int, default=60, help="watch interval in seconds")
    parser.add_argument("--timeout-sec", type=float, default=5.0, help="per-check timeout")
    args = parser.parse_args()

    while True:
        results = run_once(args.timeout_sec)
        for r in results:
            latency = "-" if r.latency_ms is None else f"{r.latency_ms}ms"
            print(f"{r.status:<7} {r.layer:<5} {r.target:<48} {latency:<8} {sanitize_detail(r.detail)}")
        print(f"log: {LOG_FILE}")
        if not args.watch:
            return 0 if all(r.status != "FAIL" for r in results) else 1
        time.sleep(max(5, args.interval_sec))


if __name__ == "__main__":
    raise SystemExit(main())
