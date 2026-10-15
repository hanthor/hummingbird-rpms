# tmt-rpmtest

Prepare minimal RPM roots for ordinary tmt tests.

The Python plugin resolves packages, installs their exact artifacts into an empty root, and records
installed identities.
Tests run on the local runner through tmt's standard executor.
A subject session copies fixtures and executes commands inside the minimal root.
Assertions, test selection, timeouts, and reports remain normal tmt features.

## Where the fresh environment comes from

The plan's `prepare` block defines the subject filesystem through `packages`, `repositories`,
and optional local `artifacts`. Each plan starts with an empty root and installs its requested
packages plus required dependencies. The plugin saves this root as an archive for that plan.
The builder image supplies assembly tools outside the subject filesystem.

`provision: local` selects the runner where tmt executes Python test scripts and their assertions.
Inside a test, `with subject() as target:` creates a private session from the prepared archive.
`target.run(...)` executes the requested program inside that session's minimal container.
The Python assertions execute on the runner, outside the subject.

Commands within one session share filesystem changes. Each new `subject()` starts from the
prepared archive, without changes from earlier sessions. Processes do not persist between commands.
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
The subject has no network access, host mounts, or added capabilities.

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

The prepare plugin sets `RPMTEST_PYTHON` to its Python environment.
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

    verified = target.rpm("-V", "curl")
    assert verified.returncode == 0, verified.stdout + verified.stderr
```

`run()` returns a standard `subprocess.CompletedProcess` with text output.
Nonzero guest exit codes remain available to assertions. Runtime failures and command timeouts raise
exceptions.
Use `timeout=` and `environment=` for command-specific settings.
Environment overrides do not carry into later commands.

Commands in one session share filesystem changes. They do not share background processes.
Each session starts from the prepared root. Copy fixtures before compiling or running them.
`copy()` accepts a file or directory and an absolute destination inside the subject.
`stat()` returns a standard `tarfile.TarInfo` for file type and mode checks.
`rpm()` runs RPM queries or verification against a snapshot, using build tools outside the subject.

The host test controls its own environment. Subject commands receive only the standard PATH and
explicit overrides.
A shell command inside the subject requires a shell supplied by its RPM dependencies.

## Evidence and limits

Use `--keep` to retain the run directory.
`TMT_PLAN_DATA/rpmtest/manifest.json` records candidates, selected RPM hashes, installed identities,
and the root archive hash.
The same directory retains the selected RPM files and root archive.
Each session writes command output, exit codes, fixture archives, and a manifest copy under
`TMT_TEST_DATA`.
RPM inspection saves its output. Temporary inspection snapshots are removed after use.

Session cleanup removes its containers and imported images.
The finish plugin removes owned resources left by interrupted tests. Shared builder images remain
cached.
Cleanup failure fails the test or finish step.

Resolution uses the configured repositories online. Installation and execution run without
networking.
Weak dependencies and documentation are disabled. Normal dependencies can still supply Bash or other
utilities.
The plugin does not add test harness packages to the root.

Hashes identify artifact bytes and installed RPM identities. They do not authenticate publishers or
pin remote repository snapshots.
This version disables signature checks and supports the runner's native architecture.
It pins tmt 1.78.0 because it uses tmt's Python plugin interfaces.
Podman is the current runtime adapter.

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
RPMTEST_CURL_RPM=/absolute/path/to/curl.rpm uv run pytest tests/test_integration.py
```

See [architecture](docs/architecture.md).
