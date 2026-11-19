# hum.meta

`hum.meta` is the canonical library for reading, writing, validating, and enumerating
`metadata/<package>.json` files in this repo. Every script that reads or writes
`metadata/<package>.json` should go through this library instead of touching the JSON directly.

This document is a practical guide for *using* the library -- what's here and how to call it.

## Quick start

Everything starts from a `MetadataRepository`, pointed at the repo's top-level checkout directory.
Loading a package gives you back a correctly-typed object -- there's no separate parse step. `load()`
checks that the document is structurally sound (the right fields for its state, nothing
unrecognized) but doesn't run the full rule set `validate()` does -- call `validate()` directly if
you need that guarantee before trusting the data:

```python
from pathlib import Path

from hum.meta import MetadataRepository

repo = MetadataRepository(Path("/path/to/rpms"))  # the repo's top-level checkout directory

metadata = repo.load("bash")  # correctly-typed; call validate() for the full rule set
print(metadata.version)  # "5.4.3"

metadata.upstream_branch = "f43"  # plain setter, validated on assignment
repo.save(metadata)  # re-validates, then writes
```

`MetadataRepository` is the only thing that touches the filesystem. Everything else --
`PackageMetadata` and its four concrete subclasses -- is pure in-memory domain logic with no idea
that JSON or `metadata/` even exist.

## Metadata States

A package's metadata is always exactly one of four concrete classes. Which class it is *is* the
state -- there's no separate status field to check, no `if metadata.status == "clean"`:

* **`CleanPackageMetadata`** -- on Fedora's version, no local changes.
* **`ModifiedFedoraPackageMetadata`** -- on Fedora's version, but with local changes (e.g. a
  backported patch).
* **`ModifiedUpstreamPackageMetadata`** -- ahead of whatever Fedora currently ships.
* **`IndependentPackageMetadata`** -- not tracked against Fedora at all.

Use `isinstance()` to branch on state, or the narrowing helpers below when you expect a specific
one and want a clear error otherwise:

```python
from hum.meta import CleanPackageMetadata

if isinstance(metadata, CleanPackageMetadata):
    print(metadata.fedora.release)

clean = metadata.ensure_clean()  # raises InvalidStateTransitionError if metadata isn't clean
```

`ensure_modified_fedora()`, `ensure_modified_upstream()`, and `ensure_independent()` do the same for
the other three classes.

## Creating new metadata

Use `MetadataRepository`'s `create_*` methods rather than constructing a class directly and saving
it yourself -- they validate and write the file in one step:

```python
from hum.meta import FedoraOriginWithRelease

repo.create_clean(
    "bash",
    version="5.4.3",
    upstream_repo="https://git.savannah.gnu.org/git/bash.git",
    fedora=FedoraOriginWithRelease(
        git_url="https://src.fedoraproject.org/rpms/bash.git",
        branch="rawhide",
        sha="2768211b5135c7169f513965d79e9f89e0ca6124",
        release="3",
    ),
)
```

The equivalent methods for the other three states are `create_modified_fedora(..., reason=...)`,
`create_modified_upstream(..., reason=...)` (takes a plain `FedoraOrigin`, no `release`), and
`create_independent(package_name, *, version, upstream_repo)`.

## Configuring upstream tracking

`track_upstream`, `release_monitoring_project_id`, `version_source`, and `tag_strip_prefix` can
only be set together, through one of three methods -- never as individual field assignments:

```python
metadata.enable_upstream_tracking(track_upstream="1.26", release_monitoring_project_id=12345)
```

That's Anitya-based tracking. For GitLab-tags-based tracking instead:

```python
metadata.enable_upstream_tracking_gitlab_tags(track_upstream="20", tag_strip_prefix="release-")
```

`track_upstream` is optional here -- in this mode it's a version-prefix search filter, not an
identifier. To stop tracking entirely and clear all four fields at once:

```python
metadata.disable_upstream_tracking()
```

The two tracking modes are mutually exclusive: `release_monitoring_project_id` requires Anitya
tracking (no `version_source`), while a customized `tag_strip_prefix` requires GitLab-tags tracking.
Mixing them raises `ValueError`.

## Changing state: transitions

Moving a package from one state to another is always a **transition method** on the current
object, and it always **returns a new object** rather than mutating in place -- the object's class
is changing, and Python objects can't change their own class mid-flight:

```python
modified = metadata.to_modified_fedora(reason="Backport CVE fix")
repo.save(modified)
```

The 10 valid transitions:

| From | To | Method |
| --- | --- | --- |
| Clean | ModifiedFedora | `to_modified_fedora(reason=...)` |
| Clean | ModifiedUpstream | `to_modified_upstream(version=..., reason=...)` |
| Clean | Independent | `to_independent()` |
| ModifiedFedora | Clean | `to_clean()` |
| ModifiedFedora | ModifiedUpstream | `to_modified_upstream(version=..., reason=...)` |
| ModifiedFedora | Independent | `to_independent()` |
| ModifiedUpstream | Clean | `to_clean(fedora=..., version=...)` |
| ModifiedUpstream | ModifiedFedora | `to_modified_fedora(fedora=..., reason=...)` |
| ModifiedUpstream | Independent | `to_independent()` |
| Independent | Clean | `to_clean(fedora=..., version=...)` |

If a transition isn't in this table, it isn't valid -- there's no method for it.

**The object a transition was called on is consumed by that call.** Any further attribute access
on it raises `StaleMetadataError` -- always use the object the transition returned:

