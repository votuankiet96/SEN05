"""Install dp_program backend Python dependencies and browser runtime."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def run_command(command: list[str], *, env: dict[str, str] | None = None) -> None:
    print("+ " + " ".join(command), flush=True)
    subprocess.check_call(command, env=env)


def run_pip(args: list[str]) -> None:
    run_command([sys.executable, "-m", "pip", "--disable-pip-version-check", *args])


def install_runtime_deps(requirements_file: Path) -> None:
    run_pip(["install", "--no-cache-dir", "-r", str(requirements_file)])


def install_playwright_chromium(app_dir: Path) -> None:
    browser_cache = app_dir / "runtime" / "cache" / "playwright-browsers"
    browser_cache.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_cache)
    node_options = env.get("NODE_OPTIONS", "")
    if "--use-system-ca" not in node_options:
        env["NODE_OPTIONS"] = f"{node_options} --use-system-ca".strip()

    run_command([sys.executable, "-m", "playwright", "install", "chromium"], env=env)


def main() -> int:
    app_dir = Path(__file__).resolve().parents[1]
    install_runtime_deps(app_dir / "requirements.txt")
    install_playwright_chromium(app_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
