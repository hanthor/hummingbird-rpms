---
title: MR Artifact Promotion
weight: 25
---

# MR artifact promotion

## Overview

An RPM package can use its tested MR artifact after merge instead of building again on the push. The push PipelineRun reads the merge commit's second Git parent, finds the passed MR Snapshot and image, and creates one push-style Component Snapshot. Integration Service then creates the automatic Release; the managed Release pipeline publishes the RPMs.

`syft` is the first package configured for this path. Its MR build remains enabled.

## Prerequisites

- The package uses two-parent merge commits on `main`, and its MR build and required tests have passed.
- The `hummingbird-promotion` ServiceAccount can read build evidence, create Snapshots, and write source Trusted Artifacts to the configured Quay repository.
- The promotion Tasks in Hummingbird tools accept a source Trusted Artifact. Their image must contain both promotion commands.
- The MR Snapshot, linked PipelineRun, and image remain available through the merge and retry window. KubeArchive can supply removed Konflux objects, but it cannot restore an expired image.

## Enable a package

Set `push_pipeline: promotion` for the package in `ci/package-overrides.yaml`, then run `make generate`. The generator removes that package's push build from `.tekton/rpms-on-push.yaml` and adds its push-triggered PipelineRun to `.tekton/rpms-on-promotion.yaml`. The package remains in `trigger-manifest-push.json` so its pushes reach Pipelines as Code.

Review the generated YAML and confirm the promotion tools image digest is current. Keep the MR build in `.tekton/rpms-on-pull-request.yaml`. A package merge must start one promotion run and no push-build run.

## Run and recovery

The Component Snapshot uses the promotion PipelineRun start time for Integration Service's existing release-order check. RPM promotion does not update the Global Candidate List.

The promotion run clones the merge commit with full history into a source Trusted Artifact, then runs the lookup and Component Snapshot Tasks. Its `COMPONENT_SNAPSHOT` result identifies the created or reused Snapshot. Check the Snapshot's Component and push labels, promotion start-time annotation, and Integration Service-created Release. The promotion pipeline does not rebuild or create a Release itself.

Retries reuse a matching live Component Snapshot. If the tested MR evidence is unavailable or the image has expired, use an explicit new build and required tests before release. If a Release fails after the Snapshot succeeds, recover through the release workflow rather than creating another Snapshot.

## Development

Run `python3 ci/generate_resources.py all` and `pytest test/test_generate_resources.py`. Review the generated push, promotion, and MR PipelineRuns for the package.
