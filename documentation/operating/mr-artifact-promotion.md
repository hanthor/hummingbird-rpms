---
title: MR Artifact Promotion
weight: 25
aliases: [/l/mr-artifact-promotion-rpm]
---

## Overview

An RPM package releases the image built and tested in its merge request (MR) by default. Merging the
MR starts a promotion PipelineRun, not another RPM build. The promoted image keeps
the MR source revision and digest; the merge commit records why it was released.

The promotion flow, from the merge-commit parents through MR Snapshot lookup and
the push-style Component Snapshot to the automatic Release, is described in
[MR artifact promotion](https://hummingbird-project.io/l/mr-artifact-promotion).
On the RPM side, the MR build produces a digest-pinned OCI image containing
unsigned RPMs, required Testing Farm tests gate the merge, and Release Service
adds duplicate-RPM filtering and Pulp publication to the managed Conforma,
signing, and publication checks.

## Prerequisites

- The package uses two-parent merge commits on `main`, and its MR build and required tests passed.
- The Component's build ServiceAccount can push source Trusted Artifacts to its Quay image repository
  and use the pipeline SCC. The `hummingbird-promotion` ServiceAccount can read build evidence and
  create Snapshots; it must be able to read the source Trusted Artifact.
- The Hummingbird tools image contains both promotion commands and is pinned by digest. The
  [Hummingbird Tools Task reference][tools-tasks] describes their inputs and validation.
- The MR Snapshot, linked PipelineRun, and image remain available through merge and retry. KubeArchive
  can supply removed Konflux objects but cannot restore an expired image; check the actual Quay
  expiry for the selected digest and its tags and do not assume a fixed retention period.

[tools-tasks]: https://gitlab.com/redhat/hummingbird/tools/-/blob/main/documentation/hummingbird-tools.md

## Choose the push pipeline

Imported packages use promotion by default. Set `push_pipeline: build` in
`ci/package-overrides.yaml` only for a documented exception, then run `make generate`. The generator
places each package's push-triggered PipelineRun in `.tekton/rpms-on-promotion.yaml` or
`.tekton/rpms-on-push.yaml` accordingly. Every package remains in `trigger-manifest-push.json` (see
[trigger manifests documentation](https://hummingbird-project.io/l/trigger-manifests)) so
its pushes reach Pipelines as Code. Remove the `build` override to restore promotion.

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
`hummingbird-promotion`.

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
