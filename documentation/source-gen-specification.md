# Hummingbird RPMs Source Generation Tool - Specification

## Overview

A modular Python CLI tool (`ci/source_gen.py`) that generates RPM source tarballs from declarative YAML configuration files. All pipeline steps execute in Podman containers for reproducibility.

This tool replaces reliance on Fedora's lookaside cache, enabling Hummingbird to:
- Build packages ahead of Fedora
- Operate independently of Fedora infrastructure
- Generate all source tarballs locally

---

## Architecture

### Design Principles

1. **Modularity**: Each step is a self-contained class, easy to add/modify/remove
2. **Separation of concerns**: Config parsing, execution, container management, and output generation are independent modules
3. **Extensibility**: New steps registered via simple registry pattern
4. **Testability**: All components can be tested in isolation

### Module Structure

```
ci/
├── source_gen.py                     # CLI entry point
│                                     # Thin wrapper, delegates to modules
│
└── source_gen/
    ├── __init__.py                   # Package exports
    │
    ├── config.py                     # YAML parsing, schema validation
    │   ├── SourceGenConfig           # Main config dataclass
    │   ├── load_config()             # Load and validate YAML
    │   ├── VariableResolver          # Handle {version}, {major}, etc.
    │   └── validate_config()         # Schema validation
    │
    ├── executor.py                   # Pipeline orchestration
    │   ├── PipelineExecutor          # Runs steps in sequence
    │   ├── ExecutionContext          # Shared state between steps
    │   └── ExecutionResult           # Result/error handling
    │
    ├── container.py                  # Podman abstraction
    │   ├── ContainerRunner           # Run commands in containers
    │   ├── DEFAULT_IMAGE             # registry.fedoraproject.org/fedora:43
    │   └── ContainerConfig           # Mount points, env vars, etc.
    │
    ├── archive.py                    # Archive creation
    │   ├── create_archive()          # Multi-format (.tar.gz, .tar.bz2, etc.)
    │   ├── SUPPORTED_FORMATS         # Format registry
    │   └── ArchiveSpec               # Source, dest, prefix, excludes
    │
    ├── checksum.py                   # sources file handling
    │   ├── compute_sha512()          # Calculate file checksum
    │   ├── update_sources_file()     # Update rpms/<pkg>/sources
    │   └── parse_sources_file()      # Read existing checksums
    │
    ├── spec_updater.py               # Spec file modifications
    │   ├── SpecUpdater               # Wrapper around specfile library
    │   ├── update_tags()             # Update Version, Release, etc.
    │   ├── update_macros()           # Update %global definitions
    │   └── add_changelog_entry()     # Add changelog entries
    │
    ├── tracking.py                   # Fedora upstream tracking
    │   ├── fetch_upstream_spec()     # Clone/fetch Fedora dist-git
    │   ├── compute_upstream_diff()   # Diff since last_synced_sha
    │   ├── compute_local_diff()      # Diff local vs Fedora current
    │   └── update_tracking_sha()     # Update YAML after sync
    │
    └── steps/                        # Step implementations
        ├── __init__.py               # Step registry
        │   ├── STEP_REGISTRY         # Maps step names to classes
        │   ├── register_step()       # Decorator for registration
        │   └── get_step()            # Lookup step by name
        │
        ├── base.py                   # Abstract base class
        │   ├── Step (ABC)            # Abstract step interface
        │   │   ├── execute()         # Run the step
        │   │   ├── validate()        # Validate parameters
        │   │   └── get_dependencies()# DNF packages needed
        │   └── StepContext           # Workdir, variables, etc.
        │
        ├── git.py                    # git-checkout step
        ├── fetch.py                  # fetch step (URL download)
        ├── patch.py                  # patch step
        ├── run.py                    # run step (arbitrary commands)
        ├── go.py                     # go/vendor, go/bump, go/tidy
        └── cargo.py                  # cargo/vendor, cargo/bump
```

### Step Registry Pattern

```python
# steps/__init__.py
STEP_REGISTRY: dict[str, type[Step]] = {}

def register_step(name: str):
    """Decorator to register a step class."""
    def decorator(cls: type[Step]) -> type[Step]:
        STEP_REGISTRY[name] = cls
        return cls
    return decorator

def get_step(name: str) -> type[Step]:
    """Get step class by name."""
    if name not in STEP_REGISTRY:
        raise ValueError(f"Unknown step: {name}")
    return STEP_REGISTRY[name]

# steps/git.py
@register_step("git-checkout")
class GitCheckoutStep(Step):
    def execute(self, context: StepContext) -> StepResult:
        ...
```

This makes adding new steps trivial - just create a new file with the `@register_step` decorator.

---

## YAML Schema v1

