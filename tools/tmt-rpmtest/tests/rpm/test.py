from tmt_rpmtest import subject

with subject() as target:
    version = target.run(["/usr/bin/curl", "--version"])
    assert version.returncode == 0, version.stderr
    assert version.stdout.startswith("curl "), version.stdout
