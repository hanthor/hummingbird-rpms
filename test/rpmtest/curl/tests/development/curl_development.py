from pathlib import Path

from tmt_rpmtest import subject

fixtures = Path(__file__).resolve().parents[2] / "fixtures"
with subject() as target:
    target.copy(fixtures / "libcurl_probe.c", "/workspace/libcurl_probe.c")
    target.copy(fixtures / "payload.txt", "/workspace/fixtures/payload.txt")
    compiled = target.run(
        [
            "/usr/bin/gcc",
            "-std=c17",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-o",
            "/workspace/libcurl_probe",
            "/workspace/libcurl_probe.c",
            "-lcurl",
        ]
    )
    assert compiled.returncode == 0, compiled.stderr
    probe = target.run(
        ["/workspace/libcurl_probe", "file:///workspace/fixtures/payload.txt"]
    )
    assert probe.returncode == 0 and probe.stdout == "rpmtest curl payload\n", probe