```yaml
schema_version: 1

package:
  name: syft
  version: 1.40.1
  release: 1
  license: Apache-2.0  # Optional

# Optional: Fedora upstream tracking
tracking:
  source: https://src.fedoraproject.org/rpms/syft.git
  branch: rawhide
  last_synced_sha: abc123def456

pipeline:
  - name: Clone repository
    uses: git-checkout
    with:
      repository: https://github.com/anchore/syft
      ref: v{version}
      dest: syft-{version}
      expected-sha: 63927ab49feea10a7fd88191acd615667227d338  # Optional

  - name: Download file
    uses: fetch
    with:
      url: https://example.com/data.tar.gz
      dest: data.tar.gz
      expected-sha256: abc123...

  - name: Apply patches
    uses: patch
    with:
      directory: syft-{version}
      patches:
        - fix-for-vendoring.patch  # From rpms/<package>/
      strip: 1  # Optional, default: 1

  - name: Bump Go dependencies
    uses: go/bump
    with:
      modroot: syft-{version}
      deps:
        - module: golang.org/x/crypto
          version: v0.32.0

  - name: Vendor Go dependencies
    uses: go/vendor
    with:
      modroot: syft-{version}
      output: vendor  # Optional, default: <modroot>/vendor

  - name: Run go mod tidy
    uses: go/tidy
    with:
      modroot: syft-{version}

  - name: Bump Rust dependencies
    uses: cargo/bump
    with:
      manifest: project/Cargo.toml
      deps:
        - crate: openssl
          version: 0.10.70

  - name: Vendor Rust dependencies
    uses: cargo/vendor
    with:
      manifest: project/Cargo.toml
      output: rust-vendor  # Optional

  - name: Custom command
    uses: run
    with:
      command: |
        ./scripts/generate-provides.sh {version}

outputs:
  - type: archive
    source: syft-{version}
    filename: syft-{version}.tar.gz
    exclude:  # Optional
      - .git/
      - vendor/
    # prefix: derived from filename stem by default

  - type: archive
    source: vendor
    filename: syft-{version}-vendor.tar.bz2
    prefix: vendor  # Optional override

changelog:  # Optional
  - entry: "Update to {version}"
  - entry: "Fix CVE-2024-12345 by bumping golang.org/x/crypto"
    cves:
      - CVE-2024-12345

spec_updates:  # Optional
  - tag: Version
    value: "{version}"
  - tag: Release
    value: "{release}%{?dist}"
  - macro: golang_version
    value: "1.24.12"
```

---

## Variable Substitution

| Variable | Description | Example (`1.40.1`) |
|----------|-------------|-------------------|
| `{name}` | Package name | `syft` |
| `{version}` | Full version | `1.40.1` |
| `{release}` | Release number | `1` |
| `{major}` | Major version | `1` |
| `{minor}` | Minor version | `40` |
| `{patch}` | Patch version | `1` |

---

## CLI Commands

### Source Generation

```bash
# Generate sources for a single package
ci/source_gen.py generate <package>

# Generate sources for all packages with YAML configs
ci/source_gen.py generate --all

# Dry-run mode (show what would be done)
ci/source_gen.py generate <package> --dry-run
```

### Version Updates

```bash
# Update version and regenerate sources
ci/source_gen.py update <package> --version X.Y.Z [--expected-sha SHA]
```

### Validation

```bash
# Validate YAML configuration
ci/source_gen.py validate <package>

# Validate all configurations
ci/source_gen.py validate --all
```

### Fedora Tracking

```bash
# Show what changed in Fedora since last sync
ci/source_gen.py upstream-diff <package>

# Show how our local spec differs from Fedora's current HEAD
ci/source_gen.py local-diff <package>

# Interactive update - fetch Fedora changes, show diff, let user merge
ci/source_gen.py upstream-update <package>
```

---

## Pipeline Steps Reference

| Step | Description | Required Params | Optional Params |
|------|-------------|-----------------|-----------------|
| `git-checkout` | Clone git repo | `repository`, `ref`, `dest` | `expected-sha` |
| `fetch` | Download URL | `url`, `dest`, `expected-sha256` | |
| `patch` | Apply patches | `directory`, `patches` | `strip` (default: 1) |
| `run` | Shell commands | `command` | |
| `go/vendor` | Vendor Go deps | `modroot` | `output` |
| `go/bump` | Bump Go deps | `modroot`, `deps` | |
| `go/tidy` | go mod tidy | `modroot` | |
| `cargo/vendor` | Vendor Rust deps | `manifest` | `output` |
| `cargo/bump` | Bump Rust deps | `manifest`, `deps` | |

---

## Archive Formats

Inferred from filename extension:

| Extension | Format |
|-----------|--------|
| `.tar.gz`, `.tgz` | gzip-compressed tar |
| `.tar.bz2`, `.tbz2` | bzip2-compressed tar |
| `.tar.xz`, `.txz` | xz-compressed tar |
| `.zip` | ZIP archive |

