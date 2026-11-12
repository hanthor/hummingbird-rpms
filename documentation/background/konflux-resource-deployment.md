---
title: Konflux Resource Deployment
description: How Konflux resources are defined and deployed across repositories
weight: 20
---

Konflux resources for RPM packages are split between two repositories:

- **[rpms]**: Per-package resources generated from package directories
- **[infrastructure]**: Application-level resources and deployment to the Konflux cluster

[rpms]: https://gitlab.com/redhat/hummingbird/rpms
[infrastructure]: https://gitlab.com/redhat/hummingbird/infrastructure

This separation allows independent iteration on each concern.

## Deployment Matrix

| Resource Type            | Source         | RPMs MR | RPMs Push | Infra MR | Infra Push |
| ------------------------ | -------------- | ------- | --------- | -------- | ---------- |
| Component                | rpms           | manual  | manual    | -        | -          |
| ImageRepository          | rpms           | manual  | manual    | -        | -          |
| ReleasePlanAdmission     | rpms           | -       | automatic | manual   | automatic  |
| EnterpriseContractPolicy | rpms           | -       | automatic | manual   | automatic  |
| IntegrationTestScenario  | rpms           | -       | automatic | -        | -          |
| Application              | infrastructure | -       | -         | manual   | automatic  |
| ReleasePlan              | infrastructure | -       | -         | manual   | automatic  |
| ServiceAccount, Secret   | infrastructure | -       | -         | manual   | automatic  |

**Legend**:

- **RPMs MR**: Downstream pipeline triggered from rpms MR (deploys from MR commit)
- **RPMs Push**: Downstream pipeline triggered from rpms push to main
- **Infra MR**: Infrastructure MR pipeline (manual trigger)
- **Infra Push**: Infrastructure push to main or web pipeline

## Resource Locations and Rationale

### RPMs Repo

Resources are defined in `konflux-templates/` and rendered to `konflux-templates/rendered.yml`.

**Component and ImageRepository**:

1. **Ownership**: Components are managed by the rpms repo. The infrastructure pipeline uses
   `ONLY_DOWNSTREAM` so only explicit downstream triggers from rpms deploy changes, giving the rpms
   repo full control over the component lifecycle.
2. **Dynamic generation**: Generated from `rpms/*/` directories via
   `ci/generate_konflux_resources.sh`, depending on package presence and configuration.
3. **Timing**: Must be deployed early during MR review so Konflux can build and test new packages.

**ReleasePlanAdmission**:

1. **Static configuration**: Unlike containers (where RPA contains per-image tag mappings), the RPMs
   ReleasePlanAdmission is static—it configures the Pulp publishing pipeline without per-package
   data. It stays in the rpms repo for consistency with the containers pattern.
2. **Main branch only**: Deployed only on push to main to maintain deployment consistency.

**EnterpriseContractPolicy**:

1. **Consistency**: Follows the containers repo pattern of keeping policy definitions alongside the
   resources they govern.

### RPMs Repo — IntegrationTestScenario

**IntegrationTestScenario** resources are defined in `konflux-templates/scenarios.yml`
(the single config source — apps, scenario types, bundle refs, and Renovate markers) and
rendered into `konflux-templates/rendered.yml` alongside Components and ImageRepositories.
The same config drives the trigger-manifest `integration_test_scenarios` section, so the
manifest cannot drift from deployed ITS.

**Deploy path**: ITS is deployed by the `*-releases` downstream pipeline (not `*-components`).
The releases sync fetches `rendered.yml` from the **main** branch and runs on every merge to
main, so ITS changes deploy automatically after merge — including Renovate bundle-digest and
`IMAGE_TAG` bumps. The components sync is deliberately excluded: it deploys from
`PARENT_COMMIT_SHA` (MR heads), and ITS is static repo-level config that is not supposed to
change in an MR — concurrent or stale MR pipelines would otherwise reset the live CR to older
versions. Pre-merge ITS deployment is intentionally unavailable by design.

**No-prune behaviour**: The deploy uses server-side apply without pruning — resources
removed or renamed in `rendered.yml` are **not** deleted from the cluster. A renamed or
removed ITS remains live and continues producing test PipelineRuns until deleted manually:

```shell
kubectl delete integrationtestscenario <name> -n hummingbird-tenant
```

See the infrastructure repo's
[kubernetes-rendering.md](https://gitlab.com/redhat/hummingbird/infrastructure/-/blob/main/documentation/operating/kubernetes-rendering.md)
"No pruning" section for details.

The `integration_test_scenarios` section of `.tekton/trigger-manifest-pull-request.json`
is derived from the same `scenarios.yml` config, ensuring the manifest never drifts from
deployed scenarios.

### Infrastructure Repo

**Application and ReleasePlan** are defined in `kubernetes/rpms-main/`:

1. **Independent iteration**: Changes are decoupled from rpms repo activity—they can be modified,
   test-deployed via manual trigger in an infrastructure MR, verified, and merged without touching
   the rpms repo.
2. **Static configuration**: These resources don't depend on per-package data.

**ServiceAccount and Secret** for releases are defined in `kubernetes/setup-konflux/`:

1. **Security**: Secret specifications (names, structure, credential references) should not be
   exposed in the rpms repo.
2. **Independent iteration**: Like other infrastructure resources, these can be modified and
   test-deployed without touching the rpms repo.

The service account is referenced by ReleasePlanAdmission to authorize publishing RPMs to Pulp.
