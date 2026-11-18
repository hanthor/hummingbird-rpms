---
title: Package Metadata Fields
weight: 54
aliases: [/l/package-metadata-fields]
---

## Overview

Each package has a metadata file at `metadata/<package>.json`. This page explains metadata
configuration and tooling behavior for day-to-day package operations. The
[RPM metadata and versioning specification][metadata-spec] defines the authoritative metadata
contract, including field definitions, package states, and validation rules.

Two fields control how Hummingbird treats local changes and rebuilds relative to Fedora:

| Field                 | Purpose                                                                          |
| --------------------- | -------------------------------------------------------------------------------- |
| `modification_status` | Whether the package may be auto-updated from Fedora                              |
| `fedora.release`      | Fedora release baseline when the package still ships the imported Fedora version |

Fedora provenance is stored in the `fedora` object, with `git_url`, `branch`, and `sha` identifying
the import or last sync. `fedora.release` is present only while the package ships that Fedora
version. A package ahead of Fedora retains provenance but has no `fedora.release`; an independent
package has no `fedora` object. There is no top-level `release` field.

## `modification_status`

`modification_status` records whether a package still matches its Fedora import, diverges from
that import, or has no Fedora origin.

| Status        | Meaning                                              | Auto-updates from Fedora |
| ------------- | ---------------------------------------------------- | ------------------------ |
| `clean`       | Unmodified Fedora import                             | Allowed                  |
| `modified`    | Local changes or an upstream version ahead of Fedora | Blocked                  |
| `independent` | Hummingbird-independent package (not from Fedora)    | Blocked                  |

### When to use each value

- **`clean`**: Default for packages imported from Fedora with no local source changes. Automatic
  Fedora updates are allowed. Conflicts with `modification_reason` (must be absent).
- **`modified`**: Use after backports, custom patches, or updates ahead of Fedora that must not be
  overwritten by an automatic update. Requires `modification_reason`.
- **`independent`**: Required for packages that are not imported from Fedora. These packages have
  no `fedora` object. Conflicts with `modification_reason` (must be absent).

Imports set the status automatically, using `clean` for Fedora imports and `independent` for
Hummingbird-independent imports. After local source changes, set status with
`dist_git.py mark-modified`. See [Package Modification Tracking][modification-tracking].

Rebuilds that only bump the spec `Release:` line do **not** change `modification_status`.
Release-only changes are ephemeral and are ignored when deciding whether a package is modified
versus clean.

### `modification_reason`

When `modification_status` is `modified`, `modification_reason` is required. It should be a short
explanation of why the package cannot be auto-updated (for example, a CVE backport or a custom
spec change). Clear the reason when marking the package clean again.

`modification_reason` applies only to `modified` packages. Do not add it for `clean` or `independent`
packages. See [When not to update these fields][when-not-to-update].

## `fedora.release` {#release}

The `fedora.release` field in metadata is not the same as the `Release:` line in the package spec.
It records the imported Fedora release without a dist tag such as `.fc42`. It remains present
after local patches or release-only rebuilds while the package stays on the imported Fedora
version. The spec's `Release:` line is authoritative for the Hummingbird build.

| Location                                     | What it represents                                                                                                      |
| -------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `metadata/<package>.json` → `fedora.release` | Fedora release baseline for the imported version, without its dist tag                                                  |
| Spec `Release:`                              | The release used for the Hummingbird build (includes `%{?dist}` / `.hum1`, and may include local `.N` rebuild suffixes) |

### Who writes metadata `fedora.release` {#who-writes-metadata-release}

| Writer                                   | When                                           | Value stored                                                                                                      |
| ---------------------------------------- | ---------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `dist_git.py import` / `update` / `sync` | Fedora import or refresh                       | Resolved Fedora release with dist suffix stripped (e.g. `5` from `5.fc42`)                                        |
| `check_upstream_versions.py`             | Local upstream version bump (`check --update`) | Removes `fedora.release` for Fedora-origin packages moving ahead of Fedora, retaining the other provenance fields |

### Fedora-imported packages

For packages with a `fedora` object:

- `fedora.release` is set on import and refreshed when `dist_git.py update` / `sync` brings in
  a Fedora version. Dist suffixes such as `.fc42` are stripped.
- When `check_upstream_versions.py check --update` moves the package ahead of Fedora, it removes
  `fedora.release` and marks the package `modified`. The default updater resets a literal spec
  release to `0.1%{?dist}`. It preserves `%autorelease` and other package-specific release macros;
  an `update_spec` hook can replace the default spec-update behavior. See
  [Package Modification Tracking][modification-tracking] for update hooks.
- Do not change `fedora.release` during local rebuilds or backports. Rebuilds bump only the spec's
  `Release:` line. With a Fedora baseline, the first rebuild appends `.1` and later rebuilds
  increment that suffix. Without a Fedora baseline, rebuilding `0.1` advances it to `0.2`.
