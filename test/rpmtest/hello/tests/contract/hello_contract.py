from tmt_rpmtest import subject

with subject() as target:
    verified = target.rpm("-V", "hello")
    assert verified.returncode == 0, verified.stdout + verified.stderr
    owner = target.rpm("-qf", "--qf", "%{NAME}", "/usr/bin/hello")
    assert owner.returncode == 0 and owner.stdout == "hello", owner
    executable = target.stat("/usr/bin/hello")
    assert executable.isfile() and executable.mode & 0o111
    provides = target.rpm("-q", "--provides", "hello")
    assert provides.returncode == 0, provides.stderr
    assert "hello" in provides.stdout, provides.stdout
    version = target.run(["/usr/bin/hello", "--version"], environment={"LC_ALL": "C"})
    assert version.returncode == 0 and "hello" in version.stdout, version
