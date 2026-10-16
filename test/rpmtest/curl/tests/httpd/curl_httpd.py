from tmt_rpmtest import Companion, subject

apache = Companion(
    image=(
        "docker.io/library/httpd@sha256:"
        "1b766f17b84026429b7cb243317b142921b24432336e798bc881c43f45ed9567"
    ),
    ready=("httpd", "-t"),
)

with subject() as target:
    with target.companions({"web": apache}, timeout=30):
        # Configuration validation can finish before Apache accepts connections.
        response = target.run(
            [
                "/usr/bin/curl",
                "--retry",
                "5",
                "--retry-connrefused",
                "--connect-timeout",
                "5",
                "--max-time",
                "10",
                "--fail",
                "--silent",
                "--show-error",
                "http://web/",
            ],
            timeout=60,
        )
        assert response.returncode == 0, response.stderr
        assert "It works!" in response.stdout, response.stdout
