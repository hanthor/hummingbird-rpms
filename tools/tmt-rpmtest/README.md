# tmt-rpmtest

Prepare minimal RPM roots for ordinary tmt tests.

The [standard package contract](../../documentation/operating/testing-rpms-with-rpmtest.md#standard-package-contract)
separates universal checks from package-specific interfaces and functional behavior. CI runs the
installed contract for every native/noarch output of components with enabled fmf package tests.
Adoption follows `component` metadata automatically; no separate package list is required.
The execution helper also checks each package test's pristine environment before its assertions.
Contract observations and functional coverage declarations are retained separately from native
test results.

The Python plugin resolves packages, installs their exact artifacts into an empty root, and records
installed identities.
Tests run on tmt's provisioned guest through its standard executor.
A subject session copies fixtures and executes commands inside the minimal root.
Assertions, test selection, timeouts, and reports remain normal tmt features.

## Where the fresh environment comes from

The plan's `prepare` block defines the subject filesystem through `packages`, `repositories`,
and optional local `artifacts`. Each plan starts with an empty root and installs its requested
packages plus required dependencies. The plugin saves this root as an archive for that plan.
The builder image supplies assembly tools outside the subject filesystem.

tmt executes Python test scripts and assertions on the provisioned guest.
`provision: local` uses the current machine. `provision: connect` uses an existing SSH guest.
Inside a test, `with subject() as target:` creates a private session from the prepared archive.
`target.run(...)` executes the requested program inside that session's minimal container.
The Python assertions execute on the guest, outside the subject container.

Commands within one session share filesystem changes. Each new `subject()` starts from the
prepared archive, without changes from earlier sessions. Processes persist only inside an explicit
`target.service(...)` block.
The session removes its containers and image checkpoints when its context ends.

## Run

Install Podman, rsync, and uv on a Linux runner:

```sh
uv sync --locked
uv run tmt --allow-unsafe-behavior=provision/local run --all --keep \
  plans --name /plans/rpm
```

The example checks curl from the Hummingbird repository.
The builder requires a privileged container. RPM scriptlets execute during assembly.
By default, the subject has no network access. Companion scopes provide private internal networking.
The subject has no host mounts or added capabilities.

A plan declares the RPM environment:

```yaml
provision:
  how: local
discover:
  how: fmf
prepare:
  how: rpmtest
  packages: [curl]
  artifacts: [/absolute/path/to/build/*.rpm]
  repositories:
    - https://packages.redhat.com/api/pulp-content/public-hummingbird/$basearch/
  cpus: 2
  memory: 1073741824
  pids: 512
execute:
  how: tmt
finish:
  how: rpmtest
```

Omit `artifacts` to test published packages.
Artifact paths and glob patterns start at the plan's fmf tree root.
An unmatched pattern fails preparation. Supply one candidate version and architecture per package.
Repositories use HTTPS. DNF expands `$basearch` inside the builder.
The optional `builder` field selects another digest-pinned image with the same build tools.

Local candidates take priority over repository packages with the same name.
Only the requested packages and their dependency closure enter the root.
Every supplied candidate gets an RPM digest check and a metadata record.
The manifest marks whether each candidate was selected. An unselected candidate has no runtime
coverage claim.

For CI, add candidate RPMs without changing the plan:

```sh
uv run tmt --allow-unsafe-behavior=provision/local run --all --keep \
  prepare --how rpmtest --artifact '/absolute/path/to/build/*.rpm'
```

## Write tests

A normal fmf test invokes a Python script on the runner:

```yaml
summary: Check offline curl behavior
test: '"$RPMTEST_PYTHON" test.py'
duration: 2m
```

The prepare plugin stages this package and candidate RPMs through `guest.push()`.
It assembles the root through `guest.execute()` and sets `RPMTEST_PYTHON` to the guest interpreter.
The guest needs Podman, rsync, and Python 3.11 or later.
Set the prepare field `python: python3.11` when the guest's default Python is older.
The test script uses ordinary Python assertions:

```python
from pathlib import Path
from tmt_rpmtest import subject

with subject() as target:
    target.copy(Path("payload.txt"), "/workspace/payload.txt")
    result = target.run(["/usr/bin/curl", "--silent", "file:///workspace/payload.txt"])
    assert result.returncode == 0, result.stderr
    assert result.stdout == Path("payload.txt").read_text()

    missing = target.run(["/usr/bin/curl", "--fail", "--silent", "file:///missing"])
    assert missing.returncode == 37, missing

    assert "curl" in target.packages.installed()
    assert target.packages.owners("/usr/bin/curl") == {"curl"}
    assert "curl" in target.packages.metadata("curl", "provides")
    verified = target.packages.verify("curl")
    assert verified.returncode == 0, verified.stdout + verified.stderr
```

`run()` returns a standard `subprocess.CompletedProcess` with text output.
Nonzero guest exit codes remain available to assertions. Runtime failures and command timeouts raise
exceptions.
Use `timeout=` and `environment=` for command-specific settings.
Environment overrides do not carry into later commands.

Commands in one session share filesystem changes. By default, they do not share background processes.
Each session starts from the prepared root. Copy fixtures before compiling or running them.
`copy()` accepts a file or directory and an absolute destination inside the subject.
`stat()` returns a standard `tarfile.TarInfo` for file type and mode checks.
`rpm()` runs RPM queries or verification against a snapshot, using build tools outside the subject.

The guest-side test controls its own environment. Subject commands receive only the standard PATH and
explicit overrides.
A shell command inside the subject requires a shell supplied by its RPM dependencies.

## Persistent services

Use `target.service()` to start a foreground process and execute commands alongside it.
The plan must request the packages that supply the service and readiness command.
For example, a plan requesting `python3` and `curl` can run a local HTTP service:

```python
from pathlib import Path
from tmt_rpmtest import subject

with subject() as target:
    target.copy(Path("payload.txt"), "/workspace/payload.txt")
    url = "http://127.0.0.1:8000/payload.txt"
    with target.service(
        [
            "/usr/bin/python3",
            "-m",
            "http.server",
            "8000",
            "--bind",
            "127.0.0.1",
            "--directory",
            "/workspace",
        ],
        ready=["/usr/bin/curl", "--fail", "--silent", url],
        timeout=30,
    ):
        response = target.run(["/usr/bin/curl", "--fail", "--silent", url])
        assert response.returncode == 0, response.stderr
        assert response.stdout == Path("payload.txt").read_text()
```

The service is PID 1 in the subject container. It must stay in the foreground.
`user="postgres"` or a numeric `"UID:GID"` runs the service and its commands under that account.
The default is root. Ordinary commands after the service block use root again.
Readiness retries until the command succeeds or `timeout` seconds elapse.
An early service exit, OOM kill, or readiness timeout raises an exception.
Commands, fixture copies, and inspection primitives use the same running container.
Inspection snapshots of a running service are not atomic database backups.

The block stops the service on exit, including after an assertion or timeout.
A command timeout stops the entire service container so an abandoned exec process cannot continue.
Service output and container state remain in the session's numbered `service` evidence directory.
Filesystem changes remain available to later commands in the enclosing subject session.
`environment=` on `service()` sets the container environment; `run(environment=...)` overrides it for
that command only. Service blocks cannot be nested.

The subject has no external network by default, host mounts, or added capabilities.
Loopback connections work within the subject. Enter a companion scope before starting a subject
service to connect it to other containers. Privileged init systems such as systemd are not supported.

## Internet access

Subjects have no network by default. Opt in for the commands that need internet access:

```python
with subject() as target:
    with target.network(internet=True):
        response = target.run(
            [
                "/usr/bin/curl",
                "--fail",
                "--silent",
                "--show-error",
                "--connect-timeout",
                "5",
                "--max-time",
                "30",
                "https://github.com/",
            ]
        )
        assert response.returncode == 0, response.stderr
```

Include `ca-certificates` in the plan's package set when checking public TLS certificates.
The framework does not inject certificates, disable verification, or inherit guest proxy variables.
Internet access uses a dedicated Podman network and depends on the guest's available connectivity.
Leaving the scope retains filesystem changes, removes that network, and restores offline execution,
including after a failed assertion. `network.json` records whether internet access was enabled.
Enter network scopes before starting a subject service; network scopes cannot be nested.

## Companion containers

`target.companions()` starts declared containers on a private internal network. Each dictionary key
becomes a DNS name on that network. The RPM subject is named `subject`.

```python
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
        response = target.run(
            ["/usr/bin/curl", "--retry", "5", "--retry-connrefused", "--fail", "http://web/"]
        )
        assert response.returncode == 0, response.stderr
        assert "It works!" in response.stdout
```

Images must be pinned by SHA-256 digest. Pulling an image can use the network; running it has no
internet access by default. Names must be DNS labels in lowercase; `subject` is reserved.
Use `target.companions({"web": apache}, internet=True)` to enable internet access for the subject
and all companions in that scope while preserving their private DNS names. External access is
configured for the whole scope, rather than individual companions.
No host directories, image-declared volumes, or published host ports are attached.
Companions use Podman's default capabilities, with privilege escalation disabled; the RPM subject
continues to drop all capabilities. Each companion receives the plan's CPU, memory, and PID limits.
These are per-container limits, without an aggregate scheduler.

`Companion` accepts optional `command`, `ready`, `environment`, and `user` settings. Without `command`,
the image's entrypoint and command are used. With it, the first argument is the entrypoint.
Without `user`, the image's configured user is used.
Readiness commands run inside the companion, so their tools must be present in the image.
`timeout` bounds readiness polling for each companion. Without `ready`, only process liveness is
checked. An Apache configuration check does not establish HTTP readiness; the example retries curl
connections for that reason. All companions are started and checked before the block executes.
All companions start before readiness polling, so they can depend on one another during startup.

Ordinary subject commands and `target.service()` can run inside the scope. A subject service must
start after entering the companion scope. Companion scopes cannot be nested.
On exit, filesystem changes are retained, the subject returns to no networking, and companions and
their network are removed. Partial startup, failed assertions, and timeouts also trigger cleanup.
The scope yields a dictionary of command handles. For example,
`with target.companions({"client": spec}) as peers:` makes
`peers["client"].run(["/usr/bin/python3", "-c", client_code])` available while a subject service runs.
Commands retain stdout, stderr, and exit status in that companion's evidence directory.
Handles become invalid when the scope closes.

Each companion retains its declared image and command, readiness attempts, state, and logs beneath
the numbered `companions` evidence directory. Cleanup failures fail the scope. The finish step also
removes owned networks left by interrupted runs. Pulled images stay cached.

## Testing Farm

CI uses one plan in `ci/run_tests_rpm.fmf`. Native fmf discovery filters tests by the
`IMAGE_NAME` component before execution, so unrelated package tests do not produce skipped results.
The standard executor reports each selected test and the shared RPM checks directly.
One generic discovery entry reads the shared fmf root at `test/rpmtest/`. New packages need only
plans and component-tagged test metadata; they do not need an entry in the CI plan.
Local runs use that same root and select plans explicitly, for example
`plans --name '^/curl/plans/'`. Plans select package-qualified tests such as `^/curl/tests/transfers$`.
Shared fixtures under `test/rpmtest/shared/` are copied with this tree, without `sync-repo`.
See the [authoring guide](../../test/rpmtest/AGENTS.md).

Artifact download and architecture filtering happen in preparation through
`ci/prepare_rpm_artifacts.sh`. Preparation also installs this checkout's Python package in an
isolated guest environment. Each selected test calls `python -m tmt_rpmtest.execution.test` with
its canonical package plan and Python script. This helper assembles a private RPM root, runs the
assertions, and cleans up even after failures. Preparation failures belong to that test's result.
The finish step recovers any remaining owned resources after an interruption.

Canonical package settings remain in `test/rpmtest/<package>/plans/`.
Local plans use the installed `how: rpmtest` plugin to prepare their root, which the test helper
reuses. Both paths use the same assembly code on the guest's native architecture.
Testing Farm needs only tmt's built-in plugins on its controller. No helper invokes another tmt run.

## Prepared-root cache

Identical package requests reuse an assembled root within the same run on the same guest.
Local plans share a cache under the run directory's `rpmtest-cache/<guest>/`.
The shared CI plan uses `TMT_PLAN_DATA/rpmtest-cache/` across its selected tests.
The universal runner uses this same directory. `RPMTEST_CACHE` identifies the shared cache;
standalone universal runs can pass `--cache` explicitly.
No test definitions or extra configuration are needed.

The key includes the package set, candidate RPM contents, ordered repository URLs, builder digest,
native architecture, and assembly settings. A second cache shares downloaded RPMs across different
requests, so a runtime suite can reuse dependencies fetched by a development suite.
Repository metadata is frozen per repository on its first successful use in the run. Later solves
use DNF's `cacheonly=metadata`; only missing RPMs require the network. A new run resolves metadata
again. Repository IDs depend on their URLs, allowing the same repository to be reused across
different repository sets. Builder digests and architectures have separate download pools.
Only successful, verified roots are published. Cache hits verify the archive and manifest, then copy
them into a private environment with fresh ownership and runtime limits. Containers and filesystem
changes are never shared. `session.json` records the cache key and whether preparation was a hit.
Root manifests record repository metadata hashes and counts of reused and downloaded RPMs.
Only the current transaction's RPMs enter its root, regardless of other cached packages.
The download pool is locked during resolution and validates published metadata and RPM hashes.
Corrupted entries fail rather than triggering a silent refresh to newer repository contents.
The cache remains in the run directory with `--keep`; it is not a global cache.

## Download retries

RPM resolution and CI artifact downloads retry recognized transient network failures up to four
attempts, with delays of 2, 4, and 8 seconds. Diagnostics are retained for every attempt.
Unknown errors, authentication failures, missing packages or manifests, certificate failures, and
interruptions fail immediately. Offline installation and test assertions are not retried.
RPM resolution stays within the builder's ten-minute timeout; failed attempts discard partial
transactions while retaining completed RPM downloads. Artifact bootstrap requests and OCI manifest
fetches have a 60-second timeout, and each ORAS pull has a
120-second timeout with a ten-second termination grace period. Image pulls retain Podman's native
three retries and exponential backoff within the framework's five-minute pull timeout.

## Package inspection primitives

`target.packages` reads the current session's filesystem and RPM database through external tools.
These operations do not add RPM utilities to the subject or decide test assertions.

| Operation | Result |
| --- | --- |
| `installed()` | Set of installed package names |
| `verify(*names)` | RPM verification result, including exit status and differences |
| `owners(path)` | Set of package names that own the path |
| `metadata(package, field)` | Tuple of values for `provides`, `requires`, `files`, or `nevra` |
| `contents(package, match=("/**",))` | Owned paths with current `TarInfo`, or `None` for missing files |
| `links(package)` | Owned symlinks with target, resolved path, and resolution error |
| `runtime_links(package, timeout=60)` | Dynamic ELF paths, loaders, and dependency resolution results |

`verify(*names)` requires at least one package name.
A nonzero verification result remains available to the test's assertion.
Other query failures raise `subprocess.CalledProcessError`, including missing packages and unowned paths.
The evidence directory retains RPM output for each operation.
`provides` and `requires` return capability names without version constraints.
Use `target.rpm(...)` for full RPM query output or other metadata fields.

For richer package contracts, assert on observations:

```python
from pathlib import PurePosixPath

entries = target.packages.contents("curl", match=("/usr/bin/*",))
assert entries
assert all(entry.info and entry.info.isfile() and entry.info.mode & 0o111 for entry in entries)
assert all(PurePosixPath(entry.path).is_relative_to("/usr") for entry in entries)

links = target.packages.links("libcurl")
assert all(link.resolved is not None and link.error is None for link in links)
# An optional package policy can reject absolute targets.
assert all(not link.target.startswith("/") for link in links)

dependencies = target.packages.runtime_links("curl")
assert dependencies
assert all(check.resolved for check in dependencies), dependencies
```

Content patterns match the full absolute path. `*` and `?` do not cross `/`; `**` does.
Without a pattern, `contents()` returns all owned paths so tests can enforce allowed prefixes.
Missing files remain visible, including omitted documentation and ghost files; tests decide which
must exist. File metadata does not follow the final symlink.
Link resolution follows absolute and relative targets within the subject root, including symlinks
in parent directories. Dangling targets and link loops are reported as resolution errors.

Runtime dependency inspection skips non-ELF files, symlinks, and static ELF files. It reads ELF
program headers outside the subject and invokes the subject's dynamic loader with `--list` inside
the subject. No inspection packages are added. Missing loaders and libraries produce unresolved
results; malformed ELF files raise `ValueError`. Default loaders for shared libraries support
glibc on x86_64 and aarch64. This checks loader resolution, not `dlopen()` paths or application
behavior. Content and link observations, ELF inspection inputs, and command output remain in evidence.

`target.stat(path)` returns standard tar metadata for file assertions.
Use `isfile()`, `issym()`, `mode`, and `linkname` for type, permissions, and symlink checks.
A missing path raises `FileNotFoundError`.

## Add a primitive

A primitive is a Python class or factory that accepts `PrimitiveContext`.
Keep it beside your tests, in a shared library, or under the plugin's `primitives/` package.
Create it with `target.use(MyPrimitive)`. No registration or tmt changes are required.
Each call creates an instance bound to the current subject session.

For example, save this class in `checks.py` beside a test:

```python
from tmt_rpmtest import PrimitiveContext
from tmt_rpmtest.primitives import Files


class Binaries:
    def __init__(self, context: PrimitiveContext):
        self.files = Files(context)

    def executable(self, path: str) -> bool:
        info = self.files.stat(path)
        return info.isfile() and bool(info.mode & 0o111)
```

Use it from an ordinary test script:

```python
from checks import Binaries
from tmt_rpmtest import subject

with subject() as target:
    binaries = target.use(Binaries)
    assert binaries.executable("/usr/bin/hello")
```

Built-in `Files` and `Packages` use the same interface.
`target.files` and `target.packages` expose their instances.
Existing `target.copy()`, `target.stat()`, and `target.rpm()` calls remain available as conveniences.
Compose built-ins inside a new primitive, as the example does, or use the context directly:

| Context operation | Purpose |
| --- | --- |
| `run(argv, timeout=..., environment=...)` | Execute in the subject and retain command evidence |
| `snapshot(name="snapshot")` | Scope a temporary filesystem archive with automatic removal |
| `inspect(argv, name="inspect")` | Run offline builder tools against a snapshot at `/inspection` |
| `operation(name)` | Scope a numbered evidence directory and retain errors |
| `stage(archive)` | Copy a prepared fixture archive into the subject |

For host-side inspection, open the archive inside `with context.snapshot() as archive:`.
The archive is removed when that block ends, including on failure.
For external inspection, `context.inspect()` extracts the snapshot with ownership, modes, and xattrs.
Its tools come from the plan's builder image and cannot change the subject.
Commands and inspections return `subprocess.CompletedProcess`, including nonzero exit codes.
Inspection output and operation errors remain in the evidence directory.

Use `context.operation("my-check")` when a primitive needs to write its own evidence.
Operation names accept lowercase letters, digits, and hyphens.
A primitive must not retain the context beyond its subject block.
After that block, context operations raise `RuntimeError` before accessing the runtime.
Primitives leave assertions to the test and do not manage containers or signal handlers.

## Evidence and limits

Use `--keep` to retain the run directory.
`RPMTEST_DIRECTORY/manifest.json` records candidates, selected RPM hashes, installed identities,
and the root archive hash.
The plugin stores each guest's root under `TMT_PLAN_DATA/rpmtest-<guest>/root/`.
Native CI preparation stores each test's root under `TMT_PLAN_DATA/rpmtest/test-*/`.
An assembled environment retains selected RPM files; a cache hit retains the manifest and root
archive without duplicating the downloaded RPM files.
Each session writes command output, exit codes, fixture archives, and a manifest copy under
`TMT_TEST_DATA`.
RPM inspection saves its output. Temporary inspection snapshots are removed after use.

Session cleanup removes its containers, private networks, and imported images.
The finish plugin removes owned resources left by interrupted tests. Shared builder images remain
cached.
Cleanup failure fails the test or finish step.

Resolution uses the configured repositories online. Installation runs without networking.
Execution uses no network by default; companion scopes use a private internal network unless
`internet=True` is explicitly requested.
Weak dependencies and documentation are disabled. Normal dependencies can still supply Bash or other
utilities.
The plugin does not add test harness packages to the root.

Hashes identify artifact bytes and installed RPM identities. Repository metadata is frozen within
the run, but these hashes do not authenticate publishers or guarantee that upstream retains every
referenced RPM. Retained downloads and manifests supply evidence for that run.
This version disables signature checks and supports the runner's native architecture.
It pins tmt 1.78.0 because it uses tmt's Python plugin interfaces.
Podman is the current runtime adapter.

## Code layout

Source modules are grouped by responsibility:

- `plugins/` contains tmt prepare, finish, and their schemas.
- `environment/` assembles minimal RPM roots and verifies artifacts.
- `execution/` owns subject lifecycle and the primitive context.
- `drivers/` contains runtime adapters, currently Podman.
- `primitives/` contains file and package operations for tests.

Unit tests mirror these areas. Live tests live under `tests/integration/`.
See [architecture](docs/architecture.md) for the source tree and dependency relationships.

## Development

```sh
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run tmt plans lint
uv build
```

The optional live test covers fixtures, filesystem state, RPM queries, and exact candidate
selection:

```sh
RPMTEST_CURL_RPM=/absolute/path/to/curl.rpm uv run pytest tests/integration/test_tmt.py -k local
```

The optional SSH case uses a localhost SSH service with Podman, Python, and rsync available:

```sh
RPMTEST_CURL_RPM=/absolute/path/to/curl.rpm \
RPMTEST_SSH_PORT=2222 RPMTEST_SSH_KEY=/absolute/path/to/key \
uv run pytest tests/integration/test_tmt.py -k ssh
```

Live feature tests use published curl and Python RPMs to check ELF dependencies, a persistent HTTP
service, an Apache companion, readiness, logs, and cleanup after failures. From the repository root:

```sh
RPMTEST_LIVE=1 uv run --project tools/tmt-rpmtest pytest \
  -c tools/tmt-rpmtest/pyproject.toml tools/tmt-rpmtest/tests/integration/test_features.py
```

Add `RPMTEST_INTERNET_LIVE=1` to explicitly include public HTTPS certificate verification against
GitHub, internet-enabled companion networking, and restoration of offline execution.

The shared NATS suite can also be validated for both packaged versions:

```sh
RPMTEST_LIVE=1 uv run --project tools/tmt-rpmtest pytest \
  -c tools/tmt-rpmtest/pyproject.toml tools/tmt-rpmtest/tests/integration/test_nats.py
```

See [architecture](docs/architecture.md).
