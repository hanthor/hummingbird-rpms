from pathlib import Path

from tmt_rpmtest import subject

payload = Path(__file__).resolve().parents[2] / "fixtures/payload.txt"
with subject() as target:
    target.copy(payload, "/workspace/fixtures/payload.txt")
    copied = target.run(
        [
            "/usr/bin/curl",
            "--fail",
            "--silent",
            "--show-error",
            "file:///workspace/fixtures/payload.txt",
            "--output",
            "/tmp/copied",
        ]
    )
    assert copied.returncode == 0, copied.stderr
    contents = target.run(["/usr/bin/curl", "--fail", "--silent", "file:///tmp/copied"])
    assert contents.returncode == 0 and contents.stdout == payload.read_text(), contents
    missing = target.run(
        ["/usr/bin/curl", "--fail", "--silent", "file:///workspace/fixtures/missing"]
    )
    assert missing.returncode == 37, missing
    restricted = target.run(
        ["/usr/bin/curl", "--proto", "=https", "file:///workspace/fixtures/payload.txt"]
    )
    assert restricted.returncode == 1, restricted
