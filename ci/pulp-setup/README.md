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

The retention stepper below also needs Python 3.11 or newer and `requests`:

```bash
python3 -m pip install requests
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
| `--retain-repo-versions <n>` | Sets Pulp `retain_repo_versions` to a non-negative integer. File repositories default to **1** if omitted. RPM repositories have no default; an explicit value is applied only when creating a new RPM repository. For an existing RPM repository with a different limit, the script stops and directs you to the bounded retention tool below. |

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

For a new RPM repository, you can set a limit explicitly. Existing RPM
repositories need the bounded retention tool below to avoid a large prune in
one update:

```bash
./create-pulp-resources.sh --domain example-domain \
  --repos new-repository --retain-repo-versions 100
```

Using a specific config file:

```bash
./create-pulp-resources.sh --domain public-hummingbird \
  --config ./cli.toml
```

## Reduce active RPM repository-version retention

Tracking: [HUM-11621](https://redhat.atlassian.net/browse/HUM-11621).

`step_repository_retention.py` makes one bounded reduction per active RPM
repository toward a target of 100 retained repository versions. It uses the
`retain_repo_versions` field; it does not change `retain_package_versions`.
Its default repositories are `source,x86_64,aarch64,s390x,ppc64le` in the
`public-hummingbird` domain. Snapshot and file repositories are excluded.
When `--repos` is omitted, those five repositories run concurrently with
separate Pulp clients. An explicit `--repos` list runs sequentially.

For one step, the first command previews the next limit; the second performs
at most one repository update per repository. By default, Pulp prunes old
versions as part of that update. `--step N` requests at most N old versions to
be removed, calculated from the current version count rather than the previous
limit. For example, with 1,130 versions, one protected latest version, and
`--step 20`, the next limit is 1,109 and the expected count afterward is 1,110.
The `--target` value is a floor for the retention limit, so the final step may
remove fewer than N versions.

```bash
python3 ci/pulp-setup/step_repository_retention.py
python3 ci/pulp-setup/step_repository_retention.py --apply
```

To keep working toward the target, run watch mode in a persistent terminal:

```bash
python3 ci/pulp-setup/step_repository_retention.py \
  --config ~/.config/pulp/cli.toml \
  --domain hummingbird-unsigned \
  --target 100 --step 20 \
  --watch --apply
```

Watch mode checks every 60 seconds by default (`--poll-seconds` changes this).
It takes one count-based step for an eligible repository only when that
repository has no waiting, running, or canceling Pulp tasks. With the default
repository set, each repository has its own polling schedule, so a slow update
does not hold up steps on the others. An explicit `--repos` list uses the
sequential watch loop. Watch mode has no
cooldown by default, so it can keep stepping while repositories are idle. Pass
`--cooldown-hours` to pause between steps. It skips busy repositories and
retries them later; completed repositories leave the watch list. It exits
successfully when every retention limit reaches the target. A repository with
a pending label or safety mismatch is blocked for operator review while watch
mode continues with the others. Watch rechecks blocked repositories each poll
and automatically reconciles a pending direct update when it can identify its
unique failed `PLP0024` task and verify the intended limit and version count.
Ambiguous or mismatched steps stay blocked for operator review. Watch keeps
running even if all remaining repositories are blocked. The periodic status includes
the names of blocked repositories. A task can still be submitted between the
queue check and update dispatch; the script cannot assign task priority inside
Pulp or preempt an update already running. Watch mode requires `--apply` and
uses direct retention updates by default.

The one-pass defaults are `--step 10`, `--target 100`, and `--cooldown-hours 24`.
Pass `--cooldown-hours 0` to allow a one-pass step immediately after a previous
completed step; it still performs at most one step per repository per run.
Choose `--step` after measuring an update duration. The script reads
`~/.config/pulp/cli.toml` by default; pass `--config` for
another local Pulp CLI config. Timestamped progress shows the planned count,
the update task ID and URL, and the verified final count. After verifying the
count, it prints versions removed, Pulp's update-task runtime, seconds per
version, versions per second, and the local wait. Task runtime covers the whole
repository update, so the per-version figures are estimates rather than
delete-only timings. The completed label records `pruned_count`,
`pulp_task_seconds` when Pulp supplies task timestamps, and
`update_wait_seconds`. Progress goes to standard error; the per-repository
summary goes to standard output.
The CLI dispatches the update in the background; the script polls its task ID
with bounded network requests. If connectivity changes, it resumes checking
the same task when the connection returns and does not send a second update.
A task still waiting or running produces a status message every 30 seconds.
If CLI dispatch stalls for 60 seconds before reporting a task ID, the script
looks for exactly one repository update task created after the pending label
was set and verifies that it reserved this repository. It then polls that task
and verifies the resulting limit and version count. If the task cannot be
identified unambiguously, it leaves the pending label for operator review.
For repositories that already have a finite limit but have never used this
script, use `--adopt-existing` on the first run after checking that no earlier
cleanup task is still active. This explicitly permits a missing
`retention_step` label; later runs do not need it.
If the update wait exceeds 30 minutes, the script finishes verification and
records completion, then a one-pass run stops before the remaining
repositories; watch mode continues with them. Change this cutoff with
`--max-wait-seconds`. It cannot interrupt a running Pulp task.

The planner assumes one protected latest version. Before a step that will
prune, it checks for one distribution following the repository and a latest
publication of the latest version; direct or checkpoint distribution references
require manual review. The update is then expected to leave the calculated
version count. An unexplained difference leaves the pending label for review.
The old worker-delete strategy remains available with `--worker-predelete`.
It deletes each planned old version in a separate worker task before lowering
the limit, including in watch mode if explicitly selected. A new
repository already below its planned limit can set that limit without a
publication because no deletion is needed.

Before changing retention, the script sets a `retention_step` label to
`pending`. After verifying the new limit and version count, it changes the
label to `completed` with the previous and new limits, planned prune count,
before and after counts, and UTC completion time. A later run skips that
repository until its cooldown has elapsed. If the label remains pending, is
missing after a finite limit was set, or disagrees with the current limit,
the script stops without making another change. An operator must reconcile
an interrupted or unverified run before continuing.
Pulp does not enforce the label; the script is the gate.

If a legacy `--worker-predelete` batch stopped after some worker deletes,
verify the partial step and close its pending label with:

```bash
python3 ci/pulp-setup/step_repository_retention.py \
  --config ~/.config/pulp/cli.toml \
  --domain hummingbird-unsigned --repos source --reconcile-predelete