- When Fedora uses `%autorelease`, `fedora.release` stores the resolved numeric release, not
  the literal macro. Imports and updates resolve it through Fedora build services and replace
  the local spec's `%autorelease` with that value plus `%{?dist}`. For rebuilds that still see
  `%autorelease`, follow [Rebuilding Packages][rebuilding].

### Independent packages

For Hummingbird-independent packages with `modification_status: "independent"`:

- There is no Fedora origin, so metadata does not contain a `fedora` object or `fedora.release`.
- The `.hum1` suffix comes from the spec `Release:` line via `%{?dist}` (for example
  `Release: 1%{?dist}`), not from metadata.
- `dist_git.py rebuild` increments the spec's current release directly for independent packages.

### Spec `Release:` vs metadata `fedora.release` {#spec-release-vs-metadata-release}

No-change rebuilds and reverse-dependency rebuilds change the **spec** `Release:` only. See
[Rebuilding Packages][rebuilding]. When `dist_git.py update` or `sync` imports a Fedora version,
it records that Fedora release in `fedora.release`. Local patches merged onto that version
retain the Fedora release baseline. When `check_upstream_versions.py` moves a package ahead of
Fedora, it removes `fedora.release`. See the specification's
[built NVR rules][built-nvr] for the complete versioning contract.

## `private_product`

`private_product` records an internal product assignment for private RPM distribution. It is
independent of the package's Fedora relationship and versioning state. See the
[metadata specification][private-product-spec] for the field's format.

Read and write assignments through `hum.meta`. The library checks that a product matches an
RPA entry in `ci/konflux_rpa_config.yml`; resource generation uses that assignment to select the
private Konflux application and exclude the package from public RPAs. Packages without an
assignment remain public, regardless of their names.

See [Private RPM Repositories][private-repositories] for assignment and onboarding commands.

## When not to update these fields

Most day-to-day package work changes the spec, sources, or patches, not metadata
`modification_status`, `modification_reason`, or `fedora.release`. Leave those fields alone unless the
package's relationship to Fedora actually changed.

| Situation                                                               | Do not change                                                  | Why                                                                                                                                               |
| ----------------------------------------------------------------------- | -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| Independent package gets a CVE patch, backport, or other source edit    | `modification_status`, `modification_reason`                   | Status stays `independent`. There is no Fedora auto-update to block, so do not switch to `modified` or add a `modification_reason`.               |
| No-change rebuild (spec `Release:` bump only)                           | `modification_status`, `modification_reason`, `fedora.release` | Status stays unchanged; `fedora.release` continues to record a Fedora baseline when one exists.                                                   |
| Fedora-imported package gets a local patch or backport                  | `fedora.release`                                               | Mark the package `modified` with a reason, but keep `fedora.release` if present. Only bump the spec's `Release:` if needed.                       |
| Package is already `modified` and you add another local change          | `modification_status`                                          | Leave status as `modified`. Update `modification_reason` only if the existing reason no longer describes why auto-updates must stay blocked.      |
| Upstream Fedora update lands and automatic Fedora updates should resume | Do not leave stale fields                                      | Use `dist_git.py update` to refresh version and Fedora provenance. Mark clean once the package matches Fedora; this clears `modification_reason`. |

### Examples

**Independent package + CVE fix:** Edit the spec and add the patch. Keep the metadata status and
version unchanged, as shown in this excerpt, not a complete metadata file:

```json
{
  "modification_status": "independent",
  "version": "1.3.0"
}
```

Do not add `modification_reason`, and do not change status to `modified`.

**Fedora package + CVE backport:** After the patch, mark modified so auto-updates stay blocked:

```bash
make dist-git ARGS='mark-modified <package> --modified --reason "Backport CVE-2024-12345"'
```

Do not edit `fedora.release` as part of that change.

**Rebuild only:** Use `dist_git.py rebuild`. Leave metadata
`modification_status` and `fedora.release` unchanged.

## Related documentation

- [RPM metadata and versioning specification][metadata-spec] defines the metadata contract.
- [Package Modification Tracking][modification-tracking] covers marking packages modified or clean,
  checking status, validation, and update hooks.
- [Rebuilding Packages][rebuilding] covers spec release bumps for no-change rebuilds and backports.
- [Adding Independent Packages][independent-packages] covers packages not imported from Fedora.
- [Updating Dist-git Packages][updating] covers imports and Fedora updates.

[metadata-spec]: https://hummingbird-project.io/l/rpm-metadata-and-versioning
[private-product-spec]: https://hummingbird-project.io/l/rpm-metadata-and-versioning#private-product-assignment
[private-repositories]: private-rpm-repositories.md
[built-nvr]: https://hummingbird-project.io/l/rpm-metadata-and-versioning#computing-the-built-nvr
[modification-tracking]: package-modification-tracking.md
[rebuilding]: rebuilding-packages.md
[independent-packages]: adding-independent-packages.md
[updating]: updating-dist-git-packages.md
[when-not-to-update]: #when-not-to-update-these-fields
