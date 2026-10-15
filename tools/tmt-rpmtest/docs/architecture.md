# Architecture

The plugin prepares RPM roots and cleans up runtime resources. tmt discovers and executes ordinary
host-side tests.
The test scripts make their own assertions. There is no custom suite, step, or assertion language.

| Module | Responsibility |
| --- | --- |
| `plugin.py` | tmt prepare and finish methods |
| `rpm.py` | Candidate selection, empty-root assembly, and installed identity verification |
| `subject.py` | Fixture staging, execution evidence, and external RPM inspection |
| `podman.py` | Builder transport, subject filesystem checkpoints, and owned-resource cleanup |

The RPM and subject modules receive their runtime transport.
They contain no Podman flags. A future adapter must supply the same build and session operations.
We will add a formal driver interface when a second implementation makes its requirements concrete.

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
