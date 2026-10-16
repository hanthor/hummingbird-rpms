# Architecture

The plugin prepares RPM roots and cleans up runtime resources. tmt discovers and executes ordinary
guest-side tests.
The test scripts make their own assertions. There is no custom suite, step, or assertion language.

```text
tmt_rpmtest/
├── __init__.py               # Public test imports
├── containers.py             # Companion image and process declarations
├── plugins/
│   ├── prepare.py            # tmt prepare method and options
│   ├── finish.py             # tmt finish method
│   ├── guest.py              # tmt guest transfers and command dispatch
│   └── schemas/              # tmt plan validation
├── environment/
│   ├── rpm.py                # Root assembly and artifact verification
│   ├── lifecycle.py          # Guest preparation and ownership cleanup
│   └── __main__.py           # Guest-side command entry point
├── execution/
│   ├── subject.py            # Fresh-session lifecycle and test conveniences
│   ├── context.py            # Commands, snapshots, inspection, and evidence
│   └── readiness.py          # Bounded polling through recorded command execution
├── drivers/
│   ├── podman.py             # Builder transport and subject containers
│   ├── companions.py         # Private network and companion ownership
│   └── persistent.py         # Container processes and persistent subject sessions
└── primitives/
    ├── archive.py            # Paths and symlink resolution within an archived root
    ├── elf.py                # ELF program headers and dynamic loader selection
    ├── files.py              # Fixture staging and file observations
    └── packages.py           # RPM verification and metadata observations
```

Add primitive modules under `primitives/` and runtime adapters under `drivers/`.
The package root exports `subject` and `PrimitiveContext` for test authors.
`tmt.plugin` entry points load the prepare and finish modules directly.
`tmt.resources` locates their schemas within `plugins/`.

Tests mirror the source responsibilities under `tests/unit/`.
The live orchestration test lives in `tests/integration/test_tmt.py`.
The example fmf test remains under `tests/rpm/`.

The RPM and subject modules receive their runtime transport.
They contain no Podman flags. A future adapter must supply the same build and session operations.
We will add a formal driver interface when a second implementation makes its requirements concrete.

## Primitive interface

`Subject.use(factory)` passes its `PrimitiveContext` to a normal Python factory and returns the result.
There is no registration step, base class, or primitive dispatcher.
A primitive can live in a test tree or an independent Python library.
New primitive modules do not change the subject lifecycle, tmt methods, or runtime adapter.

The context owns evidence numbering, command execution, fixture transport, and temporary snapshots.
It also runs external tools against an offline snapshot in the builder.
`Files` and `Packages` depend on this interface, as custom primitives do.
A new primitive can compose either built-in without depending on `Subject` or Podman.

The subject invalidates its context before runtime cleanup.
A primitive that outlives its subject cannot start another command or inspection.
Snapshot scopes remove partially written archives on export or inspection failure.
Operation scopes record errors and propagate them to the test.
The context returns process results; primitives expose observations; tests make assertions.

## Preparation

The controller stages the package and candidate files through `guest.push()`.
Candidate digests are checked again after transport, before assembly.
`guest.execute()` runs the shared preparation helper on the provisioned guest.
Each guest has a separate root under `TMT_PLAN_DATA/rpmtest-<guest>/root/`.
The helper publishes `RPMTEST_DIRECTORY` and `RPMTEST_PYTHON` through the plan environment file.
Tests execute on that guest through tmt's standard executor.

Preparation uses a guest-local cache scoped to the run. Local plans share the run cache;
CI tests share their plan's cache. A request key covers package names, candidate content hashes,
ordered repositories, builder digest, architecture, and assembly scripts. A per-key file lock and
atomic rename publish only complete entries. Hits verify stored hashes and copy the archive and
manifest into a private environment. Ownership and runtime limits are created anew for every
environment, so cleanup cannot remove another test's resources. Repository contents are resolved
once per request within the run; the cache is not reused by new runs.

