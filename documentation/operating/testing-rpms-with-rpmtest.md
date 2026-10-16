---
title: Testing RPMs with rpmtest
description: Run fresh-environment tests against local or published Hummingbird RPMs
weight: 65
---

Hello, curl, and NATS use the Python `tmt-rpmtest` plugin and ordinary tmt tests.
They share the fmf root at `test/rpmtest/`, with plans and tests under each package directory.
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

`target.packages.contents()`, `links()`, and `runtime_links()` expose package contents, symlink
resolution, and ELF dependencies for Python assertions. `with target.service(argv, ready=probe):`
keeps a foreground service running while subject commands execute in the same container.
The service stops when the block exits, and logs are retained with test evidence.
`with target.companions({"web": Companion(image=...)})` connects the subject to declared containers
on a private internal network. Image digests, readiness attempts, and logs are retained; containers
and the network are removed when the scope exits, including after failures.
See the [plugin README](../../tools/tmt-rpmtest/README.md) for examples and limitations.

## Run package tests through tmt

Install uv, Podman, and rsync on a Linux runner. Prepare an isolated plugin environment:

```console
uv sync --python python3 --frozen --project tools/tmt-rpmtest
```

Pass the full set of native and noarch binary RPMs from a build:

```console
tools/tmt-rpmtest/.venv/bin/tmt --root test/rpmtest \
  --allow-unsafe-behavior=provision/local run --all --keep \
  plans --name '^/hello/plans/' \
  prepare --how rpmtest --artifact "$PWD/builds/hello/RPMS/*.rpm"
```

Hello is not available from the public Hummingbird repository at the time of this migration.
Use local candidates for its plans. Curl can also test published packages:

```console
tools/tmt-rpmtest/.venv/bin/tmt --root test/rpmtest \
  --allow-unsafe-behavior=provision/local run --all --keep \
  plans --name '^/curl/plans/'
```

For an existing SSH guest with Podman, rsync, and Python 3.11 installed:

```console
tools/tmt-rpmtest/.venv/bin/tmt --root test/rpmtest run --all --keep \
  plans --name '^/hello/plans/' \
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
The subject has no external network access by default and receives no test harness packages.
Use `with target.network(internet=True):` for deliberate public-network checks, or pass
`internet=True` to `target.companions(...)` for a scope containing companions. Both restore offline
execution on exit. Public HTTPS trust checks should include `ca-certificates` in the package set.

The two hello plans preserve RPM integrity, ownership, executable, provides, version, and greeting
checks.
The three curl plans preserve package contracts, offline transfers, precise failure codes, and
libcurl compile-and-run coverage.
The transfer test compares output on the guest, so its root no longer needs diffutils.

NATS 2.12 and 2.14 share tests under `test/rpmtest/shared/nats/`. Each version has its own
package plans. Contract checks verify RPM contents, ownership, dependencies, version, and the
shipped configuration. Messaging checks start the server as `nats` with that configuration and
exercise wildcard subscriptions, empty and binary payloads, and request/reply. The Python client
runs in a digest-pinned companion on a private network; the server root contains only its RPM
and required dependencies. These tests do not start systemd or test JetStream persistence.

Run either version locally:

```console
tools/tmt-rpmtest/.venv/bin/tmt --root test/rpmtest \
  --allow-unsafe-behavior=provision/local run --all --keep \
  plans --name '^/nats-server2\.12/plans/'
```

Use the plan filter `'^/nats-server2\.14/plans/'` for the other version. To test a local build, append
`prepare --how rpmtest --artifact "$PWD/builds/nats-server2.12/RPMS/*.rpm"`.

Testing Farm uses one plan with native fmf discovery in `ci/run_tests_rpm.fmf`.
Discovery filters tests by component before execution, so curl runs do not include hello or NATS
results. Preparation downloads artifacts, filters native and noarch RPMs, and installs this checkout's
Python package in an isolated guest environment. Each selected test prepares its own minimal root
from its canonical package plan and cleans up afterward. Local plans reuse plugin preparation.
Each test gets its own tmt result, including preparation failures. The existing
`ci/run_tests_rpm.sh` checks have a separate result. A failure in either path fails the job.
There is no nested tmt invocation.

Add a package's plans and test metadata under the shared root; CI discovery needs no package list.
Each local plan must select package-qualified tests, such as `^/curl/tests/transfers$`.
Always filter local plans: a package subdirectory used as `--root` still resolves to the shared tree.
See [the authoring guide](../../test/rpmtest/AGENTS.md) for onboarding examples.

After adding a package's `tests/` directory, run `make generate` to update build triggers.
Adopted packages watch their own tests, the shared test directory, the plugin,
and the shared CI entry points.