python3 ci/pulp-setup/step_repository_retention.py \
  --config ~/.config/pulp/cli.toml \
  --domain hummingbird-unsigned --repos source --reconcile-predelete --apply
```

The reconciliation requires the planned deletes to form the missing oldest
version prefix, or confirms that no planned delete ran yet. It accounts for
newly appended versions, checks that no repository tasks are active, and
confirms the latest publication. It records the deletes already made and leaves
the retention limit at its previous value. The next ordinary run can reduce
the limit after its cooldown.

Pulp may report `PLP0024` after an immediate update has already set the limit
and pruned versions. The script checks the failed task, current limit, expected
version count, repository task queue, and pending label before automatically
recording a verified completion. If any check fails, it leaves the label pending
for manual review; it does not resend the update. Reducing `--step` does not
change Pulp's five-second immediate-task limit. A 1141-to-1000 update of
`hummingbird-unsigned/source` pruned 129 versions in a 34.2-second task that
Pulp nevertheless marked failed with `PLP0024`. Pulp's `TASK_PREFER_DEFER`
server setting can avoid this timeout but is not controlled by this script.
When that failed task is reconciled, its recorded runtime and the verified net
version reduction are printed and saved in the completed label.

To reconcile a timed-out limit update, first inspect the failed task, retained
versions, and any versions protected by distributions. Then run the recovery
mode with exactly one repository, that task's UUID, and the verified version
counts. The first command previews the label change; `--apply` writes it:

```bash
python3 ci/pulp-setup/step_repository_retention.py \
  --config ~/.config/pulp/cli.toml \
  --domain konflux-release-integration-tests --repos source --target 5 \
  --reconcile-pending --task-uuid <failed-task-uuid> \
  --expected-count <verified-version-count>
# Repeat the command with --apply after reviewing the preview.
```

The manual recovery mode accepts only a failed `PLP0024` repository-update task that
reserved this repository, was created after the pending marker, and is still
its latest task. It also checks for active tasks and verifies the current
limit, label, and version count again before writing. The resulting
`completed` label records that the Pulp task failed and was reconciled. New
pending labels include their initial version count, so
`--before-count` is only needed for older labels. This changes only the
label; it does not repeat cleanup. A same-value repository update would not
run cleanup because Pulp's hook runs only when the limit changes.

The first reduction from an unlimited repository sets the limit to the current
count minus one protected version minus `--step`, down to `--target`; it aims to
prune at most `--step` existing versions. Always review the preview and the
completed task duration before increasing the step size. This script does not
install a cron job or change Pulp until `--apply` is supplied.

For the five active RPM repositories in the integration-test domain, a
read-only preview toward a target limit of 5 is:

```bash
python3 ci/pulp-setup/step_repository_retention.py \
  --config ~/.config/pulp/cli.toml \
  --domain konflux-release-integration-tests \
  --target 5 --adopt-existing
```
