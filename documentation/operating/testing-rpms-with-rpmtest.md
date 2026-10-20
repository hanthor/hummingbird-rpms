---
title: Testing RPMs with rpmtest
description: Run fresh-environment tests against local or published Hummingbird RPMs
weight: 65
---

Hello, curl, and NATS use the Python `tmt-rpmtest` plugin and ordinary tmt tests.
They share the fmf root at `test/rpmtest/`, with plans and tests under each package directory.
The plugin source lives in `tools/tmt-rpmtest/` in this repository.

## Standard package contract

The contract has two independently reported layers: universal package checks and package-specific
tests. Universal checks establish that the build artifacts are valid and install correctly.
Package-specific tests establish that the installed product supplies its promised interfaces and
performs useful work. Passing universal checks alone does not establish functional coverage.

CI keeps the existing legacy RPM checks as one result and runs the new installed package contracts
as a separate result. Native package tests retain individual results. The legacy shell runner and
its YAML definitions remain unchanged, including package overrides and known issues. Universal and
functional categories apply to the new rpmtest framework.

For enabled components, the installed-contract runner prepares one minimal profile per native/noarch
output, including debug and development outputs. It checks resolved RPM
identities, installed-file verification, and dynamic ELF linkage through shared code. Local package
tests invoke the same checks before their assertions. Pilot scripts keep their interface expectations
and functional behavior, while the shared helper performs their baseline verification.

Run the installed contracts independently using the rpmtest Python environment:

```sh
tools/tmt-rpmtest/.venv/bin/python -m tmt_rpmtest.execution.universal \
  --artifacts "$PWD/builds/curl/RPMS" --directory "$PWD/builds/curl/contracts" \
  --component curl-main
```

The artifact directory must contain native/noarch binary RPMs. Empty, foreign-architecture, and
ambiguous candidate sets fail. Enabled repository URLs come from `ci/repos/`, ordered by configured
priority, with Hummingbird ahead of the Fedora fallback. Override the configuration directory with
`--repos`. Package plans similarly prefer earlier repository URLs over later ones.
CI reports an explicit skip when a source artifact and only foreign-architecture binaries are
present. An empty build does not qualify for that skip and fails the required checks.

`results.json` records each output's result and environment path; each environment retains a
`contract.json` with U05, U06, and U07 observations. Functional scenarios carry `tag: functional`;
interface checks carry `tag: contract`. `coverage.json` reports declared functional scenarios or
missing coverage. Declaration does not mean execution: native tmt results establish which scenarios
actually passed. Components with enabled fmf package tests must declare functional coverage;
missing declarations fail their contract gate. Components without those tests continue to run their
existing legacy checks.

CI resolves the build artifact to an immutable digest, compares downloaded RPMs with that OCI
manifest's filenames and SHA-256 digests, then records
source, selected, and foreign-architecture outputs in `rpm-artifacts.json`. Acquisition verifies RPM
digests for all outputs, including foreign outputs, and requires an actual source RPM. Every new test
rechecks
the staged candidates against this catalog. Local artifact directories get the same acquisition
catalog, but have no external manifest against which to prove completeness.

Explicit alternative installation groups, structured verification exceptions, and expiring waivers
remain follow-ups. The existing universal `rpm-install` check remains
enabled, including its CPE checks and known issues, until the shared runner replaces that coverage.

### Shared caches and repository consistency

The universal runner and native package suites use `TMT_PLAN_DATA/rpmtest-cache` in CI. Local plans
share `rpmtest-cache/<guest>` under the tmt run directory. `RPMTEST_CACHE` identifies that run's cache;
the standalone universal command accepts `--cache` to share it explicitly.

Identical requests reuse verified root archives. Different requests also share downloaded RPMs and
repository metadata. Each repository is frozen at its first successful use in the run. DNF resolves
later requests using that cached metadata and downloads only missing RPMs. It never refreshes the
repository view to make a failed resolution succeed. New runs start with fresh repository metadata.

Downloads are serialized per builder and architecture. File locks, atomic state publication, and
digest checks protect concurrent users and reject corrupted entries. Completed downloads survive
transient retries; partial transaction directories are discarded. Unpublished files from interrupted
requests are removed before another request uses the cache.

Only the RPMs selected for each transaction enter its root. A cached compiler cannot enter a runtime
root merely because an earlier development test downloaded it. Containers, mutable filesystems, and
running services remain private to each test.

Each root manifest records repository metadata hashes and `resolution.reused_rpms` and
`resolution.downloaded_rpms`. `session.json` records complete-root cache hits. The cache is retained
with the run's artifacts; it is not shared between CI jobs. A cache miss can still fail when a remote
repository is unavailable or removes an RPM referenced by its frozen metadata. Publisher signature
verification remains a separate release requirement.

### Production rollout

Adoption comes from enabled fmf tests with
matching `component` metadata; there is no separate package registry. Adding a package suite
automatically enables its universal checks and requires a scenario tagged `functional`. Components
without package tests receive an explicit rpmtest skip while their legacy checks continue unchanged.
Native package tests honor the same policy. Run `make generate` when adding suites to update their
build triggers.

