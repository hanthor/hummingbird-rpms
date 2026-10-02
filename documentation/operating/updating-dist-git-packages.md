---
title: Updating Dist-git Packages
description: How packages are automatically updated from Fedora dist-git
weight: 40
aliases: [/l/updating-dist-git-packages]
---

Packages are automatically updated from Fedora dist-git. Each update creates a separate MR that is
automatically approved and merged when CI passes.

## Portable package filenames

Import, update, and sync escape Windows-invalid filename characters with `~` followed by their
hexadecimal character code. For example, `fix:one.patch` becomes `fix~3aone.patch`. Reserved device
names and trailing dots or spaces are escaped too. Safe filenames stay unchanged. If an escaped
name would collide with another filename, including on a case-insensitive filesystem,
sanitization fails before changing files.

Packaging specs, scripts, and configuration files use local names. Patch contents and source
archives stay unchanged. Download URLs keep their original locations. A tracked Source or Patch
declared by URL gets an RPM `#/` suffix for its local filename. The `sources` file keeps Fedora
lookaside names.

Merges normalize both the old and new Fedora snapshots before applying local changes.
The transformation is deterministic and idempotent, so no filename map is stored in metadata.
Modification checks and ordinary `diff` compare sanitized upstream trees. Use
`diff --raw` to see the filename substitutions themselves. Sanitization does not change Release,
version, or modification status.

To migrate existing packages:

```bash
make dist-git ARGS='--dry-run sanitize-paths --all'
make dist-git ARGS='sanitize-paths patch'
# Or migrate every package:
make dist-git ARGS='sanitize-paths --all'
```

This command leaves changes in the working tree without committing. It includes tracked files and
new files that Git does not ignore, and skips downloaded lookaside archives. Review and commit the
renamed files and updated references together. For a clean Fedora package,
include its existing `Upstream:` SHA trailer in the package commit to preserve history validation.

`make check` runs `dist_git.py check-paths` on tracked paths throughout the repository. Stage the
migration before checking, so Git's index contains the new names. The check also rejects Windows
reserved names and paths that differ only by case.

New clones can check out the corrected tree on Windows. Older commits still contain the original
filenames, so checking out those commits requires a filesystem that supports them.

## Testing Locally

```bash
# Dry-run (check all packages, no MRs)
./ci/dist_git_update_multi_mr.sh --clone

# Dry-run (check first 5 packages, no MRs)
./ci/dist_git_update_multi_mr.sh --clone --max-packages=5

# Check only a specific package (for testing/debugging)
./ci/dist_git_update_multi_mr.sh --clone --only-package=libgcrypt

# Check only clean packages (skip modified/independent)
./ci/dist_git_update_multi_mr.sh --clone --clean-only

# Check only modified packages (skip clean/independent)
./ci/dist_git_update_multi_mr.sh --clone --modified-only

# Create up to 3 test MRs (checks all packages, stops after finding 3 updates)
export CHORE_MR_GITLAB_TOKEN="glpat-xxxxxxxxxxxxxxxxxxxx"
./ci/dist_git_update_multi_mr.sh --clone --max-updates=3 --create-mrs

# Check first 10 packages, create up to 3 MRs
export CHORE_MR_GITLAB_TOKEN="glpat-xxxxxxxxxxxxxxxxxxxx"
./ci/dist_git_update_multi_mr.sh --clone --max-packages=10 --max-updates=3 --create-mrs

# Create MRs only for clean packages (skip modified/independent)
export CHORE_MR_GITLAB_TOKEN="glpat-xxxxxxxxxxxxxxxxxxxx"
./ci/dist_git_update_multi_mr.sh --clone --clean-only --create-mrs
```

## Environment Variables

- `CHORE_MR_GITLAB_TOKEN` - GitLab token with `api` and `write_repository` scopes (required for
  `--create-mrs` and resolved diagnostic-MR cleanup)
- `CHORE_MR_APPROVAL_GITLAB_TOKEN` - GitLab token with `api` scope for auto-approving MRs (used by
  CI)
