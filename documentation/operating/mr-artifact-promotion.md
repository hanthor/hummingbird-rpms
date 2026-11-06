---
title: MR Artifact Promotion
weight: 25
aliases: [/l/mr-artifact-promotion]
---

## Overview

An RPM package configured for promotion releases the image built and tested in its merge request
(MR). Merging the MR starts a promotion PipelineRun, not another RPM build. The promoted image keeps
the MR source revision and digest; the merge commit records why it was released.

The path is:

1. The MR build produces a digest-pinned OCI image containing unsigned RPMs. Required Testing Farm
   tests pass, and Konflux records the image in an MR Component Snapshot.
2. A two-parent merge commit `M` lands on `main`. Its second parent `B` is the tested MR head. The
   push-triggered promotion PipelineRun clones `M`, finds the passed MR Snapshot and its linked build,
   and verifies that they identify `B` and the same image digest `D`.
3. The promotion Task creates one push-style Component Snapshot with source `B` and image `D`. It
   records `M`, the MR Snapshot, build PipelineRun, and promotion PipelineRun in annotations. The
   promotion PipelineRun does not build RPMs or create a Release.
4. Integration Service processes the Component Snapshot without another integration test and
   creates the automatic Release. Release Service runs the existing managed pipeline, including
   Conforma, duplicate-RPM filtering, signing, and Pulp publication.

`syft` was the first package on this path. Check `.tekton/rpms-on-promotion.yaml` for the current
package set; packages absent from it continue to build on push. The earlier PR Snapshot Promotion
PoC was an experiment; its scripts and staging manifests are not the production workflow.

## Prerequisites

- The package uses two-parent merge commits on `main`, and its MR build and required tests passed.
- The Component's build ServiceAccount can push source Trusted Artifacts to its Quay image repository
  and use the pipeline SCC. The `hummingbird-promotion` ServiceAccount can read build evidence and
  create Snapshots; it must be able to read the source Trusted Artifact.
- The Hummingbird tools image contains both promotion commands and is pinned by digest. The
  [Hummingbird Tools Task reference][tools-tasks] describes their inputs and validation.
- The MR Snapshot, linked PipelineRun, and image remain available through merge and retry. KubeArchive
  can supply removed Konflux objects, but cannot restore an expired image. Check the actual Quay
  expiry for the selected digest and its tags; do not assume a fixed retention period.

[tools-tasks]: https://gitlab.com/redhat/hummingbird/tools/-/blob/main/documentation/hummingbird-tools.md

## Enable a package

Set `push_pipeline: promotion` for the package in `ci/package-overrides.yaml`, then run
`make generate`. The generator removes the package's push build from `.tekton/rpms-on-push.yaml` and
adds its push-triggered PipelineRun to `.tekton/rpms-on-promotion.yaml`. The package remains in
`trigger-manifest-push.json` so its pushes reach Pipelines as Code.

Review the generated YAML and confirm the Hummingbird tools image digest is current. Keep the MR
build in `.tekton/rpms-on-pull-request.yaml`. A package merge should start one promotion run and no
push-build run.

## Verify a promotion

Find these resources in the tenant's Konflux UI or with `oc`. Follow their names and links rather
than treating the promotion run as a build:

```bash
oc -n hummingbird-tenant get pipelineruns -l appstudio.openshift.io/component=syft-main
oc -n hummingbird-tenant get snapshots -l appstudio.openshift.io/component=syft-main
oc -n hummingbird-tenant get releases -o custom-columns=NAME:.metadata.name,SNAPSHOT:.spec.snapshot
```

Select the promotion PipelineRun for merge commit `M`; its `COMPONENT_SNAPSHOT` result names the
created Snapshot. Its annotations name the tested MR Snapshot and build PipelineRun. Find the
Release whose `spec.snapshot` matches, then follow its managed PipelineRun.

