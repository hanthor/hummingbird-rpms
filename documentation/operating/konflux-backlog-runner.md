---
title: Konflux Backlog Runner
description: Retrigger selected merge requests in bounded batches
---

The runner processes an ordered list of existing RPMs and containers merge requests (MRs). It
posts a Pipelines as Code `/test` comment for the current MR SHA, waits for a matching build
PipelineRun, and starts the next MR only when capacity is available. It does not approve or merge
by default. Use it after the MR-creating schedules have been paused and Konflux can accept new
runs.

## Queue and commands

Edit `config/konflux-backlog-queue.json` to contain MR references in priority order. The team can
also supply another file with `--queue`.

```json
[
  "rpms!5679",
  "https://gitlab.com/redhat/hummingbird/containers/-/merge_requests/22252"
]
```

For `--arm-auto-merge`, give every MR the expected PipelineRun template names from its
repository's `.tekton/*-on-pull-request.yaml`:

```json
[
  {"mr": "rpms!1234", "expected_builds": ["example-main-on-pull-request"]}
]
```

From the repository root, use an authenticated `glab` and a rh03 `kubectl` context. Preview the
queue first; the runner defaults to dry run. Start with one in-flight MR for the pilot:

```bash
python3 ci/konflux_backlog_runner.py \
  --context hummingbird-tenant/api-kflux-prd-rh03-nnv1-p1-openshiftapps-com:6443/shebert
python3 ci/konflux_backlog_runner.py \
  --context hummingbird-tenant/api-kflux-prd-rh03-nnv1-p1-openshiftapps-com:6443/shebert \
  --max-in-flight 1 --apply --watch
```

Use `--apply` without `--watch` for one scheduling pass. Re-running is safe: the command comment
contains the MR and SHA, and the runner does not post another command for that SHA. The runner
counts active PipelineRuns across the tenant, plus commands waiting for PaC admission. The limit
is on concurrent MR attempts: one MR can create several PipelineRuns, so it is not a hard cap on
PipelineRun count. A command with no matching PipelineRun after 15 minutes stalls the queue.
Investigate it before retrying;
do not remove the comment just to bypass the hold. The run must have the same repository, MR
number, and SHA, and must have been created after the command comment. Completed runs settle for
two minutes before the slot is released. Failed runs are reported and are not retried
automatically. An MR that closes without a verified run also stops the queue; check whether a
workqueue item or a person merged it.

After the pilot, add `--arm-auto-merge` to the `--apply` command if selected MRs should merge
automatically. The runner waits for every expected build, at least one fresh successful Konflux
status, all observed Konflux and Testing Farm statuses to be fresh and successful,
successful GitLab CI, approval, resolved discussions, and
GitLab mergeability. It then requests GitLab auto-merge with the checked SHA. **GitLab may merge
immediately** if all conditions are already met. The runner never arms auto-merge for an MR
outside the queue. Without this option, the operator reviews results and enables auto-merge
separately.

The runner uses the [PaC `/test` command](https://docs.pipelinesascode.com/v0.53.0/docs/guides/gitops-commands/)
to force a fresh run even if a previous check passed. Validate on one MR that this command
produces every expected build PipelineRun before increasing `--max-in-flight`. Confirm the
repository's required statuses are represented in GitLab before using `--arm-auto-merge`.
Existing workqueue items can still merge independently.

The queue file is under `config/`, outside the RPM PaC watched paths. Changing only this file
does not match an RPM or `setup` PipelineRun; normal GitLab validation can still run. Verify this
with the first queue-only MR before using the file as the team's shared priority list.
