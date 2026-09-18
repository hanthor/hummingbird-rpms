"""Tests for the lookaside-cache upload script's retry behavior."""

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UPLOAD_SCRIPT = ROOT / "ci" / "upload-to-lookaside-cache.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def _run_upload(
    tmp_path: Path, failures: int, max_attempts: int = 5
) -> tuple[subprocess.CompletedProcess[str], int, list[str]]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    attempts_file = tmp_path / "attempts"
    sleep_log = tmp_path / "sleeps"

    _write_executable(
        fake_bin / "aws",
        """#!/bin/bash
set -euo pipefail
attempts=0
if [[ -f "${AWS_ATTEMPTS_FILE}" ]]; then
    attempts=$(<"${AWS_ATTEMPTS_FILE}")
fi
attempts=$((attempts + 1))
printf '%s\\n' "${attempts}" > "${AWS_ATTEMPTS_FILE}"
if (( attempts <= AWS_FAILURES )); then
    echo 'aws: [ERROR]: Failed to connect to proxy URL' >&2
    exit 255
fi
""",
    )
    _write_executable(
        fake_bin / "sleep",
        """#!/bin/bash
set -euo pipefail
printf '%s\\n' "$1" >> "${SLEEP_LOG}"
""",
    )

    source = tmp_path / "source.tar.gz"
    source.write_bytes(b"source archive")
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "AWS_ATTEMPTS_FILE": str(attempts_file),
        "AWS_FAILURES": str(failures),
        "SLEEP_LOG": str(sleep_log),
        "LOOKASIDE_UPLOAD_MAX_ATTEMPTS": str(max_attempts),
    }
    result = subprocess.run(
        [str(UPLOAD_SCRIPT), "-f", str(source), "-p", "test-package"],
        env=env,
        capture_output=True,
        text=True,
    )
    attempts = int(attempts_file.read_text())
    sleeps = sleep_log.read_text().splitlines() if sleep_log.exists() else []
    return result, attempts, sleeps


def test_upload_retries_with_exponential_backoff(tmp_path: Path) -> None:
    result, attempts, sleeps = _run_upload(tmp_path, failures=3)

    assert result.returncode == 0
    assert attempts == 4
    assert sleeps == ["5", "10", "20"]
    assert "S3 upload attempt 3/5 failed; retrying in 20s" in result.stderr
    assert "Upload complete!" in result.stdout


def test_upload_stops_after_max_attempts(tmp_path: Path) -> None:
    result, attempts, sleeps = _run_upload(tmp_path, failures=3, max_attempts=3)

    assert result.returncode == 255
    assert attempts == 3
    assert sleeps == ["5", "10"]
    assert "S3 upload failed after 3 attempts" in result.stderr


def test_upload_does_not_sleep_after_first_attempt_success(tmp_path: Path) -> None:
    result, attempts, sleeps = _run_upload(tmp_path, failures=0)

    assert result.returncode == 0
    assert attempts == 1
    assert sleeps == []
