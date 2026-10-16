---
title: Testing RPMs with rpmtest
description: Run fresh-environment tests against local or published Hummingbird RPMs
weight: 65
---

Hello and curl use the Python `tmt-rpmtest` plugin and ordinary tmt tests.
Their fmf trees live under `test/rpmtest/hello/` and `test/rpmtest/curl/`.
The plugin source lives in `tools/tmt-rpmtest/` in this repository.

## Define the environment

Each plan's `prepare` block defines the RPM package set. For example,
`test/rpmtest/hello/plans/greeting.fmf` requests `hello` from the configured repository.
The plugin starts with an empty filesystem, installs the package and its required dependencies,
and saves an archive for that plan. Local build artifacts override repository packages by name.

tmt runs Python tests and assertions on its provisioned guest.
`provision: local` uses the current machine. A `connect` guest uses an existing machine over SSH.
Each `with subject() as target:` block creates a fresh container session from the plan's archive.
`target.run(...)` executes a program inside that minimal environment.
Commands in one session share filesystem changes. A new session starts from the original archive.

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

For an existing SSH guest with Podman, rsync, and Python 3.11 installed:

```console
tools/tmt-rpmtest/.venv/bin/tmt --root test/rpmtest/hello run --all --keep \
  provision --how connect --guest "$VM_ADDRESS" --user root --key "$VM_KEY" \
  prepare --how rpmtest --python python3.11 --artifact "$PWD/builds/hello/RPMS/*.rpm"
```

The artifact paths refer to the controller. The plugin copies and checks them on the guest.

Each plan prepares its own requested packages and their dependency closure.
All supplied candidate RPMs receive digest checks and metadata records.
The manifest identifies selected and unselected candidates separately.
Debuginfo and debugsource are not added to unrelated runtime environments or reported as runtime-
tested.

Tests make ordinary Python assertions on the provisioned guest.
A subject session supplies fixture copying, command execution, file inspection, and external RPM
queries.
Multiple commands in one session share filesystem changes. Separate sessions start fresh.
The subject stays offline and receives no test harness packages.

The two hello plans preserve RPM integrity, ownership, executable, provides, version, and greeting
checks.
The three curl plans preserve package contracts, offline transfers, precise failure codes, and
libcurl compile-and-run coverage.
The transfer test compares output on the guest, so its root no longer needs diffutils.

Testing Farm uses native fmf discovery for the hello and curl tests in `ci/run_tests_rpm.fmf`.
Preparation downloads artifacts, filters native and noarch RPMs, and installs this checkout's
Python package in an isolated guest environment. The RPM helper builds the requested environment.
Each test gets its own tmt result. The existing `ci/run_tests_rpm.sh` checks have a separate result.
A failure in either path fails the job. There is no nested tmt invocation.
