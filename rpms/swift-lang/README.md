# Swift Language Fedora Package

## update_swift_version.py

A script to update the Swift language package from Swift's official checkout configuration.

### Usage

```bash
./update_swift_version.py <swift-version>
```

### Example

```bash
./update_swift_version.py 6.1.3
```

### What it does

1. Downloads the Swift release configuration from the official Swift repository
2. Parses the repository versions for all Swift components
3. Reads embedded LLVM, cmark-gfm, ICU, LMDB, and BoringSSL versions from the pinned source revisions
4. Excludes repositories that the Fedora production preset does not check out
5. Generates bundled `Provides` for every retained checkout in `bundled-provides.inc`
6. Emits stable source-index macros used by the patch declarations
7. Updates the source URLs and package version in `swift-lang.spec`

The script prepares and validates the full update before replacing the spec and include files. A failed download or an unrecognized version format leaves both files unchanged.

Keep `EXCLUDED_CHECKOUTS` in the script aligned with `fedora-presets.ini`. The list is the only package-specific policy needed for ordinary checkout metadata.

### Requirements

- Python 3

### After running

Review the changes to `swift-lang.spec` and remove any patches that are no longer needed for the new version.
