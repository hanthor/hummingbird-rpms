"""Check the packaged NATS server without adding inspection tools to its root."""

import os

from tmt_rpmtest import subject

package = os.environ["NATS_PACKAGE"]
with subject() as target:
    assert "nats-server" in target.packages.metadata(package, "provides")
    contents = target.packages.contents(package)
    # Minimal roots deliberately omit RPM documentation.
    missing = [
        item.path
        for item in contents
        if item.info is None and not item.path.startswith("/usr/share/doc/")
    ]
    assert contents and not missing, missing
    for path in (
        "/usr/bin/nats-server",
        "/etc/nats-server/nats-server.conf",
        "/usr/lib/systemd/system/nats-server.service",
        "/usr/lib/sysusers.d/nats-server.conf",
        "/var/lib/nats-server",
        "/var/log/nats-server",
    ):
        assert target.packages.owners(path) == {package}
    assert target.stat("/usr/bin/nats-server").mode & 0o111
    assert all(link.resolved for link in target.packages.links(package))
    result = target.run(["/usr/bin/nats-server", "--version"])
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().startswith(
        "nats-server: v" + os.environ["NATS_VERSION_PREFIX"]
    ), result.stdout
    result = target.run(
        [
            "/usr/bin/nats-server",
            "--test",
            "--config",
            "/etc/nats-server/nats-server.conf",
        ]
    )
    assert result.returncode == 0, result.stdout + result.stderr
