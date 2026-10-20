"""Run the actual shared shell policy with deterministic download failures."""

import os
import shlex
import subprocess
from pathlib import Path

import pytest

from tmt_rpmtest.environment import rpm

HELPER = Path(rpm.__file__).with_name("download.sh")


def run_download(tmp_path, diagnostic, *, failures=2, status=1, shell="sh"):
    download = tmp_path / "download"
    download.write_text("""#!/bin/sh
count=0
[ ! -f "$STATE" ] || count=$(cat "$STATE")
count=$((count + 1))
echo "$count" > "$STATE"
echo "download output $count"
if [ "$count" -le "$FAILURES" ]; then
    echo "$DIAGNOSTIC" >&2
    exit "$STATUS"
fi
echo "download complete" >&2
""")
    download.chmod(0o755)
    sleep = tmp_path / "sleep"
    sleep.write_text('#!/bin/sh\necho "$1" >> "$DELAYS"\n')
    sleep.chmod(0o755)
    result = subprocess.run(
        [shell, "-c", 'set -eu; . "$1"; rpmtest_download "$2"', "test", str(HELPER), str(download)],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "STATE": str(tmp_path / "attempts"),
            "DELAYS": str(tmp_path / "delays"),
            "DIAGNOSTIC": diagnostic,
            "FAILURES": str(failures),
            "STATUS": str(status),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    attempts = int((tmp_path / "attempts").read_text())
    delays = tmp_path / "delays"
    return result, attempts, delays.read_text().splitlines() if delays.exists() else []


@pytest.mark.parametrize(
    "diagnostic",
    [
        "curl: (6) Could not resolve host: example.org",
        "curl: (22) The requested URL returned error: 503",
        "Curl error (28): Timeout was reached",
        "Curl error (56): Failure when receiving data from the peer",
        "Status code: 503 for https://example.org/repodata/repomd.xml",
        "response status code 429: Too Many Requests",
        "Get https://example.org/: read: connection reset by peer",
        "dial tcp: lookup example.org: no such host",
        "net/http: TLS handshake timeout",
        "unexpected EOF",
        'Get "https://example.org/": EOF',
    ],
)
def test_transient_failures_back_off_then_succeed(tmp_path, diagnostic):
    result, attempts, delays = run_download(tmp_path, diagnostic)
    assert result.returncode == 0, result.stderr
    assert attempts == 3 and delays == ["2", "4"]
    assert result.stderr.count(diagnostic) == 2
    assert "download complete" in result.stderr
    assert result.stdout.splitlines() == [
        "download output 1",
        "download output 2",
        "download output 3",
    ]


@pytest.mark.parametrize(
    "diagnostic,status",
    [
        ("No match for argument: missing-package", 1),
        ("nothing provides required-library", 1),
        ("manifest unknown", 1),
        ("response status code 404: Not Found", 1),
        ("UNAUTHORIZED: authentication required", 1),
        ("Forbidden; response status code 503", 1),
        ("curl: (60) SSL peer certificate cannot be authenticated", 60),
        ("SSL certificate verify failed", 1),
        ("unexpected programming error", 1),
        ("connection reset by peer", 130),
        ("connection reset by peer", 143),
    ],
)
def test_permanent_errors_and_interruptions_fail_immediately(tmp_path, diagnostic, status):
    result, attempts, delays = run_download(tmp_path, diagnostic, status=status)
    assert result.returncode == status
    assert attempts == 1 and delays == []
    assert diagnostic in result.stderr


@pytest.mark.parametrize("shell", ["sh", "bash"])
def test_retry_budget_is_bounded_and_keeps_last_failure(tmp_path, shell):
    result, attempts, delays = run_download(
        tmp_path, "connection refused", failures=100, status=7, shell=shell
    )
    assert result.returncode == 7
    assert attempts == 4 and delays == ["2", "4", "8"]
    assert result.stderr.count("connection refused") == 4


def test_command_timeout_is_retryable(tmp_path):
    result, attempts, delays = run_download(tmp_path, "", status=124)
    assert result.returncode == 0
    assert attempts == 3 and delays == ["2", "4"]


def test_only_resolution_retries_and_installation_stays_offline():
    assert "rpmtest_download rpmtest_resolve" in rpm.RESOLVE
    assert "--store=/work/transaction" in rpm.RESOLVE
    assert "cacheonly=metadata" in rpm.RESOLVE
    assert "rpmtest_download" not in rpm.ASSEMBLE


def test_resolution_retry_discards_partial_transaction(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    dnf = tmp_path / "dnf5"
    dnf.write_text("""#!/bin/sh
if [ ! -e "$STATE" ]; then
    touch "$STATE"
    mkdir -p "$WORK/transaction/packages"
    echo stale > "$WORK/transaction/packages/stale.rpm"
    echo retained > "$WORK/retained.rpm"
    echo 'Curl error (6): Could not resolve host' >&2
    exit 1
fi
test ! -e "$WORK/transaction/packages/stale.rpm" || exit 99
test -e "$WORK/retained.rpm" || exit 98
mkdir -p "$WORK/transaction/packages"
echo selected > "$WORK/transaction/packages/selected.rpm"
""")
    dnf.chmod(0o755)
    sleep = tmp_path / "sleep"
    sleep.write_text("#!/bin/sh\nexit 0\n")
    sleep.chmod(0o755)
    result = subprocess.run(
        ["sh", "-c", rpm.RESOLVE.replace("/work", shlex.quote(str(work))), "test", "curl"],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "STATE": str(tmp_path / "state"),
            "WORK": str(work),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert [path.name for path in (work / "packages").iterdir()] == ["selected.rpm"]
