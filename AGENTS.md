# RPMs Repository Guidelines

## `ci/dist_git.py` — The Package Management Tool

**All package operations must go through `ci/dist_git.py`.** Do not manually edit spec
files, metadata JSON, or Konflux/Tekton resources to perform operations that the script
handles. Some commands commit their changes. `mark-modified` only updates metadata, so include
that metadata file in the package commit that needs it.

Use the repository container target because the script needs Python dependencies that are not
available on all hosts:

```bash
make dist-git ARGS='<command and arguments>'
```

Use `limactl shell fedora` only when the Makefile container is unavailable.

| Command | When to use |
| ------- | ----------- |
| `./ci/dist_git.py import fedora/<pkg>` | Import a new package from Fedora rawhide (default) or another branch (`--branch f42`) |
| `./ci/dist_git.py update [<pkg>]` | Pull latest Fedora changes into one or all packages (merges local modifications) |
| `./ci/dist_git.py sync <pkg>` | Force-sync a package to upstream, discarding local changes |
| `./ci/dist_git.py rebuild <pkg> --reason "..."` | Bump Release to trigger a no-change rebuild (use `--all` for every package, plus `--exclude pkg1,pkg2` to skip specific ones) |
| `./ci/dist_git.py rebuild-rev-deps <pkg> --reason "..."` | Rebuild all reverse-dependencies of a package |
| `./ci/dist_git.py rename <pkg>` | Rename a package (reads new name from spec) |
| `./ci/dist_git.py mark-modified <pkg> --modified --reason "..."` | Mark a package as locally modified so it isn't auto-updated |
| `./ci/dist_git.py mark-modified <pkg> --clean` | Clear the modified flag to allow auto-updates again |
| `./ci/dist_git.py set-upstream <pkg> --track-version <prefix>` | Pin a versioned package to a version line (e.g., `1.26`) |
| `./ci/dist_git.py list [--clean\|--modified\|--independent\|--prerelease]` | List packages by status |
| `./ci/dist_git.py diff [<pkg>…\|--all]` | Show what changed locally vs Fedora upstream |
| `./ci/dist_git.py sanitize-paths <pkg> (or --all)` | Make package filenames Windows-safe, leaving changes for review, use either `<pkg>` or `--all` |
| `./ci/dist_git.py check-paths` | Reject tracked filenames incompatible with Windows |
| `./ci/dist_git.py ls-sources <pkg>` | Inspect source archives for a package |
| `./ci/dist_git.py update-releases` | Refresh `upstream-releases.json` from Bodhi |
| `./ci/dist_git.py add-private-product <product> --packages pkg1,pkg2` | Create a private product: Pulp repos, RPA config, and package assignments |
| `python -m tmt_rpmtest.execution.universal --artifacts <dir> --directory <results>` | Run installed-package contracts for all native/noarch candidates using the rpmtest Python environment |
| `python -m tmt_rpmtest.environment.artifacts --source <dir> --binaries <dir> --architecture <arch> --output <catalog>` | Record source, selected, and foreign RPM outputs before tests; optionally compare with `--oci-manifest` |
| `RPMTEST_LIVE=1 uv run --project tools/tmt-rpmtest pytest -c tools/tmt-rpmtest/pyproject.toml tools/tmt-rpmtest/tests/integration/test_ci_contracts.py tools/tmt-rpmtest/tests/integration/test_shared_cache.py` | Validate the shared CI path, failure handling, cleanup, and shared RPM downloads |
| `tools/tmt-rpmtest/.venv/bin/tmt --root test/rpmtest --allow-unsafe-behavior=provision/local run --all plans --name '^/<pkg>/plans/'` | Test hello, curl, or NATS through the RPM plugin |
| `python -m tmt_rpmtest.execution.test --plan <plan.fmf> <test.py>` | Prepare, execute, and clean up one component-selected CI test |
| `RPMTEST_LIVE=1 uv run --project tools/tmt-rpmtest pytest -c tools/tmt-rpmtest/pyproject.toml tools/tmt-rpmtest/tests/integration/test_features.py` | Validate RPM inspection, persistent services, and companion containers with published RPMs |

The `--dry-run` flag is available on most commands to preview without committing.

**Keeping this file in sync:** Whenever a new `dist_git.py` subcommand is added, or a new
operational task is introduced (new script, new workflow, new hook type), update both the
command table above and the Operations Index below in the same commit. This file is the
authoritative quick-reference for agents and should always reflect the current state of the
tooling.

## Operations Index