```python
modified = metadata.to_modified_fedora(reason="Backport CVE fix")
metadata.version  # raises StaleMetadataError -- use `modified` instead
```

**Same-state updates** are different: refreshing a package's version or Fedora build without
changing its state uses `update()` instead, which mutates in place:

```python
metadata.update(fedora=new_fedora, version="5.4.4")  # Clean/ModifiedFedora
metadata.update(version="0.9~rc5")                   # ModifiedUpstream/Independent
```

## Computing the next Release

`bump_release(current_release)` computes the next spec `Release:` value -- the caller writes the
result to the spec file, it's never stored in metadata itself:

```python
next_release = metadata.bump_release(current_release)
```

The algorithm depends on state. On `CleanPackageMetadata`/`ModifiedFedoraPackageMetadata`,
`current_release` is compared against `fedora.release`: if they match, this is the first local
rebuild since import/sync (`fedora.release + ".1"`); if `current_release` already has a trailing
`.N` past that base, it increments (`.N` -> `.N+1`). On `ModifiedUpstreamPackageMetadata`/
`IndependentPackageMetadata`, there's no stored base to compare against -- `current_release` is
expected to already be shaped `0.N`, and it increments directly (`0.1` -> `0.2`).

Either way, `bump_release()` raises `ReleaseMismatchError` if `current_release` doesn't match the
shape its state implies -- e.g. the spec's `Release:` disagreeing with `fedora.release`, or not
being `0.N`-shaped when there's no stored base.

## Other fields

A handful of fields are plain settable properties, independent of state, with no cross-field
rules: `upstream_branch`, `version_suffix_strip`, `upstream_version_transform`,
`source_availability_check`, `cve_product`, `version_transform`, `fix_status`. Set them directly:

```python
metadata.cve_product = "bash"
metadata.version_suffix_strip = "-RELEASE"
```

## Private products

`private_product` is independent of package state. Set or clear it through the metadata object:

```python
metadata = repo.load("hello")
metadata.private_product = "example"
repo.save(metadata)

metadata.private_product = None  # clear the assignment; omit the field from JSON
repo.save(metadata)
```

The setter checks that a supplied value is a non-empty string. `save()` also checks that the
product matches an RPA entry in `ci/konflux_rpa_config.yml`. `load()` checks the metadata format,
not the external RPA configuration.

Use repository queries rather than scanning metadata files:

```python
repo.private_products()  # {"hello": "example", "nodejs20-lts": "nodejs20"}
repo.packages_by_private_product()  # {"example": ["hello"], "nodejs20": ["nodejs20-lts"]}
```

Both queries validate assignments against the RPA configuration and reject malformed metadata or
unknown products. Public packages are omitted. Package names, including a `-lts` suffix, do not
determine assignments.

## Validation

Every constructor, setter, and transition already enforces the rules relevant to it, so most
invalid states can't be constructed in the first place. But if you need to check an
already-in-hand object -- or re-verify after code you don't control has touched it -- call
`validate()` directly:

```python
result = metadata.validate()
if result.violations:
    for violation in result.violations:
        print(violation.message)
```

`validate()` re-derives every applicable check from the object's actual current data every time --
it doesn't trust that the object was only ever built or mutated through this library's own API.
One check needs data the object doesn't have on its own, so it only runs if you supply a resolver
callback:

```python
from hum.meta import resolve_spec_version

result = metadata.validate(
    resolve_spec_version=lambda m: resolve_spec_version(rpms_root / m.package_name),
)
```

`resolve_spec_version` is provided by this library (`hum.meta.spec`) -- it runs `rpmspec` against
a package's spec file and returns its resolved `Version:` line.

`ValidationResult.checked_spec_version` tells you whether that check ran, so an empty `violations`
list is never ambiguous between "nothing wrong" and "didn't check."

`MetadataRepository.save()` always calls `validate()` (with no resolvers) before writing, and
raises `InvalidMetadataError` -- without writing anything -- if there are any violations.

## Error handling

Each failure mode this library can hit has its own exception, so a caller can catch exactly the
one it knows how to handle instead of a bare `Exception`:

| Exception | Raised when |
| --- | --- |
| `MalformedMetadataError` | `load()` can't classify the raw JSON at all -- an unrecognized field, a missing required field, an invalid `modification_status` |
| `InvalidMetadataError` | `save()` is called on a well-formed but rule-violating object; carries `.result`, a `ValidationResult` |
| `InvalidStateTransitionError` | `ensure_clean()`/`ensure_modified_fedora()`/`ensure_modified_upstream()`/`ensure_independent()` is called on the wrong class |
| `StaleMetadataError` | An object is used after one of its own transition methods already ran on it |
| `ReleaseMismatchError` | `bump_release()` is given a `Release:` value inconsistent with the object's own state |

All five inherit from `MetadataError`, so catching that alone is enough for a caller that just
wants to know "something about this package's metadata is wrong" without distinguishing why.

## Enumerating packages

`MetadataRepository` also answers "which packages" questions, matching what
`dist_git.py`/`check_upstream_versions.py`/`generate_resources.py` already compute today, but in
one place:

```python
repo.packages_with_spec()          # rpms/<name>/ dirs containing a .spec file
repo.packages_with_metadata()      # metadata/<name>.json files that exist
repo.target_packages()             # target-packages.yml, unioned with the rpms/ directory listing
repo.all_package_directories()     # every directory under rpms/, unconditionally
repo.load_all()                    # {name: PackageMetadata} for every metadata/*.json file
```

All four enumeration methods return sorted lists.
