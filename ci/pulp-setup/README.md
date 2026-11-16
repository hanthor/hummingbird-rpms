# Creating Pulp Domain and Repositories

## Overview

- This document and script should be used to initialize and setup a pulp domain and a set of
  repositories
- We choose the domain prefix of `public-` to ensure that the repositories are publicly available

## Prerequisites

Install the Pulp CLI with the `console` plugin, which provides the
`pulp console populated-domain create` command this script uses to create domains:

```bash
pip install "pulp-cli-console>=0.1.5.dev0"
```

> **Note:** `pulp-cli-console` only publishes pre-release versions on PyPI, so the version
> specifier above is required — a plain `pip install pulp-cli-console` will not find a
> matching distribution. This also pulls in a compatible `pulp-cli`; RPM support is built
> into the base `pulp-cli` package, so no separate RPM plugin is needed. Note that pip may
> need to **downgrade** an existing newer `pulp-cli` install to satisfy `pulp-cli-console`'s
> pinned dependency — this is expected.

Verify the install — `pulp console --help` should show the `populated-domain` command group
(the `console` plugin does not appear under `pulp --version`'s Plugin Versions list):

```bash
pulp console --help
```

## Setup

### Using existing credentials

- Obtain the value of the field `cli.toml` from the secret located
  in the vault at
  `rhel-primitives/PULP_PUBLIC_RHEL_PRIMITIVES_CONFIG_FILE`
- Save the contents to a file, e.g., `cli.toml`
- You can either copy this file to the default location
  `~/.config/pulp/cli.toml` or pass its path using the `--config`
  argument to the creation script.

### Creating a new Pulp service account

See [Pulp Access](../../documentation/operating/pulp-access.md) for instructions on creating
a new service account, configuring `cli.toml`, and storing credentials in the vault.

Then use it with the creation script via `--config ./cli.toml`.

## Create

Usage:

```bash
./create-pulp-resources.sh --domain <domain_name> [OPTIONS]
```

### Options

| Option | Description |
| --- | --- |
| `--domain <name>` | **Required.** Pulp domain to create or use. |
| `--repos <list>` | Comma-separated repository names. Defaults: RPM `source,x86_64,s390x,ppc64le,aarch64`; file `metadata,rpm-catalog`. |
| `--type <type>` | Repository plugin type (e.g. `rpm`, `file`). Default: `rpm`. |
| `--config <path>` | Path to the Pulp CLI config (e.g. `cli.toml`). If omitted, the CLI default (e.g. `~/.config/pulp/cli.toml`) is used. |
| `--retain-repo-versions <n>` | Sets Pulp `retain_repo_versions` to a non-negative integer. File repositories default to **1** if omitted. RPM repositories have no default; an explicit value is applied only when creating a new RPM repository. For an existing RPM repository with a different limit, setup stops to avoid unbounded pruning. |

> **Note:** If you re-run with `--type file` and omit
> `--retain-repo-versions`, the script applies the default **1**
> and will change existing file repositories whose current
> `retain_repo_versions` is not already `1`. Pass
> `--retain-repo-versions <n>` explicitly (including to match a
> custom value already on the server) to avoid an unintended
> reset.

Run `./create-pulp-resources.sh --help` for the full usage text from the script.

### Examples

Using default config `~/.config/pulp/cli.toml`, default type `rpm`, and default RPM repositories:

```bash
./create-pulp-resources.sh --domain public-hummingbird
```

Overriding the repositories to be created, still using default `rpm` type:

```bash
./create-pulp-resources.sh --domain public-hummingbird \
  --repos "source,x86_64,s390x,ppc64le,aarch64"
```

Creating `file` type repositories with default repos `metadata`
and `rpm-catalog`. Retention defaults to **1** repo version per
file repository (updated only if the server value is different):

```bash
./create-pulp-resources.sh --domain public-hummingbird --type file
```

Explicit file retention, for example keeping **10** repository versions:

```bash
./create-pulp-resources.sh --domain public-hummingbird \
  --type file --retain-repo-versions 10
```

For a new RPM repository, set a limit explicitly:

```bash
./create-pulp-resources.sh --domain example-domain \
  --repos new-repository --retain-repo-versions 100
```

For an existing RPM repository, changing the retention limit can prune many
versions during the update and hold the repository lock. Make that change in
bounded steps outside this setup script.

Using a specific config file:

```bash
./create-pulp-resources.sh --domain public-hummingbird \
  --config ./cli.toml
```

### Private-product download access

After creating the signed and unsigned private product domains and arranging the content guard
setup with the Pulp team, verify that their RPM distributions use the expected guard and that
authenticated downloads work. Follow
[Verify RPM download access](../../documentation/operating/private-rpm-repositories.md#verify-rpm-download-access)
for the verification steps.