Set the tmt environment variable `RPMTEST_ROLLOUT=off` to disable new contracts and native package
tests during an incident. Artifact acquisition integrity checks remain active. No rollout file or
package list is required. The standalone universal command checks any supplied candidates when
`--component` and `IMAGE_NAME` are omitted; component runs require declared functional coverage.

Before broadening production enforcement:

1. Publish a GitLab runner image containing rsync and obtain a green MR check pipeline.
2. Run the actual Testing Farm plan on supported RHEL guests for both x86_64 and aarch64, using
   complete binary/source artifacts for every adopted component, including development and debug outputs.
3. Confirm native results for the legacy checks, installed contracts, and functional scenarios.
   Exercise missing artifacts, a broken functional fixture, timeout cleanup, and disabled rollout.
4. Compare cache metrics, duration, and disk use across suites. Check that manifests identify the
   same repository snapshot and that shared dependencies are not downloaded again.
5. Adopt further packages by adding and validating their fmf suites; review unsupported profiles
   and their expectations before enabling those suites.

CI prepares a private containers configuration with public DNS for test-container downloads unless
`CONTAINERS_CONF_OVERRIDE` is already set. It checks that the bootstrapped Python interpreter can
import rpmtest before publishing its path. Neither preparation step changes host-wide configuration.

Run the live regressions on a disposable Linux guest with Podman, rsync, and uv available:

```sh
RPMTEST_LIVE=1 uv run --project tools/tmt-rpmtest pytest \
  -c tools/tmt-rpmtest/pyproject.toml \
  tools/tmt-rpmtest/tests/integration/test_shared_cache.py \
  tools/tmt-rpmtest/tests/integration/test_ci_contracts.py
```

The shared CI regression builds a binary/source fixture, runs artifact preparation, Python bootstrap,
legacy checks, universal checks, and native assertions, and verifies cleanup. Its local provisioning
uses the installed Python interpreter and omits guest package installation; it does not replace the
RHEL and architecture validation above.

### Universal checks

Run centrally for every build, including packages without a functional suite. Enumerate all output
RPMs first; selecting only artifacts used by a functional plan is insufficient.

| ID | Scope | Required assertion |
| --- | --- | --- |
| U01 artifact integrity | Every binary RPM and SRPM | RPM headers and payload digests validate; record the artifact SHA-256 before transfer and verify it after transfer. |
| U02 build identity | Every binary RPM and SRPM | Name, epoch, version, release, architecture, and source identity match the build's declared outputs. Releases end in `.hum<N>`. The SRPM name matches the source package directory; binaries identify that SRPM. |
| U03 artifact selection | Every build and test environment | Every artifact has an explicit disposition. Select native/noarch candidates by RPM metadata. Reject ambiguous candidates and fail when an expected artifact is missing. |
| U04 clean installation | Every supported binary output | Install that candidate and its required dependency closure into an empty root. Disable weak dependencies and use the documented documentation policy. Scriptlet or dependency resolution failures fail the check. |
| U05 installed identity | Every installation | The installed inventory matches the resolved transaction, including candidate header and payload identities. A repository package with the same name or NEVRA cannot substitute for the candidate. |
| U06 installed contents | Every installed candidate, before functional mutation | Compare installed files with RPM metadata, including content, modes, ownership, and capabilities where recorded. Unexpected missing files or verification differences fail. Apply explicit rules for documentation omitted by policy, ghost files, and documented scriptlet changes. |
| U07 dependency linkage | Applicable ELF files in installed candidates | The declared interpreter exists and required shared objects resolve in the minimal runtime environment. Inspect with external tooling. Static files and non-ELF files are not applicable; unsupported loaders require a visible coverage decision. |
| U08 distribution identity | Applicable ELF files in installed candidates | ELF package notes that carry a CPE include `cpe:/a:redhat:hummingbird:1`. Record files without a CPE as not applicable to this rule. |

Install each output independently with its dependency closure by default. Declare separate valid
installation groups for conflicting alternatives; do not require mutually exclusive subpackages to
coexist. Development, documentation, debug, and metapackages still receive artifact checks and an
installation disposition. A package may need a different dependency repository or installation
profile; inability to resolve it must remain visible rather than silently removing it from coverage.

Debug-only ELF files receive installed-content verification; their stripped interpreter and dynamic
sections do not represent runnable files and are not runtime-linkage failures.

U06 uses RPM metadata as the source of expected files and attributes. Package-specific ownership
assertions, such as requiring `/usr/bin/curl` to belong to `curl`, belong in the package contract
below. Do not impose blanket rules that every package has an executable, all symlinks resolve in
every profile, or all installed files remain unchanged after application startup.

Cryptographic signature verification is a separate release gate against configured trusted keys.
Digest integrity and candidate identity do not authenticate the publisher. Unsigned build candidates
may receive these checks, but their results must not claim signature verification. The current
rpmtest assembler disables signature checks.

### Package-specific contracts and functional tests

Each adopted source package declares its important interfaces and at least one functional scenario
for each shipped product role. Subpackages may share scenarios when those scenarios actually select
and exercise them. Record outputs without functional coverage explicitly, including cases where
runtime behavior is not applicable.

