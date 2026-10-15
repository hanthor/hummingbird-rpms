import re

from tmt_rpmtest import subject

with subject() as target:
    verified = target.rpm("-V", "curl", "libcurl", "libcurl-devel")
    assert verified.returncode == 0, verified.stdout + verified.stderr
    paths = ["/usr/bin/curl", "/usr/lib64/libcurl.so.4", "/usr/include/curl/curl.h"]
    owners = target.rpm("-qf", "--qf", "%{NAME}\n", *paths)
    assert owners.returncode == 0, owners.stderr
    assert owners.stdout.splitlines() == ["curl", "libcurl", "libcurl-devel"], (
        owners.stdout
    )
    assert target.stat(paths[0]).mode & 0o111
    target.stat(paths[1])
    assert target.stat(paths[2]).isfile()
    version = target.run(["/usr/bin/curl", "--version"])
    assert version.returncode == 0, version.stderr
    assert re.search(r"^curl [0-9]+\.[0-9]+", version.stdout), version.stdout
    protocols = next(
        line for line in version.stdout.splitlines() if line.startswith("Protocols:")
    )
    assert {"file", "http", "https"} <= set(protocols.split()[1:]), protocols
    features = next(
        line for line in version.stdout.splitlines() if line.startswith("Features:")
    )
    assert "SSL" in features.split()[1:], features
