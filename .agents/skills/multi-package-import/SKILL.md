---
name: multi-package-import
description: >-
  Import a batch of Fedora dist-git packages into the Hummingbird repo,
  build-verify each locally in dependency order, populate upstream_repo, run
  make check, and open a single MR. Use when onboarding multiple packages from
  a ticket/inventory, migrating a package group to hum1 builds, or when the
  user says /multi-package-import.
---

# Multi-Package Import

Onboard a batch of Fedora-sourced packages (e.g. a migration group from a
Jira ticket or an inventory list) into Hummingbird as `hum1` builds. This
skill is for **Fedora `dist_git.py import` only** — it does not cover adding
native (non-Fedora) packages; see
[Adding Native Packages](../../../documentation/operating/adding-native-packages.md)
for that.

The workflow imports each package, builds it locally as a gate before moving
on, batches the required `upstream_repo` metadata fix-up at the end, runs the
full repo validation once, and lands everything in a single MR.

## Prerequisites / environment

- Run `dist_git.py` and `build_rpms.sh` inside `limactl shell fedora` (or an
  equivalent container) — they need `specfile`, `packaging`, `jinja2`, and
  `mock`.
- **Python interpreter gotcha:** if `which python3` resolves to a non-system
  interpreter (e.g. a linuxbrew shim) that lacks `specfile`, invoke the tool
  explicitly as `/usr/bin/python3 ci/dist_git.py ...`. Verify first with:

  ```bash
  /usr/bin/python3 -c "import specfile, packaging, jinja2; print('deps OK')"
  ```

- `build_rpms.sh` requires `setfacl` in `$PATH`. If missing:

  ```bash
  sudo dnf -y install acl
  ```

- Builds run mock inside podman and can take minutes (NetworkManager-sized
  packages can take 5+ minutes). Launch each build fully detached so a
  shell/tool timeout can't kill it mid-build, then poll:

  ```bash
  setsid nohup ./ci/build_rpms.sh <pkg> > /tmp/build-<pkg>.log 2>&1 < /dev/null &
  disown
  # poll periodically:
  pgrep -af "build_rpms.sh <pkg>"
  tail -n 40 /tmp/build-<pkg>.log
  ```

## Inputs

Determine the package list from whatever the user provides: a Jira ticket
(inventory table), an explicit list of package names, or "packages in group
X". If the user hasn't specified a source branch, default to Fedora
`rawhide`. If invoked with no package list at all, ask the user for it.

## Step 1: Determine order and source branch

1. For each package in the list, check whether `metadata/<pkg>.json` already
   exists. Only packages missing it are new imports; flag any that already
   exist for the user (they need `update`/`sync`, not `import`).
2. Order the import/build loop by build-dependency layer (foundational
   libraries first, then packages that link against them, then top-level
   consumers). Use your judgment on the dependency graph — for
   networking/netfilter-style stacks this typically looks like:
   base libs → mid libs → CLI tools → top-level daemons/managers.
3. Decide the source branch:
   - **Default:** `./ci/dist_git.py import fedora/<pkg>` tracks `rawhide`.
     This matches the convention most existing packages use and is the
     right choice unless there's a reason to pin.
   - **Pin to a specific Fedora release:** `--branch fNN` when the ticket or
     user explicitly requires tracking a numbered release line. Note the
     tradeoff: metadata `branch` then stays pinned to `fNN` and won't follow
     rawhide forward. Before deciding, check whether `rawhide` and the
     target `fNN` currently point at the same commit — if so, importing
     from `rawhide` gets identical content while keeping the package on the
     repo's common tracking convention:

     ```bash
     git ls-remote --heads https://src.fedoraproject.org/rpms/<pkg>.git fNN rawhide
     ```

   Surface this choice to the user if it isn't already dictated by the
   ticket.

## Step 2: Per-package import + build loop

For each package, **in the dependency order from Step 1**:

