# Architecture

The plugin prepares RPM roots and cleans up runtime resources. tmt discovers and executes ordinary
host-side tests.
The test scripts make their own assertions. There is no custom suite, step, or assertion language.

```text
tmt_rpmtest/
├── __init__.py               # Public test imports
├── plugins/
│   ├── prepare.py            # tmt prepare method and options
│   ├── finish.py             # tmt finish method
│   └── schemas/              # tmt plan validation
├── environment/
│   └── rpm.py                # Root assembly and artifact verification
├── execution/
│   ├── subject.py            # Fresh-session lifecycle and test conveniences
│   └── context.py            # Commands, snapshots, inspection, and evidence
├── drivers/
│   └── podman.py             # Builder transport and subject containers
└── primitives/
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

The prepare method stores the root under `TMT_PLAN_DATA/rpmtest`.
It exposes the plugin's Python interpreter through tmt's plan environment file.
Tests use that interpreter and the standard tmt executor.

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
Processes do not persist between commands. Separate sessions always start from the prepared archive.

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
