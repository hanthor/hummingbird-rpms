---
title: Monitoring RPM Signing Releases
weight: 56
---

Prepare the capture before a release rerun, then start it as soon as the new Release name is known,
before signing begins. The direct signing PipelineRun and pod can be pruned soon after completion,
and the high-level Release condition does not contain the failing step's logs. The monitor reads
both clusters; it does not rerun or change any resource.

## Start the capture

Connect to the VPN and log in to both clusters. Check the context names with `oc config get-contexts`.
For the current production setup, rh03 hosts the Hummingbird Release and managed PipelineRun;
`api.kflux-c-prd-i01.7hyu.p3.openshiftapps.com:443` hosts the direct signing PipelineRun in
`internal-services`. Always pass both contexts explicitly: the active `oc` context may be either
cluster.

```bash
release_name='PASTE_NEW_RELEASE_NAME_HERE'
python3 ci/monitor_signing.py "$release_name" \
  --rh03-context 'default/api-kflux-prd-rh03-nnv1-p1-openshiftapps-com:6443/shebert' \
  --signing-context 'default/api-kflux-c-prd-i01-7hyu-p3-openshiftapps-com:443/shebert' \
  --output "/tmp/rpm-signing-$release_name"
```

The monitor can also wait for a Release whose name is already known but has not appeared yet. It
polls every 10 seconds for up to three hours, then captures for another minute after the Release
completes. Use `--interval`, `--timeout`, `--post-completion`, or `--once` to change that behavior.
Keep it running in a separate terminal while the release processes. The evidence directory and
files have private permissions because PipelineRun parameters and logs may contain internal
artifact locations.

## How the resources connect

1. Get the Release from rh03 `hummingbird-tenant`. Its
   `status.managedProcessing.pipelineRun` is normally `rhtap-releng-tenant/managed-...`.
2. Read that managed PipelineRun's UID. Its signing InternalRequests in rh03
   `rhtap-releng-tenant` and the direct signing PipelineRuns on the **other cluster** share the
   label `internal-services.appstudio.openshift.io/pipelinerun-uid=<managed UID>`.
3. Direct PipelineRuns are in the signing cluster's `internal-services` namespace and have
   `tekton.dev/pipeline=direct-rpm-signing`. Their TaskRuns and pods have
   `tekton.dev/pipelineRun=<direct PipelineRun name>`.
4. Inspect the direct TaskRun condition and the pod's step container states, logs, resource
   limits, and events. The managed `rh-sign-rpm` task often exits 21 for several different
   downstream errors; the direct TaskRun usually identifies the actual step.

The monitor saves `release.json`, `managed-pipelinerun.json`, and `internalrequests.json` at the top
level. Each direct PipelineRun gets its own directory containing the PipelineRun, TaskRuns, events,
and pod directories. Each pod directory contains its status, events, available logs for download,
signing, trusted-artifact creation, merge, and upload, plus `metrics.jsonl` and `memory.jsonl`.
`metrics.jsonl` samples the Kubernetes metrics API; `memory.jsonl` reads cgroup v2
`memory.current` and `memory.peak` from running download, signing, and trusted-artifact containers.
Metrics can miss short spikes. A recorded cgroup peak is stronger evidence, but an OOM may kill the
container before its final peak is sampled.

## Read the result

```bash
jq '.status.conditions[] | select(.type == "Released")' "/tmp/rpm-signing-$release_name/release.json"
jq '.items[] | {name: .metadata.name, condition: .status.conditions[0]}' \
  /tmp/rpm-signing-"$release_name"/internalrequest-*/taskruns.json
jq '{containers: [.spec.containers[] | {name, resources}], states: .status.containerStatuses}' \
  /tmp/rpm-signing-"$release_name"/internalrequest-*/internalrequest-*-pod/pod.json
```

`StepOOM`, exit 137, and `OOMKilled` identify a memory-limit kill; inspect the **named step** and
its pod limit. `PipelineRunTimeout` means the direct PipelineRun exceeded its timeout; compare
step start and finish times to see where it spent the time. A download exit 1 alone does not
establish whether the artifact was absent, access failed, or the service had another error; read
`step-download.log` and pod events. Distinguish a signing failure from a later trusted-artifact,
merge, or upload failure before proposing a fix.

For a rerun, compare all direct PipelineRuns. The managed task may make several signing attempts
before it fails, and the Release may itself have multiple reruns. Record the Release name, managed
PipelineRun, direct PipelineRun, TaskRun, pod, exact step, exit reason, and timestamps in any issue.