1. **Import:**

   ```bash
   /usr/bin/python3 ci/dist_git.py import fedora/<pkg>
   # or, to pin a branch:
   /usr/bin/python3 ci/dist_git.py import fedora/<pkg> --branch fNN
   ```

   This clones the branch, parses version/release (resolving
   `%autorelease` via MDAPI if present), writes
   `metadata/<pkg>.json` (`modification_status: clean`), looks up the
   Anitya (release-monitoring.org) project ID, regenerates
   `konflux-templates/` and `.tekton/`, and **auto-creates** a commit
   `Import <pkg>-<ver>-<rel>`. A missing Anitya ID is only a warning — it
   does not block progress.

2. **Build locally** (detached, per the pattern above):

   ```bash
   setsid nohup ./ci/build_rpms.sh <pkg> > /tmp/build-<pkg>.log 2>&1 < /dev/null &
   disown
   ```

   Poll until it finishes, then confirm the expected binary RPM names
   appear under `Binary RPMs:` in the log / `builds/<pkg>/RPMS/`.

3. **On build failure: stop.** Diagnose using
   [Debugging Build Failures](../../../documentation/operating/debugging-build-failures.md),
   fix, rebuild until green. Do not import the next package while the
   current one is red — this keeps the dependency chain trustworthy (a
   later package's mock buildroot may need the current one's headers/libs
   from the Fedora repos, and a broken import should never be built on top
   of).

4. Advance to the next package only after a green build.

## Step 3: Populate `upstream_repo` (batched)

Once all packages in the batch build cleanly, populate `upstream_repo` for
all of them in one pass using the `/generate-package-map` skill (or its
rules directly: netfilter.org / canonical-repo mapping, mirror
canonicalization, and the Fedora DistGit fallback when no upstream git repo
exists).

Commit this as **one separate commit** covering all newly-imported
packages' metadata files — do not amend it into the individual import
commits. Example:

```bash
git add metadata/<pkg1>.json metadata/<pkg2>.json ...
git commit -m "Add upstream_repo for <group description> (HUM-XXXX)"
```

This is required before CI passes: `test/test_package_map.py::test_upstream_repo_exists`.

## Step 4: Validate

```bash
make check
```

Runs shellcheck, ruff, mypy, JSON validation, `validate_package_modifications.py --all`,
the full pytest suite (including `test_upstream_repo_exists` for each new
package), markdownlint, frontmatter, and conflict-marker checks. Fix any
findings and re-run before proceeding — this only needs to run once for the
whole batch, not per package.

## Step 5: Push and open the MR

Detect whether you have direct push access to
`redhat/hummingbird/rpms` or need a fork:

```bash
git remote -v
# If only 'origin' (redhat/hummingbird/rpms) exists and you have
# Maintainer/Developer access, push directly:
git push -u origin <branch>

# Otherwise, detect your fork remote and push there:
FORK_REMOTE=$(git remote -v | grep '(push)' | grep -v "redhat/hummingbird/rpms" | head -1 | awk '{print $1}')
git push -u "$FORK_REMOTE" <branch>
```

Open **one MR** covering the whole batch, targeting `main`:

- Title: `HUM-XXXX: Import <group description>`
- Body: a table of source packages → binary RPMs produced → imported
  version; a note on the source branch decision from Step 1; confirmation
  that each package was build-verified locally and `make check` passes;
  `Closes: HUM-XXXX`.

Trigger the automated review once the pipeline starts:

```text
/hummingbird code-review
```

## Expected commit shape

For a batch of N packages: N `Import <pkg>-<ver>-<rel>` commits, followed by
one batched `Add upstream_repo for ...` commit.

## Follow-up checklist (flag to the user, don't do automatically)

Batch package migrations often have follow-up work outside this MR's scope:

- Downstream consumers (lockfiles, image manifests, other repos) that pin to
  the old (e.g. Fedora-published) versions of these packages may need
  updating to consume the new `hum1` builds.
- Functional/regression verification specific to what the packages do (e.g.
  networking connectivity, service behavior) should happen after the
  packages are built and published, not as part of this MR.

Ask the user whether this follow-up belongs in the same ticket or a separate
one.