Common operational tasks that users or AI agents may need to perform:

| Operation                      | `dist_git.py` command                                           | Documentation                                                                                   | When to use                                                            |
|--------------------------------|-----------------------------------------------------------------|-------------------------------------------------------------------------------------------------|------------------------------------------------------------------------|
| Import dist-git package        | `import fedora/<pkg>`                                           | [Updating Dist-git Packages](documentation/operating/updating-dist-git-packages.md)             | Add a new package from Fedora dist-git                                 |
| Add independent package        | _(manual — not from Fedora)_                                    | [Adding Independent Packages](documentation/operating/adding-independent-packages.md)           | Add new package not imported from Fedora                               |
| Rebuild package (no-change)    | `rebuild <pkg> --reason "..."` (or `--all --exclude pkg1,pkg2`) | [Rebuilding Packages](documentation/operating/rebuilding-packages.md)                           | Faulty RPM published; need to bump Release for rebuild                 |
| Backport a patch               | Add patch, bump Release, run `mark-modified`                    | [Rebuilding Packages](documentation/operating/rebuilding-packages.md)                           | Fast-track an upstream fix not yet in Fedora                           |
| Configure metadata fields      | _(edit `metadata/<pkg>.json`)_                                  | [Package Metadata Fields](documentation/operating/package-metadata-fields.md)                   | Understand `release` and `modification_status` configuration           |
| Debug build failures           | _(see docs)_                                                    | [Debugging Build Failures](documentation/operating/debugging-build-failures.md)                 | Investigate and fix failed package builds                              |
| Test packages through tmt      | _(see docs)_                                                    | [Testing RPMs with rpmtest](documentation/operating/testing-rpms-with-rpmtest.md)               | Run normal tmt assertions against minimal RPM roots                    |
| Run a selected RPM CI test     | `python -m tmt_rpmtest.execution.test --plan <plan> <script>`   | [RPM testing plugin](tools/tmt-rpmtest/README.md)                                               | Execute one native tmt result with its own minimal RPM root            |
| Validate RPM test features     | _(see plugin README)_                                           | [RPM testing plugin](tools/tmt-rpmtest/README.md)                                               | Check RPM contracts, services, and companions with local Podman        |
| Mark package modified          | `mark-modified <pkg> --modified --reason "..."`                 | [Package Modification Tracking](documentation/operating/package-modification-tracking.md)       | Track local changes; prevent automatic Fedora updates                  |
| Set package basename           | `set-upstream <pkg> --track-version <prefix>`                   | [Package Modification Tracking](documentation/operating/package-modification-tracking.md)       | Set basename/track_version for versioned packages                      |
| Track upstream versions        | `set-upstream <pkg>`                                            | [Package Modification Tracking](documentation/operating/package-modification-tracking.md)       | Enable/disable upstream version checking for a package                 |
| List upstream version status   | `list`                                                          | [Package Modification Tracking](documentation/operating/package-modification-tracking.md)       | See all packages with upstream version and tracking info               |
| Sanitize package filenames     | `sanitize-paths <pkg>` or `sanitize-paths --all`                | [Updating Dist-git Packages](documentation/operating/updating-dist-git-packages.md)             | Migrate existing paths without changing package status                 |
| Check repository filenames     | `check-paths`                                                   | [Updating Dist-git Packages](documentation/operating/updating-dist-git-packages.md)             | Validate Windows checkout compatibility, also run by `make check`      |
| View package differences       | `diff <pkg>`                                                    | [Package Modification Tracking](documentation/operating/package-modification-tracking.md)       | See what changed in modified packages vs Fedora                        |
| Add per-package update hooks   | _(edit metadata hooks YAML)_                                    | [Package Modification Tracking](documentation/operating/package-modification-tracking.md)       | Customize spec update, source download, or post-update steps           |
| Update dist-git packages       | `update [<pkg>]`                                                | [Updating Dist-git Packages](documentation/operating/updating-dist-git-packages.md)             | Test or trigger automated package updates from Fedora                  |
| Lookaside cache access         | _(AWS credentials setup)_                                       | [Lookaside Cache Access](documentation/operating/lookaside-cache-access.md)                     | Set up AWS credentials to upload source tarballs                       |
| Pulp access                    | _(service account setup)_                                       | [Pulp Access](documentation/operating/pulp-access.md)                                           | Create Pulp service accounts and configure CLI credentials             |
| Analyze upstream diffs         | `diff --all --stat`                                             | [Upstream Diff Analysis](documentation/operating/upstream-diff-analysis.md)                     | Classify modified packages for upstreaming                             |
| Test package contracts         | `tmt_rpmtest.execution.universal`                               | [Testing RPMs](documentation/operating/testing-rpms-with-rpmtest.md)                            | Validate outputs independently of functional suites                    |
| Capture build outputs          | `tmt_rpmtest.environment.artifacts`                             | [Testing RPMs](documentation/operating/testing-rpms-with-rpmtest.md)                            | Record and verify complete artifact acquisition before tests           |
| Validate RPM production path   | `RPMTEST_LIVE=1` integration tests                              | [Testing RPMs](documentation/operating/testing-rpms-with-rpmtest.md#production-rollout)         | Exercise CI bootstrap, failures, cleanup, and offline download reuse   |
| Report CVE data issues         | _(see docs)_                                                    | [Reporting CVE Data Issues](documentation/operating/reporting-cve-data-issues.md)               | Get incorrect CVE data on cve.org or NIST corrected                    |
| Konflux resource deployment    | _(background info)_                                             | [Konflux Resource Deployment](documentation/background/konflux-resource-deployment.md)          | Understand how Konflux resources are deployed                          |
| Private RPM repositories       | _(package-overrides + RPA config)_                              | [Private RPM Repositories](documentation/operating/private-rpm-repositories.md)                 | Route packages to private per-product Pulp repos                       |
| Add private product            | `add-private-product <product> --packages ...`                  | [Private RPM Repositories](documentation/operating/private-rpm-repositories.md)                 | Create a private product with Pulp repos, RPA, and packages            |
| Rebase buildroot to new Fedora | _(multi-step; see runbook)_                                     | [Rebasing Buildroot to New Fedora](documentation/operating/rebasing-buildroot-to-new-fedora.md) | Move mock.cfg, core toolchain, and package set to a new Fedora release |
| Add/rotate GPG source keys     | _(edit `metadata/gpg-keys/` + pipeline YAML)_                   | [GPG Source Verification](documentation/operating/gpg-source-verification.md)                   | Add, rotate, or revoke an upstream signing key for source verification |

## Post-Import / Post-Add Requirements

After importing a new SRPM (`dist_git.py import`) or adding an independent package, the `upstream_repo`
field **must** be populated in `metadata/<package>.json` before CI will pass. The `dist_git.py
import` command does not set this field automatically.

To populate it:

1. Use the `/generate-package-map` skill (`.agents/skills/generate-package-map/SKILL.md`),
   which searches for the canonical upstream git repository and handles mirror canonicalization,
   known overrides, and versioned-package branches.
2. Or manually add the field to `metadata/<package>.json`:
   `"upstream_repo": "https://github.com/example/project"`
3. If no upstream git repository exists, use the Fedora DistGit URL as a fallback:
   `"upstream_repo": "https://src.fedoraproject.org/rpms/<package>"`

CI enforces this via `test/test_package_map.py::test_upstream_repo_exists`.

## Development Guidelines

### Testing Requirements

When modifying `ci/dist_git.py`:

- **Add tests** for new commands or functionality in `test/test_dist_git.py`
- Run `make check` to verify all tests pass before committing
- Test coverage is tracked - aim to test all user-facing behavior
- Examples: See existing tests like `test_import`, `test_update`, `test_mark_modified_with_reason`

When modifying validation or CI scripts:

- Ensure `make check` passes (includes linting, type checking, and validation)
- Add tests if the change affects user-facing behavior

### Code Style

**Exception Handling:**

- **Never use bare `except Exception:` handlers** - they hide bugs like `AttributeError`,
  `TypeError`, and other programming errors
- Only catch specific exception types that you expect and know how to handle (e.g.,
  `json.JSONDecodeError`, `OSError`, `subprocess.CalledProcessError`)
- Let unexpected errors crash with a traceback - this helps identify bugs during development and
  prevents silent failures in production
- If you need to catch an exception for cleanup or logging, re-raise it afterward so the error isn't
  silently ignored

Bad:

```python
try:
    process_package(pkg)
except Exception:
    logger.warning(f"Error processing {pkg}")
    # Silently continues, hiding the real error!
```

Good:

```python
# Let it crash - we want to see AttributeError, TypeError, etc.
process_package(pkg)

# Or catch only specific expected errors:
try:
    with open(metadata_file) as f:
        data = json.load(f)
except (json.JSONDecodeError, OSError) as e:
    return False, f"Failed to load metadata: {e}"
```