All candidate RPMs get digest checks and metadata records.
A local repository makes those exact artifacts available to the solver.
Remote repositories exclude candidate package names, so a selected package cannot silently use a
remote replacement.
Only the requested package roots and their dependency closure are downloaded and installed.
Unselected candidates remain visible in the manifest. They are not reported as runtime-tested.

Assembly starts from an empty root and has no network access.
The full installed inventory must match the selected NEVRA, header SHA-256, and payload SHA-256
identities.
Selected files must match candidate file hashes where applicable.
The RPM files are checked again after assembly. The final archive has its own digest.
These checks establish artifact identity within the run. They do not supply publisher authentication
or a repository snapshot.

## Subject sessions

A session imports the verified root and owns private containers and image checkpoints.
A new command checkpoints the previous container's filesystem, then starts a container with the
requested executable.
This preserves compile-and-run state without adding a persistent agent or shell to the root.
Processes do not persist between ordinary commands. Separate sessions always start from the prepared
archive.

`Subject.service()` scopes a persistent session adapter through the primitive context.
The adapter implements the same run, copy, snapshot, and runtime operations used by primitives.
The Podman session owns container creation and final removal; the persistent adapter owns exec,
process-state checks, shutdown, and logs. Readiness uses the context's normal command execution and
evidence. The context restores ordinary execution when the service block exits.
New execution modes can supply these session operations without changing package or file primitives.

Network scopes own one private network and optional companion image containers. The network is
internal by default; `internet=True` explicitly enables external routing for every member.
The same implementation handles subject-only internet scopes and companion scopes, retaining
filesystem changes and restoring offline execution on exit. Network evidence records the opt-in.
Companion scopes own their declared image containers. The Podman session selects
that network for subject commands and persistent services, then checkpoints and removes its attached
container before network cleanup. Process state, exec, readiness evidence, and shutdown are shared
through `ContainerProcess`; `PersistentSession` adds subject file operations to that implementation.
Scoped command handles reuse the context's evidence recording and reject execution after exit.
An `ExitStack` registers companion cleanup as resources are allocated, so partial startup still
removes earlier containers and the network. Ownership labels also let the finish step remove networks
after interrupted tests. Shared pulled images are not owned by a test.

Package contents and symlink checks use one archive per inspection. Paths resolve within that archive,
without following the host filesystem. ELF inspection reads program headers from the archive and
uses the subject's dynamic loader for dependency resolution. Assertions remain in Python tests.

Fixture archives enter the container through the transport. There are no subject host mounts.
Command-specific environment overrides do not persist through checkpoints.
Subject resource limits come from the plan.

RPM queries run in a separate builder against an exported snapshot.
The snapshot retains file ownership, modes, and extended attributes.
Tests can call standard RPM query and verification operations without installing RPM tools into the
subject.
The plugin does not interpret query results or command output as assertions.

Each session removes its owned resources on exit. Cleanup has an independent deadline.
The finish method also removes resources carrying the plan's unique ownership label.
It never selects shared builder images or another plan's resources.

## CI orchestration

Testing Farm runs one native plan in `ci/run_tests_rpm.fmf`.
One generic discovery entry reads the shared fmf root at `test/rpmtest/`.
Component filters select package tests during discovery. Package-local plans use qualified test
names and local runs select the desired package's plans explicitly. New packages require no central
CI registration. Shared fixtures are included in the fmf tree copy. The standard executor reports
each selected test directly, without invoking another tmt run. Existing RPM checks have their own
test result.

CI bootstraps the in-tree package with native shell preparation on the guest.
`execution.test` reads each selected test's canonical package plan, prepares a private root using
`environment.lifecycle`, runs the Python assertions, and cleans up in a `finally` block.
The local plugin prepares the same environment in advance and the helper reuses it.
No per-test environment is published to the plan, so package sets cannot leak between tests.
Preparation errors appear in the affected test's result. The finish step recovers all recorded owners
in the plan directory, including after interrupted execution.