| Resource                     | Check                                                                                                                                                                                                                                                            |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| MR build PipelineRun         | `type=build`, MR event, successful build result, source revision `B`, and digest `D`.                                                                                                                                                                            |
| Tested MR Snapshot           | The package's Component entry has source `B` and image `D`; required MR tests passed.                                                                                                                                                                            |
| Promotion PipelineRun        | `type=promotion`, merge revision `M`, three Tasks (`clone-repository`, `find-tested-mr-artifact`, `create-promotion-component`), and a `COMPONENT_SNAPSHOT` result.                                                                                              |
| Promotion Component Snapshot | One Component entry with the same source `B` and image `D`; `type=component`, push event and Component labels; annotations link `M` and the source evidence. Its start-time annotation comes from the promotion PipelineRun.                                     |
| Automatic Release            | `spec.snapshot` names the promotion Snapshot. Check its conditions and managed PipelineRun; the promotion Task did not create it.                                                                                                                                |
| Managed release              | Confirm Conforma and Pulp Tasks succeeded. Compare signed RPM SHA256 values in the Release's `status.artifacts.rpmfiles` with matching Pulp package records. Signing changes RPM bytes, so these hashes need not equal the unsigned RPM hashes inside image `D`. |

The Component Snapshot normally reports that no required IntegrationTestScenarios were found and
testing was skipped. The promotion Task fails before Snapshot creation if a configured scenario
could retest it. Verify that the MR image digest and the Component Snapshot digest are identical;
this proves promotion reused the tested image. Look up the merge SHA in Konflux PipelineRuns to
confirm that no push build ran for this Component.

The clone Task runs under the Component build ServiceAccount and stores the source Trusted Artifact
in the Component's Quay repository. Lookup and Snapshot creation run under
`hummingbird-promotion`. The Snapshot's `test.appstudio.openshift.io/pipelinerunstarttime` annotation
is the promotion run's start time for Integration Service's supersession check. The RPM application
has no group tests consuming the Global Candidate List, so this path needs no separate override
Snapshot or reducer change.

## Recover a failed run

| Failure                                                  | Action                                                                                                                                                                                                                                                        |
| -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Clone cannot push or restore the source Trusted Artifact | Check the Component build ServiceAccount, its Quay access, and the `hummingbird-promotion` read path. The clone Task needs the pipeline SCC.                                                                                                                  |
| Lookup cannot find passed MR evidence                    | Check the MR Snapshot, its build PipelineRun, required test result, and KubeArchive access. If the evidence cannot be recovered, build and test again before release.                                                                                         |
| Package source changed on `main` after `M`               | Stop the stale promotion. Use a fresh MR build and tests for the newer package inputs.                                                                                                                                                                        |
| Tested image expired or cannot be pulled                 | Do not create a Snapshot with a missing image. Rebuild and run the required tests before release.                                                                                                                                                             |
| Snapshot creation hits quota or finds a conflict         | Check the tenant quota or the existing deterministic Snapshot. A retry reuses only a matching live Snapshot; it does not reuse an archived or conflicting one.                                                                                                |
| Promotion Task or PipelineRun needs a fix                | Start a new promotion PipelineRun for the original merge SHA using the corrected manifest and Task pins. An old PipelineRun keeps its old Task revisions, and a garbage-collected run cannot be rerun. Do not bump the RPM Release solely to retry promotion. |
| Release fails after the Component Snapshot exists        | Recover the failed Release through the Release Service workflow using that Snapshot. Do not create another promotion Snapshot or rebuild RPMs to diagnose a release-service failure.                                                                          |

Record the MR Snapshot, tested digest, promotion PipelineRun and Component Snapshot, automatic
Release, managed PipelineRun, Conforma result, and Pulp RPM checksums in the work item. Keep a compact
evidence attachment when Konflux resources may be garbage-collected. An automatic Release can fail
and later succeed through a retry using the same Snapshot; link both outcomes.

## Development

Run `python3 ci/generate_resources.py all` and `pytest test/test_generate_resources.py`. Review the
generated push, promotion, and MR PipelineRuns for the package.
