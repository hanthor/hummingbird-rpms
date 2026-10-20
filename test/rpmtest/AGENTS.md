# RPM test authoring guide

This directory contains ordinary Python assertions run through tmt. The framework lives in
[`tools/tmt-rpmtest`](../../tools/tmt-rpmtest/README.md). See the
[operating guide](../../documentation/operating/testing-rpms-with-rpmtest.md) for runner setup.
Root repository instructions still apply. Keep package onboarding separate from framework redesign.

The [standard package contract](../../documentation/operating/testing-rpms-with-rpmtest.md#standard-package-contract)
defines the separation between universal checks and package-specific tests. Universal rules belong
in the central framework. The execution helper runs identity, file verification, and ELF linkage
checks before package assertions; do not repeat those checks in package scripts. Package tests
supply expected interfaces and useful functional scenarios. Preserve legacy coverage until its
replacement has been verified.

## Execution model and host effects

There are three places code can run:

- The tmt guest runs the Python test script and the framework's orchestration.
- A builder container resolves dependencies, installs RPMs into an empty root, and performs offline
  inspection. Installation uses `/newroot` inside that container.
- A subject container runs commands through `target.run()` against the prepared minimal root.

`provision: how: local` selects your machine as the tmt guest. It does not provide isolation itself.
The `prepare: how: rpmtest` plugin installs tested RPMs inside the builder container, rather than
into your host's RPM database. Python outside `target.run()` executes on the tmt guest and can change
that machine. A normal `prepare: how: install` installs packages on that guest, including your host
with local provisioning. The shared CI plan uses this for runner tools; package plans use `rpmtest`.

The builder runs privileged and mounts its work directory. Treat this as test infrastructure,
not a security sandbox for untrusted RPMs. Use an existing VM with `how: connect` when needed.
Do not change provisioning defaults as part of onboarding a package unless explicitly requested.

## Onboard a package

Use the exact directory name from `rpms/<package>/`. All packages share the fmf root at
`test/rpmtest/.fmf/version`. Do not add a nested `.fmf` directory for a package:

```text
<package>/
  plans/contract.fmf
  tests/contract/main.fmf
  tests/contract/contract.py
```

1. Create the package's `plans/` and `tests/` directories.
2. Add a canonical plan for each distinct package set or test purpose.
3. Add the test metadata and Python script.
4. Set test component metadata so generic CI discovery selects the package.
5. Run `make generate` to update build triggers, then review the generated diff.
6. Validate discovery and run the affected tests against local candidates.

These files are maintained by hand. `make generate` updates build resources and triggers;
it does not create plans or Python tests. There is no `suite.yaml` configuration. Enabled tests with
matching `component` metadata automatically adopt a component; no rollout
package list is maintained. Include a scenario tagged `functional`. The CI universal runner checks
every native/noarch candidate independently of these package tests. Validate the suite before
enabling it. `RPMTEST_ROLLOUT=off` disables the new CI tests during an incident.

For example, `jq/plans/contract.fmf` would contain:

```yaml
summary: Check the jq package contract
provision:
  how: local
discover:
  how: fmf
  test: ^/jq/tests/contract$
prepare:
  how: rpmtest
  packages:
  - jq
  repositories:
  - https://packages.redhat.com/api/pulp-content/public-hummingbird/$basearch/
execute:
  how: tmt
finish:
  how: rpmtest
```

Keep one `prepare` mapping with `how: rpmtest`. The CI helper reads that mapping directly.
List only the packages needed by this test. Required dependencies are resolved automatically;
weak dependencies and documentation are omitted. A compile test can have a separate plan with GCC
and development headers without adding those packages to runtime tests.

`jq/tests/contract/main.fmf` would contain:

```yaml
summary: Check jq installation and version output
test: '"$RPMTEST_PYTHON" -m tmt_rpmtest.execution.test --plan ../../plans/contract.fmf contract.py'
duration: 30m
component: jq-main
tag: contract
```

The command runs from the test directory. Relative script and plan paths must work there.
Use the execution helper: local plans reuse plugin preparation, while CI prepares a private root
for each selected test and cleans it up afterward. The duration must cover CI preparation as well
as the assertions.

`jq/tests/contract/contract.py` could contain:

```python
from tmt_rpmtest import subject

with subject() as target:
    assert target.packages.owners("/usr/bin/jq") == {"jq"}
    assert target.stat("/usr/bin/jq").mode & 0o111
    version = target.run(["/usr/bin/jq", "--version"])
    assert version.returncode == 0, version.stderr
    assert version.stdout.startswith("jq-"), version.stdout
```

Choose assertions that fit the package. A library need not have a CLI, and programs need not support
`--version` or `--help`. Add a separate `tag: functional` test that processes a representative fixture
and checks its result; a version check alone is insufficient. CI reports missing functional
coverage explicitly. Existing legacy checks still run through the unchanged legacy RPM-check path.
Do not remove legacy coverage until its replacement has been verified.

## CI discovery is automatic

The single fmf discovery entry in `ci/run_tests_rpm.fmf` reads the shared root:

```yaml
  - name: rpmtest
    how: fmf
    path: $TMT_TREE/test/rpmtest
    test: ^/[^/]+/tests/
    filter: component:${IMAGE_NAME}
```

Do not add a package entry to this file. Adding package test metadata is sufficient for discovery.
Set each test's `component` to its Konflux component name. Package names are lowercased and `.`, `_`,
and `+` become `-`, followed by `-main`. For example, `nats-server2.12` uses `nats-server2-12-main`.
Check the existing component resources when unsure.

Use package-qualified test names in local plans, such as `^/jq/tests/contract$`.
Keep component filtering in generic CI discovery. Do not add enabled CI plans per package or filter
unrelated tests only during execution; that produces irrelevant skipped results.
Extend the discovery cases in
[`test_ci_selection.py`](../../tools/tmt-rpmtest/tests/unit/execution/test_ci_selection.py) for the
new component and its expected tests.

Shared scripts and fixtures under `test/rpmtest/shared/` are copied with the shared fmf tree.
They need no `sync-repo` option. Keep test assets under this root; avoid copying the whole repository
for ordinary package tests. See the NATS plans and `shared/nats/` for examples.

A package's `tests/` directory enables build triggers for that package's own tests when
`make generate` runs. Shared framework and fixture changes run GitLab framework checks; they do not
rebuild every adopted package. CI plumbing retains the existing single `setup` canary. Validate
selected live suites against published RPMs or retained artifacts when needed. Commit the updated
`.tekton` resources with trigger changes. Never hand-edit generated Tekton resources.

## Use the framework

A new `subject()` scope starts from the prepared archive. Commands within a scope share filesystem
changes, but ordinary `target.run()` calls do not preserve running processes between commands.
Use `target.service()` for a persistent process.

Identical package sets reuse a verified prepared archive within one run on the same guest.
This happens automatically for local plans and the shared CI plan. Each environment still gets a
private archive copy and fresh container ownership; test writes and running services are not shared.
Candidate contents, repository URLs, builder digest, architecture, and assembly settings determine
reuse. Different package sets share verified RPM downloads as well. Each repository's metadata stays
fixed after its first successful use in the run, even when later tests request additional packages.
Start a new run to resolve published packages again. `session.json` records cache hits and misses.
Do not change package sets just to obtain a cache hit, or modify the cached files directly.
Read `resolution.reused_rpms` and `resolution.downloaded_rpms` in root manifests to inspect reuse.

Preparation retries recognized transient download failures with bounded exponential backoff.
Image pulls use Podman's native retries. Do not wrap installation or assertions in broad retry
loops; keep invalid packages, broken certificates, and functional failures visible.

| Operation | Usage |
| --- | --- |
| Run a program | `target.run(["/usr/bin/program", "argument"], timeout=60)` |
| Stage a fixture | `target.copy(Path("fixture.txt"), "/workspace/fixture.txt")` |
| Inspect a file | `target.stat("/usr/bin/program")` returns tar file metadata |
| Inspect RPM metadata | `target.packages.installed()`, `owners(path)`, `metadata(package, field)` |
| Verify installed files | `target.packages.verify(package)` returns RPM's status and differences |
| Inspect owned contents | `target.packages.contents(package, match=("/usr/bin/*",))` |
| Inspect symlinks | `target.packages.links(package)` returns resolution records |
| Inspect ELF dependencies | `target.packages.runtime_links(package)` returns loader results |
| Extend test operations | `target.use(MyChecks)` constructs a helper with the session context |

Check command return codes and include stdout or stderr in assertion messages. Nonzero statuses are
returned to the test, so expected failure behavior can be asserted. RPM queries and snapshots use
external tools; the subject does not need RPM, Python, or a shell for inspection.
Contents, symlinks, and dependency methods return observations. They do not assert correctness for
you. Documentation, ghost files, and optional integrations need package-specific expectations.

For persistent services, use a foreground command and a bounded readiness probe:

```python
with target.service(server_argv, ready=probe_argv, timeout=30, user="service-user"):
    response = target.run(client_argv)
    assert response.returncode == 0, response.stderr
```

Use the packaged service user when testing permissions. Probe service readiness rather than sleeping
for an assumed startup time. The framework stops the service, retains logs, and restores ordinary
subject execution when the scope exits.

Use `Companion` for external peers or client tooling. Images must be pinned by SHA-256 digest.
Enter the companion scope before starting a subject service:

```python
from tmt_rpmtest import Companion

with target.companions({"client": Companion(image=CLIENT_IMAGE, command=CLIENT_COMMAND)}) as peers:
    with target.service(server_argv, ready=probe_argv):
        response = peers["client"].run(client_argv)
        assert response.returncode == 0, response.stdout + response.stderr
```

Companions and the subject use a private internal network by default. Dictionary keys are DNS names;
the subject's DNS name is `subject`. Companion handles expire when the scope closes. Readiness
commands for companions execute inside their images. The NATS messaging test is a working example.

For deliberate public-network checks, use `with target.network(internet=True):` around the subject
commands. For companions, pass `internet=True` to `target.companions(...)`; this enables internet
access for every container in that scope. Both restore offline subject execution and remove their
network on exit. Do not nest network scopes or enter them after starting a subject service.
Include `ca-certificates` when checking public HTTPS trust, and bound requests with connection and
overall timeouts. Keep ordinary functional tests independent of public services.

## Run and validate

Run these commands from the repository root on a Linux runner with uv, Podman, and rsync available:

```sh
uv sync --python python3 --frozen --project tools/tmt-rpmtest

tools/tmt-rpmtest/.venv/bin/tmt --root test/rpmtest \
  --allow-unsafe-behavior=provision/local run --all --keep \
  plans --name '^/jq/plans/' \
  prepare --how rpmtest --artifact "$PWD/builds/jq/RPMS/*.rpm"
```

Always select package plans when running locally. Using a package subdirectory as `--root` does
not restrict discovery now that the packages share a root; an unfiltered run selects every plan.
Escape dots in plan regexes, for example `'^/nats-server2\.12/plans/'`.

Supply all native/noarch binary candidates needed from the build. Keep source RPMs out of this glob.
The plugin hashes and transfers local artifacts, including for SSH guests. An artifact that is
recorded but not selected for the requested environment is not runtime-tested. Do not claim coverage
for unselected candidates or debug packages.

For an existing VM with Podman, rsync, and Python 3.11 installed:

```sh
tools/tmt-rpmtest/.venv/bin/tmt --root test/rpmtest run --all --keep \
  plans --name '^/jq/plans/' \
  provision --how connect --guest "$VM_ADDRESS" --user root --key "$VM_KEY" \
  prepare --how rpmtest --python python3.11 --artifact "$PWD/builds/jq/RPMS/*.rpm"
```

Omit `--artifact` to use published packages when available. Keep `--keep` while developing so tmt
retains manifests, command output, readiness attempts, and service/container logs.
Use targeted linting and discovery regression tests for metadata changes:

```sh
tools/tmt-rpmtest/.venv/bin/tmt --root test/rpmtest plans lint '^/jq/plans/'
uv run --project tools/tmt-rpmtest pytest \
  tools/tmt-rpmtest/tests/unit/execution/test_ci_selection.py
```

Run the affected functional suite after assertions, package sets, or fixture behavior change.
Do not run `make check`; the user has explicitly requested targeted checks instead.

## Do's and don'ts

- Do keep assertions in ordinary Python and give each independent test its own tmt result.
- Do keep subject roots minimal. Put harness-only clients in companions or on the tmt guest.
- Do use argument lists and absolute executable paths for subject commands. A shell is available only
  if it is supplied by the subject's packages; `target.run()` does not add one.
- Do scope services, companions, and subjects with context managers so cleanup also runs on failure.
- Do share version-independent scripts and fixtures, as NATS does, while keeping package sets explicit.
- Do catch only expected exception types; preserve tracebacks for programming errors.
- Don't run subject commands with host `subprocess.run()` or install tested RPMs on the tmt guest.
- Don't start daemons in the background with `&`, manage Podman resources directly in package tests,
  or mount host directories into subjects. Use the scoped internet opt-in for deliberate public
  network checks.
- Don't assume a new session retains files or processes from an earlier session.
- Don't invoke another tmt run from a test. The local and CI paths use one orchestration layer.
- Don't bypass candidate identity checks, broadly suppress verification failures, or turn errors into
  passing results.
- Don't change framework behavior or provisioning defaults in response to a question about them.