---

## Output Specification

### Archive Prefix

By default, the archive prefix (directory name inside the archive) is derived from the filename stem:
- `syft-1.40.1.tar.gz` → contents under `syft-1.40.1/`

This can be overridden with the `prefix` field:
```yaml
outputs:
  - type: archive
    source: vendor
    filename: syft-{version}-vendor.tar.bz2
    prefix: vendor  # Contents under "vendor/" instead of "syft-1.40.1-vendor/"
```

### Exclude Patterns

Exclude files/directories from archives:
```yaml
outputs:
  - type: archive
    source: syft-{version}
    filename: syft-{version}.tar.gz
    exclude:
      - .git/
      - vendor/
      - target/
```

---

## Container Execution

All pipeline steps execute in Podman containers for reproducibility.

### Default Image

```
registry.fedoraproject.org/fedora:43
```

### Step Dependencies

Each step declares its required DNF packages, which are installed before execution:

| Step | Dependencies |
|------|--------------|
| `git-checkout` | `git` |
| `fetch` | `curl` |
| `patch` | `patch` |
| `go/*` | `golang` |
| `cargo/*` | `rust`, `cargo` |

### Execution Flow

1. Create temporary working directory
2. Mount workdir into container
3. Install step dependencies via DNF
4. Execute step commands
5. Copy artifacts back to host
6. Cleanup

---

## Fedora Tracking

### Workflow

1. Initial import sets `tracking.last_synced_sha` in YAML
2. Run `upstream-diff` to see if Fedora has changes
3. If changes look useful, run `upstream-update`:
   - Fetches Fedora's current spec to temp location
   - Shows diff from our spec
   - User manually copies/merges desired changes
   - User confirms, tool updates `last_synced_sha` in YAML
4. Local modifications are always preserved - user has full control

### Tracking Configuration

```yaml
tracking:
  source: https://src.fedoraproject.org/rpms/syft.git
  branch: rawhide
  last_synced_sha: abc123def456
```

---

## Patch Handling

Patches can be applied at two points:

1. **During source generation** (via `patch` step): Baked into source tarball
2. **During RPM build** (via `%autosetup`): Applied at build time

Patch files are stored in `rpms/<package>/` and referenced by filename in the YAML:

```yaml
pipeline:
  - name: Apply patches for vendoring
    uses: patch
    with:
      directory: project-{version}
      patches:
        - fix-go-mod.patch
        - hummingbird-specific.patch
      strip: 1
```

---

## Key Design Decisions

| Decision | Choice |
|----------|--------|
| Container runtime | Podman |
| Default image | `registry.fedoraproject.org/fedora:43` |
| All steps containerized | Yes |
| Archive formats | `.tar.gz`, `.tar.bz2`, `.tar.xz`, `.zip` |
| Archive prefix | From filename stem, overridable |
| Variables | `{name}`, `{version}`, `{release}`, `{major}`, `{minor}`, `{patch}` |
| Vendor output | Configurable directory |
| Archive excludes | Supported |
| Patch files location | `rpms/<package>/` |
| Fedora tracking | On-demand fetch, metadata in YAML |
| Dependencies | Self-contained in container |
| Dry-run | Supported |
| Auto-commit | No |

---

## Example: chunkah Package

```yaml
schema_version: 1

package:
  name: chunkah
  version: 0.1.1
  release: 1
  license: MIT OR Apache-2.0

pipeline:
  - name: Clone chunkah repository
    uses: git-checkout
    with:
      repository: https://github.com/jlebon/chunkah
      ref: v{version}
      dest: chunkah-{version}

  - name: Vendor Rust dependencies
    uses: cargo/vendor
    with:
      manifest: chunkah-{version}/Cargo.toml
      output: vendor

outputs:
  - type: archive
    source: chunkah-{version}
    filename: chunkah-{version}.tar.gz
    exclude:
      - .git/

  - type: archive
    source: vendor
    filename: chunkah-{version}-vendor.tar.bz2
    prefix: vendor
```

---

## Implementation Phases

| Phase | Components | Deliverables |
|-------|------------|--------------|
| 1 | `source_gen.py`, `config.py`, `container.py`, `steps/base.py` | Core infrastructure |
| 2 | `steps/git.py`, `steps/fetch.py`, `steps/patch.py`, `steps/run.py` | Core steps |
| 3 | `archive.py`, `checksum.py` | Output generation |
| 4 | `steps/go.py`, `steps/cargo.py` | Language steps |
| 5 | `spec_updater.py` | Spec file updates |
| 6 | `executor.py`, `generate` command | Full pipeline |
| 7 | `tracking.py`, tracking commands | Fedora tracking |
| 8 | `update`, `validate` commands | Remaining CLI |
| 9 | `test/test_source_gen.py` | Tests |
