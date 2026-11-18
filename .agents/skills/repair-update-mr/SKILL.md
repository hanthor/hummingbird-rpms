---
name: repair-update-mr
description: Repair stale or conflicted Hummingbird package-update MRs, preserving local fixes and keeping branches, commits, titles, and descriptions clean.
---

# Repair Update MR

Repair the requested MRs on their existing branches with the smallest correct change.
Do not create replacement MRs, merge, or close MRs automatically. Commit, push, and change
MR readiness only when authorized by the user, otherwise leave the repair for review.

## Workflow

1. Read repository instructions, the MR, its current diff, and relevant failure output.
   Fetch the source and target branches. Check whether the update is already on the target
   or has been superseded before repairing it.
2. Switch to the MR branch without disturbing uncommitted work. Use a separate worktree.
   Record the remote source tip for an explicit force-with-lease push.
3. Read the package metadata and modification reason (field semantics:
   `documentation/operating/package-metadata-fields.md`). For Fedora-derived packages, compare
   local changes with Fedora using `make dist-git ARGS='diff <pkg>'` and check the tracked
   Fedora branch for relevant newer fixes; for `independent` packages, compare with the
   upstream source instead. Compare patches and dependencies with the upstream release being
   packaged, too.
4. Fix the actual failure and verify with the existing tooling below. Preserve intentional local
   names, interpreter versions, security fixes, and build settings. Do not resolve conflicts
   by blindly choosing one side. Minimize unexplained divergence, not all divergence.
   Do not pull unrelated newer versions into the repair.
5. Run the checks below and review the final diff. Confirm every remaining difference from
   Fedora or upstream is intentional. Check mergeability against the freshly fetched target.
6. When authorized, amend the existing bot update commit where safe. Keep its message
   accurate and concise, preserve required trailers, and do not squash unrelated work.
   Inspect commits created by package tooling before rewriting anything. Push with an
   explicit lease against the recorded remote tip; if it fails, inspect the new remote work.
7. Refresh the MR title and description to describe the actual outcome, important preserved
   changes, checks performed, and blockers. Preserve Jira links and useful context, not
   obsolete failure dumps. Remove Draft/CONFLICT and the draft automation's `no-test` label
   only when the repair is complete, conflict-free, and authorized to be marked ready.
   Report observed CI results; pending or failed checks are not success. In the MR description,
   a Jira ticket must be mentioned. Use the ticket associated with the work being repaired,
   e.g.: Jira: [HUM-XXXX](https://redhat.atlassian.net/browse/HUM-XXXX)

## Package tooling

Routine evidence is read-only:

| Task | Command |
| --- | --- |
| Compare local packaging with Fedora | `make dist-git ARGS='diff <pkg>'` |
| Check tracked upstream versions | `ci/check_upstream_versions.py check <pkg>` |

The update commands (`make dist-git ARGS='update <pkg>'`,
`ci/check_upstream_versions.py check --update <pkg>`) are not validation: they can overwrite or
delete patch work, restore files from `HEAD`, and auto-commit. Rerun one only to re-bump a
version the MR already carries, in a clean scratch worktree, with permission if it may commit.

Default to uncommitted changes; commit and push are separate permissions. Never treat
`--dry-run` as a safe preview or `sync` as conflict resolution. For `independent` packages the
Fedora commands do not apply: use `ci/check_upstream_versions.py`, keep status `independent`,
and never run `mark-modified` or add a `modification_reason`.

## Patch resolution

- Inventory every active patch and its application point: `PatchN` entries, `%autosetup`/
  `%autopatch`/`%patch`, `%goprep`, patches stored as `SourceN`, explicit commands, and
  scripts called by `%prep`. Record order, strip levels, and architecture conditionals.
- Keep checksum-verified pristine sources separate from the mutable preparation tree. For
  vendor patches, prove against the dependency version actually selected by the updated
  package; stale `go.sum` entries do not establish that version.
- Reproduce failures against the intended release in scratch, never in the package checkout,
  using the real patch tool and flags. Keep needed patches that apply; rebase needed patches
  whose context changed.
- Drop a patch or hunk only after inspecting released upstream code and proving every removed
  change is present, equivalently fixed, or no longer applicable. Record the release or
  dependency version, affected file/symbol, and why the old intent is satisfied. A patch
  failure, a release note, or reverse application against a locally patched tree is not proof.
- For partially upstreamed or multi-file patches, keep the remaining needed changes; do not
  drop the whole patch because one hunk is upstream. Keep vendoring pre-commands, source
  patches, and regenerated manifests consistent.
- Remove obsolete patch files together with their declarations and application references,
  then rerun the complete retained sequence from fresh sources after the final edit.

## Verification

- Run all applicable tests and validation using documented commands, including `make check`.
  Start with focused checks, then run the full repository checks. Add regression coverage
  when the repair changes tooling behavior. Do not silently skip unavailable checks.
- Check spec parsing and the actual package name, version, and release expansion; do not
  trust the MR title or assume `%autorelease` expands to the intended release.
- Validate package modification metadata, conflict markers, and whitespace. Verify source
  checksums and the selected patch sequence when affected.
- Prefer real preparation over guesswork: `rpmbuild -bp --nodeps` in a Linux scratch tree with
  the relevant macros and tools. This is preparation, not a full RPM build, and it proves the
  spec's extraction, transformations, and patches apply in build order. If `%prep` itself
  compiles or needs unavailable work, replay its source extraction, transformations, and patch
  commands in order and report "ordered patch replay passed", not "prep passed".
- Leave RPM builds and build-dependent tests to CI, especially Rust, Go, Python...
- `no-test` is draft automation metadata, not permission to skip tests. Follow the readiness
  step above to remove it; never claim unobserved CI success or weaken checks to get green.
- Finish with changes, verification results, and unresolved issues for each requested MR.

## Stop and explain instead of guessing

- Already updated on the target: recommend closing the duplicate; do not close it yourself.
- Missing source archive: do not invent checksums or substitute a release candidate.
- Possibly redundant backport: confirm its fix is upstream before dropping it.
- Missing credentials or an unapproved target-branch merge/rebase: state the next action.
- Current branch already passes: avoid unnecessary changes or commit rewrites.