- `GITLAB_REMOTE_URL` - Target repo (default: <https://gitlab.com/redhat/hummingbird/rpms.git>)

## Flags

- `--clone` - Clone from GitLab to /tmp (safe for local testing, uses latest main branch)
- `--max-packages=N` - Check only the first N packages (limits input set)
- `--max-updates=N` - Stop after finding N updates (limits output MRs created)
- `--create-mrs` - Actually create MRs (requires token)
- `--only-package=NAME` - Check only the specified package (for testing/debugging specific packages)
- `--clean-only` - Skip packages with `modification_status` of 'modified' or 'independent', only process
  clean packages
- `--modified-only` - Skip packages with `modification_status` of 'clean' or 'independent', only process
  modified packages (mutually exclusive with `--clean-only`)

### Using --clean-only

The `--clean-only` flag filters out packages marked as 'modified' or 'independent' before attempting
updates. This is useful for:

1. **Better failure detection** - Exit code 1 indicates real update failures, not expected errors
   from modified/independent packages
2. **Cleaner output** - No error messages for packages that can't be auto-updated by design
3. **Efficient CI** - Focus on packages that should update automatically
4. **Performance** - Avoids invoking `dist_git.py` for packages that will fail

Without `--clean-only`, the script attempts to update all packages. Modified/independent packages fail
with:

```text
ERROR: Cannot auto-update <package>
       Status: modified/independent
       Reason: <reason>
       Use 'sync' to force update or 'mark-modified --clean' to allow updates
```

These expected failures can mask genuine update issues. Using `--clean-only` prevents these false
failures.

### Using --modified-only

The `--modified-only` flag filters to only process packages marked as 'modified', skipping clean and
independent packages. This is useful for checking the merge logic, as well as getting an overview of
current merge conflicts.

## Auto-Merge and Auto-Approval

MRs created by this script are configured to:

- **Auto-merge** when pipeline succeeds (set via `merge_request.merge_when_pipeline_succeeds`)
- **Auto-approve** after 10 minutes via the `chore_mr_approval` CI job (gives Konflux time to post
  commit statuses)

## Version-Constrained Updates

Packages with `track_upstream` set to a version prefix in their metadata are version-constrained.
This is used for versioned packages like `golang1.26` that track a specific upstream version line.

### How It Works

The `track_upstream` and `release_monitoring_project_id` metadata fields work together across two
systems:

**`dist_git.py update`** (dist-git sync from Fedora):

When `track_upstream` is a version prefix (e.g., `"1.26"`), the update command uses prefix matching:

- `1.26` matches `1.26`, `1.26.0`, `1.26.3` (allowed)
- `1.26` does **not** match `1.27.0`, `2.0` (skipped)

Skipped packages log a warning:

```text
WARNING: Skipping golang1.26: upstream version 1.27.0 doesn't match tracked version 1.26
```

**`check_upstream_versions.py`** (release-monitoring.org checks):

When `release_monitoring_project_id` is a string, Anitya is queried using that name instead of the
RPM package name. For example, `golang1.26` with `release_monitoring_project_id: "golang"` queries
Anitya for `golang`. When it is an integer, the v2 API is queried directly by project ID. When
`track_upstream` is a version prefix, the list of upstream versions returned by Anitya is filtered
to only those matching the prefix. This means `check_upstream_versions.py check` will report
`1.26.5` as the latest version for `golang1.26` even if Anitya reports `1.27.0` as the latest
`golang` release.

When `--update` is used, the script updates the spec file, downloads new sources, and commits the
result. Packages that need custom update logic can provide a hooks file at
`metadata/<package>.update-hooks.yaml` to override the spec update, source download, or add a
post-update step. See [Package Modification Tracking](../package-modification-tracking) for details.

### Setting Up Version Constraints

```bash
# Constrain golang1.26 to only receive 1.26.x updates
./ci/dist_git.py set-upstream golang1.26 --project-id golang --track-version 1.26

# Remove the version constraint
./ci/dist_git.py set-upstream golang1.26 --no-track-version
```

### Behavior

- `dist_git.py`: Version constraint is checked before pre-release and Koji build checks
- `dist_git.py`: The `sync` command bypasses the version constraint (explicit force operation)
- `check_upstream_versions.py`: `release_monitoring_project_id` determines the Anitya lookup (int
  for project ID, string for name, absent for RPM name); `track_upstream` filters versions when set
  to a prefix
- Batch operations continue processing other packages after skipping constrained ones

## Pre-Release Version Filtering

By default, the update mechanism skips pre-release versions to prevent unstable packages from
entering the repository automatically. Pre-release patterns include:

- **Tilde notation**: `5.3.0~rc1`, `2.0~beta1`, `1.0~alpha` (RPM standard)
- **Suffix notation**: `5.3.0-rc1`, `2.0.beta1`, `3.0-alpha`, `1.0.dev`
- **Development markers**: `1.5git20240101`, `2024.01.snapshot`, `1.0dev`

### Manual Override

To explicitly update to a pre-release version:

```bash
# Update single package to pre-release version
./ci/dist_git.py update --allow-prerelease package-name --skip-build-check

# Batch update allowing pre-releases
./ci/dist_git.py update --allow-prerelease --skip-build-check
```

### Pre-release filtering behavior

- Pre-release detection occurs before Koji build checks (saves API calls)
- Skipped packages log a warning with the detected pattern
- Batch updates continue processing other packages
- The `sync` command bypasses this check (explicit force operation)
