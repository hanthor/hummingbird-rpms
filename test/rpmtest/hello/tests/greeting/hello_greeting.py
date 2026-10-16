from tmt_rpmtest import subject

with subject() as target:
    greeting = target.run(["/usr/bin/hello"], environment={"LC_ALL": "C"})
    assert greeting.returncode == 0 and greeting.stdout == "Hello, world!\n", greeting
    custom = target.run(
        ["/usr/bin/hello", "--greeting=rpmtest"], environment={"LC_ALL": "C"}
    )
    assert custom.returncode == 0 and custom.stdout == "rpmtest\n", custom
