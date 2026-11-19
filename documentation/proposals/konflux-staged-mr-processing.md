---
title: Staged Konflux MR Processing
description: Proposal for processing RPM and container merge requests in bounded batches
---

**Status:** Proposed · **Tracking:** [HUM-13088](https://redhat.atlassian.net/browse/HUM-13088)

## Decision

Use existing open MRs as the work queue. Pause schedules that create new update MRs, then
retrigger selected MRs in bounded batches. Restore the schedules when the pending backlog falls
below an agreed threshold. This uses existing PaC definitions and avoids changing package build
triggers.

This plan assumes priority work is already represented by open MRs. If a priority RPM has no MR,
create one through the existing package workflow and add it to the queue. Pausing schedules does
not block manually opened or updated MRs, existing workqueue items, or post-merge runs. If all
other package activity must be suppressed, a separate PaC gate is still required.

## Queue and safety controls

Manage the MR-creating schedules in the [infrastructure GitLab repo config][schedule-config]. Set
`active: false` for RPM `dist_git_update` and `upstream_version_update` and containers
`lockfile_update`. Leave unrelated schedules, such as containers `metrics_report`, active. Deploy
the config change through the infrastructure pipeline and record the prior values for restoration.

Inventory open MRs with their repository, package or image, current SHA, last Konflux result, and
auto-merge state. Cancel GitLab auto-merge already armed on deferred MRs; restore it only for a
selected MR after verifying a fresh build and required tests for its current SHA. Leave the
workqueue running. Pausing its scheduled feeders stops routine new submissions and updates, but
existing work items can still reconcile and merge when GitLab marks their MRs mergeable. Monitor
those merges during the rollout; a strict no-unselected-merge guarantee would need a separate gate.

A small queue runner accepts an ordered list of MR IDs and a maximum number of in-flight
PipelineRuns. It offers a dry run, rechecks each MR's current SHA before triggering, records each
attempt through its GitLab command comment and matching PipelineRun, and starts the next MR only
when capacity is free. No shared selection record is needed. Use a new `/retest`
comment for a missing or failed MR PipelineRun. If an earlier successful status would make
`/retest` skip the run, use a named `/test <pipelinerun-name>` to force it. [PaC documents these
commands][pac-commands]. Its `comment_strategy: disable_all` setting controls outgoing status
comments; validate command handling and the package path match with one GitLab MR before batching.

Count only a PipelineRun whose MR number and revision match the selected MR and SHA and whose
creation time follows the trigger. If priorities are stored in a repository file, keep it outside
`ci/`, `mock/`, `rpms/`, and `test/rpms/`. Verify config-only changes trigger no RPM or `setup` PaC
run or required Konflux check. Normal GitLab validation stays enabled.

## Rollout

1. Set and deploy only the three MR-creating schedule flags to inactive in the infrastructure
   config. Verify those jobs stop submitting work; leave the workqueue service running.
2. Agree on an in-flight limit and backlog threshold. Inventory and prioritize open MRs, cancel
   GitLab auto-merge on deferred MRs, then test retriggering and status tracking on one MR.
3. Process selected MRs in batches. Check the new PipelineRun, required tests, and current SHA
   before releasing the next slot or restoring GitLab auto-merge. Watch for unexpected workqueue
   merges. Create and queue a priority MR if needed.
4. When the pending backlog stays below the threshold and PipelineRun load is stable, restore the
   recorded schedule flags through infrastructure config changes one at a time. Verify new MRs
   enter the same controlled path.

## Acceptance checks

- Only the three MR-creating schedules are paused and restored through infrastructure config;
  unrelated schedules and the workqueue keep running. Those jobs stop submitting work.
- The runner supports dry run and bounded concurrency using GitLab comments and matching
  PipelineRuns as attempt history. A pilot proves `/retest` and named `/test` create the intended run.
- GitLab auto-merge is canceled on deferred MRs and restored only after a selected MR's current SHA
  has a new build and required tests. Existing workqueue items and unexpected merges are monitored.
- A priority package without an MR has a documented creation and queueing path. The plan states
  how manual MR activity and post-merge runs are handled.
- Queue-config changes, if any, create no Konflux run or check; normal GitLab checks still run.

[pac-commands]: https://docs.pipelinesascode.com/v0.53.0/docs/guides/gitops-commands/
[schedule-config]: https://gitlab.com/redhat/hummingbird/infrastructure/-/blob/main/gitlab-repo-config/hummingbird.yml.j2
