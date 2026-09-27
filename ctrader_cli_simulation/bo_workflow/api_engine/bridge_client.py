from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from .contracts import BacktestApiJob, BacktestApiResult


class BridgeTimeout(TimeoutError):
    pass


class BridgeClient:
    def __init__(self, bridge_root: str | Path):
        self.bridge_root = Path(bridge_root).resolve()
        self.jobs_dir = self.bridge_root / "jobs"
        self.results_dir = self.bridge_root / "results"
        self.failed_dir = self.bridge_root / "failed"

    @staticmethod
    def new_job_id(prefix: str = "job") -> str:
        return f"{prefix}-{uuid.uuid4().hex}"

    def ensure_dirs(self) -> None:
        for name in ("jobs", "claimed", "results", "failed", "heartbeat"):
            (self.bridge_root / name).mkdir(parents=True, exist_ok=True)

    def submit(self, job: BacktestApiJob) -> Path:
        self.ensure_dirs()
        final_path = self.jobs_dir / f"{job.job_id}.json"
        self._write_json_atomic(final_path, job.to_json_dict())
        return final_path

    def wait_result(
        self, job_id: str, *, timeout_seconds: float, poll_seconds: float = 1.0
    ) -> BacktestApiResult:
        deadline = time.monotonic() + timeout_seconds
        result_path = self.results_dir / f"{job_id}.json"
        failed_path = self.failed_dir / f"{job_id}.json"
        while time.monotonic() < deadline:
            if result_path.exists():
                return self._read_result(result_path)
            if failed_path.exists():
                return self._read_result(failed_path)
            time.sleep(poll_seconds)
        raise BridgeTimeout(f"Timed out waiting for plugin result: {job_id}")

    def submit_and_wait(
        self, job: BacktestApiJob, *, timeout_seconds: float, poll_seconds: float = 1.0
    ) -> BacktestApiResult:
        self.submit(job)
        return self.wait_result(
            job.job_id, timeout_seconds=timeout_seconds, poll_seconds=poll_seconds
        )

    @staticmethod
    def _read_result(path: Path) -> BacktestApiResult:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return BacktestApiResult.from_json_dict(payload)

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temp, path)
