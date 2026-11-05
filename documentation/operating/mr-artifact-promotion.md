---
title: MR Artifact Promotion
weight: 25
---

## Overview

An RPM package can release its tested MR artifact after merge without rebuilding it. The promotion
PipelineRun reads the merge commit's second Git parent, finds the passed MR Snapshot and image, and
creates one push-style Component Snapshot. Integration Service creates the automatic Release; the
managed release pipeline publishes the RPMs.

`syft` is the first package configured for this path. Its MR build remains enabled.

## Prerequisites

- The package uses two-parent merge commits on `main`, and its MR build and required tests passed.
- The `hummingbird-promotion` ServiceAccount can read build evidence, create Snapshots, and write
  source Trusted Artifacts to the configured Quay repository.
- The Hummingbird tools image contains both promotion commands and accepts a source Trusted Artifact.
- The MR Snapshot, linked PipelineRun, and image remain available through merge and retry. KubeArchive
  can supply removed Konflux objects, but cannot restore an expired image.

## Enable a package

Set `push_pipeline: promotion` for the package in `ci/package-overrides.yaml`, then run
`make generate`. The generator removes the package's push build from `.tekton/rpms-on-push.yaml` and
adds its push-triggered PipelineRun to `.tekton/rpms-on-promotion.yaml`. The package remains in
`trigger-manifest-push.json` so its pushes reach Pipelines as Code.

Review the generated YAML and confirm the Hummingbird tools image digest is current. Keep the MR
build in `.tekton/rpms-on-pull-request.yaml`. A package merge should start one promotion run and no
push-build run.

## Run and recovery

The promotion run clones the merge commit with full history into a source Trusted Artifact, then runs
the lookup and Component Snapshot Tasks. Its `COMPONENT_SNAPSHOT` result names the created or reused
Snapshot. The Snapshot records the promotion PipelineRun start time for Integration Service's existing
release-order check. The pipeline does not rebuild or create a Release.

Check the Snapshot's Component and push labels, start-time annotation, and Integration Service-created
Release. The RPM application has no group tests that consume the Global Candidate List, so this phase
needs no separate override Snapshot or reducer change.

A retry reuses a matching live Component Snapshot. If MR evidence is unavailable or the image has
expired, use an explicit new build and required tests before release. Recover a failed Release through
the release workflow without creating another Snapshot.

## Development

Run `python3 ci/generate_resources.py all` and `pytest test/test_generate_resources.py`. Review the
generated push, promotion, and MR PipelineRuns for the package.
