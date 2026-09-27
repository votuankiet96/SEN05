from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
from pathlib import Path


PLUGIN_NAME = "BoBacktestRunner"
PROJECT_FILES = (
    "BacktestMapper.cs",
    "BoBacktestRunner.cs",
    "BoBacktestRunner.csproj",
    "config.json",
    "Contracts.cs",
    "GlobalUsings.cs",
    "ReportValidator.cs",
)


def _roots() -> tuple[Path, Path]:
    api_root = Path(__file__).resolve().parents[1]
    c_algo_sources = api_root.parents[2]
    source = api_root / "plugin" / PLUGIN_NAME
    target = c_algo_sources / "Plugins" / PLUGIN_NAME
    return source, target


def _cli_version(path: Path) -> tuple[int, ...]:
    try:
        proc = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return (-1,)
    match = re.search(r"\b(\d+)\.(\d+)(?:\.(\d+))?(?:\.(\d+))?\b", proc.stdout + proc.stderr)
    return tuple(int(part or 0) for part in match.groups()) if match else (-1,)


def _find_cli() -> Path:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    candidates = list((local_app_data / "Spotware" / "cTrader").glob("*/ctrader-cli.exe"))
    standalone = local_app_data / "Programs" / "cTrader CLI" / "ctrader-cli.exe"
    if standalone.exists():
        candidates.append(standalone)
    if not candidates:
        raise FileNotFoundError("ctrader-cli.exe not found")
    return max(candidates, key=_cli_version)


def deploy(*, build: bool = True) -> Path:
    source, target = _roots()
    target_project = target / PLUGIN_NAME
    target_project.mkdir(parents=True, exist_ok=True)

    shutil.copy2(source / f"{PLUGIN_NAME}.sln", target / f"{PLUGIN_NAME}.sln")
    for name in PROJECT_FILES:
        shutil.copy2(source / PLUGIN_NAME / name, target_project / name)

    if build:
        cli = _find_cli()
        subprocess.run(
            [str(cli), "build", str(target_project / f"{PLUGIN_NAME}.csproj")],
            check=True,
        )
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy BoBacktestRunner to cTrader Sources/Plugins.")
    parser.add_argument("--no-build", action="store_true", help="copy files only")
    args = parser.parse_args()
    print(deploy(build=not args.no_build))


if __name__ == "__main__":
    main()