Package-specific contract assertions describe deliberate promises that cannot be inferred from RPM
metadata: expected entry points, owners, users, configuration paths, permissions, capabilities,
required or forbidden dependencies, supported protocols, and public library interfaces. Keep these
expectations with the package. A shared helper executes the check; the package supplies the expected
values. Version output is a useful interface check, but alone is insufficient functional coverage.

| Product role | Minimum useful functional scenario |
| --- | --- |
| Command line tool | Process a deterministic fixture, assert its output or effect, and check a representative failure with the expected status. |
| Shared library | Run a small consumer that calls a public interface and checks the result. Verify headers and linking in a separate development environment when a development subpackage is shipped. |
| Service | Start with the shipped configuration as the intended service user, wait for bounded readiness, and complete a real client operation. Assert response or state and retain logs. |
| Language runtime or module | Execute or import representative code and check its result using the installed runtime and module. |
| Configuration, data, or trust package | Exercise a consumer against a supplied fixture and check acceptance, rejection, lookup, or effective configuration. |
| Metapackage | Assert the intended dependency selection; mark standalone runtime behavior as not applicable. Exercise the resulting product through a shared scenario where appropriate. |

Examples: hello must emit the expected greeting; curl must transfer and compare a fixture; libcurl
must support a compiled consumer; NATS must publish and receive a message. Installation, an existing
file, or a successful `--version` command cannot replace these scenarios. Upgrade compatibility,
ABI compatibility, persistence, and FIPS behavior need explicit suites when they are product
requirements; the universal baseline makes no claim about them.

Runtime scenarios request only the subject packages and their required dependencies. Keep compilers
and development headers in a separate development plan. Put harness clients in companions or on the
guest unless they are part of the product being tested. Subject commands use framework operations;
tests do not implement RPM installation, container lifecycle, retries, or evidence collection.

### Results and exceptions

Report universal checks and functional scenarios separately, with stable check IDs, package NEVRA,
architecture, installation profile, and links to manifests and logs. Grouping checks in a native
tmt test is acceptable only if its evidence identifies each check's outcome. Retain the full
dependency inventory, repository URLs, builder digest, and artifact hashes for each environment.
Repository URLs alone do not reproduce a transaction; preserve resolved artifacts or immutable
repository references when replay is required.

- **Pass:** the applicable assertion executed and succeeded.
- **Fail:** the package violated the assertion.
- **Error:** infrastructure, preparation, timeout, or cleanup prevented a reliable result.
- **Not applicable:** a documented rule excludes this artifact or profile from this check.
- **Waived failure:** a narrowly scoped known defect has an owner, issue, reason, and expiry or
  review date. Retain the failed observation and the waiver separately.
- **Missing coverage:** an applicable check or required scenario was absent or did not execute.

These are reporting requirements, not additional tmt result enum values. Preserve native results
and attach applicability, coverage, and waiver information as structured evidence. Excluded
architectures must be distinguishable from an unexpectedly empty artifact set. Preparation failures
must not become passes or not-applicable results. Do not retry failed assertions until they pass;
bounded infrastructure retries must retain diagnostics.

A required build gate succeeds only when every applicable required check and scenario passes or has
an active, permitted waiver. Errors, missing coverage, and expired waivers block the gate. An
unexpected pass requires waiver review and removal. Before a package adopts the functional gate,
show its missing functional coverage explicitly; do not label its universal-only result fully tested.

### Migration responsibilities

1. Extract the existing suffix, RPM integrity, SRPM identity, installation, and CPE checks into the
   central universal layer, preserving their coverage during the transition.
2. Add candidate identity, installed-content, ELF linkage, and artifact-disposition checks centrally.
   Use the framework's existing assembly and inspection primitives instead of package-local setup.
3. Keep universal rules in one implementation. Remove repeated baseline assertions from pilot
   scripts only after the central checks run against the same candidate outputs and profiles.
4. Keep package expectations and functional fixtures under `test/rpmtest/<package>/`, sharing
   version-independent scenarios where possible. Reuse verified roots while keeping sessions fresh.
5. Validate a good candidate, a deliberately broken candidate, a missing artifact, a package without
   functional tests, and interrupted cleanup through the actual CI path before making gates required.

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
Each test gets its own tmt result, including preparation failures. Legacy RPM checks and the new
installed contracts have separate results. A failure in any path fails the job.
There is no nested tmt invocation.

Add a package's plans and test metadata under the shared root; CI discovery needs no package list.
Each local plan must select package-qualified tests, such as `^/curl/tests/transfers$`.
Always filter local plans: a package subdirectory used as `--root` still resolves to the shared tree.
See [the authoring guide](../../test/rpmtest/AGENTS.md) for onboarding examples.

After adding a package's `tests/` directory, run `make generate` to update build triggers.
Package builds watch their own sources and package-specific tests. Shared plugin, fixture, and fmf
root changes run the framework checks in GitLab CI without rebuilding every adopted package.
CI plumbing changes retain the existing single `setup` canary. Run selected live suites against
published RPMs or retained build artifacts when broader integration validation is needed; a shared
framework change does not need fresh builds of those RPMs.
