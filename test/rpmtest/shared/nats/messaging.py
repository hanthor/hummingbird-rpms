"""Exercise the RPM server with client tools confined to a companion container."""

from pathlib import Path

from tmt_rpmtest import Companion, subject

CLIENT_IMAGE = (
    "quay.io/hummingbird-ci/hummingbird-builder@sha256:"
    "3defcdbfc19d33ec3686cfb97be7372fb97d2c8ea3935697b54f7d80b11fb59c"
)

with subject() as target:
    with target.companions(
        {
            "client": Companion(
                CLIENT_IMAGE,
                command=("/usr/bin/python3", "-c", "import time; time.sleep(600)"),
                ready=("/usr/bin/python3", "-c", "pass"),
            ),
        }
    ) as clients:
        with target.service(
            [
                "/usr/bin/nats-server",
                "--config",
                "/etc/nats-server/nats-server.conf",
                "--pid",
                "/tmp/nats.pid",
            ],
            ready=["/usr/bin/nats-server", "--signal", "reopen=/tmp/nats.pid"],
            user="nats",
        ):
            result = clients["client"].run(
                [
                    "/usr/bin/python3",
                    "-c",
                    Path(__file__).with_name("client.py").read_text(),
                ],
                timeout=45,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            assert "publish/subscribe and request/reply passed" in result.stdout
