from tmt_rpmtest import subject

with subject() as target:
    assert "hello" in target.packages.installed()
    verified = target.packages.verify("hello")
    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert target.packages.owners("/usr/bin/hello") == {"hello"}
    executable = target.stat("/usr/bin/hello")
    assert executable.isfile() and executable.mode & 0o111
    assert "hello" in target.packages.metadata("hello", "provides")
    version = target.run(["/usr/bin/hello", "--version"], environment={"LC_ALL": "C"})
    assert version.returncode == 0 and "hello" in version.stdout, version
