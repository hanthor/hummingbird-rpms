---
title: Testing RPMs with rpmtest
description: Run fresh-environment tests against local or published Hummingbird RPMs
weight: 65
---

Hello and curl use the Python `tmt-rpmtest` plugin and ordinary tmt tests.
Their fmf trees live under `test/rpmtest/hello/` and `test/rpmtest/curl/`.
The plugin source lives in `tools/tmt-rpmtest/` in this repository.

## Run hello or curl through tmt

Install uv, Podman, and rsync on a Linux runner. Prepare an isolated plugin environment:

```console
uv sync --python python3 --frozen --project tools/tmt-rpmtest
```

Pass the full set of native and noarch binary RPMs from a build:

```console
tools/tmt-rpmtest/.venv/bin/tmt --root test/rpmtest/hello \
  --allow-unsafe-behavior=provision/local run --all --keep \
  prepare --how rpmtest --artifact "$PWD/builds/hello/RPMS/*.rpm"
```

Hello is not available from the public Hummingbird repository at the time of this migration.
Use local candidates for its plans. Curl can also test published packages:

```console
tools/tmt-rpmtest/.venv/bin/tmt --root test/rpmtest/curl \
  --allow-unsafe-behavior=provision/local run --all --keep
```

Each plan prepares its own requested packages and their dependency closure.
All supplied candidate RPMs receive digest checks and metadata records.
The manifest identifies selected and unselected candidates separately.
Debuginfo and debugsource are not added to unrelated runtime environments or reported as runtime-
tested.

Tests make ordinary Python assertions on the runner.
A subject session supplies fixture copying, command execution, file inspection, and external RPM
queries.
Multiple commands in one session share filesystem changes. Separate sessions start fresh.
The subject stays offline and receives no test harness packages.

The two hello plans preserve RPM integrity, ownership, executable, provides, version, and greeting
checks.
The three curl plans preserve package contracts, offline transfers, precise failure codes, and
libcurl compile-and-run coverage.
The transfer test compares output on the runner, so its root no longer needs diffutils.

Testing Farm installs the local plugin in an isolated environment for hello and curl.
The plugin and tests always come from the same checkout.
It passes the complete candidate pool to their tmt plans and retains nested tmt reports under
`TMT_TEST_DATA`.
It also runs `ci/run_tests_rpm.sh`. A failure in either path fails the job.
