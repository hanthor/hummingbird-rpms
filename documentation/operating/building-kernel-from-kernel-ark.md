---
title: Building the Kernel from kernel-ark
description: Import a pinned Fedora kernel source package without using Fedora dist-git
weight: 46
---

Hummingbird takes the kernel packaging directly from
[kernel-ark](https://gitlab.com/cki-project/kernel-ark). Fedora's kernel dist-git is generated from
kernel-ark, so it is not an input to this operation.

## Import a release

Choose an immutable kernel-ark release tag or a full 40-character commit SHA. Release tags start
with `kernel-`.

```bash
./ci/dist_git.py import-kernel-ark --ref kernel-7.2.2-0
```

Run the command in `limactl shell fedora` or in the repository development container. The command
needs the kernel-ark source-RPM build dependencies.

Install the kernel-ark generation dependencies before the first import:

```bash
sudo dnf install git make gcc flex bison bzip2 rpm-build python3-pyyaml patchutils
```

Use `--dry-run` to generate and inspect the package without creating a commit:

```bash
./ci/dist_git.py --dry-run import-kernel-ark --ref kernel-7.2.2-0
```

The command performs these operations:

1. Fetches only the requested kernel-ark history and resolves it to a commit.
2. Runs `make dist-git` in kernel-ark with a temporary local rpkg adapter.
3. Copies the generated spec, configuration, scripts, and source archives into `rpms/kernel/`.
4. Disables kernel-ark's normal lookaside upload step.
5. Writes `metadata/kernel.json` with the requested ref and resolved commit.
6. Configures the kernel's build timeout and platforms.
7. Regenerates Konflux resources and commits the complete import.

The command rejects branch names such as `fedora-7.2`. A moving branch would make the imported
source depend on when the command ran.

The source archives currently remain in `rpms/kernel/`. Move them to Hummingbird's lookaside cache
before the repository starts tracking kernel updates.

## What is not automated

This operation does not discover new stable releases or choose when Hummingbird should move to a
new kernel series. Select and review each ref before importing it.

Kernel image and module signing are also separate from RPM artifact signing. Configure the
Hummingbird Secure Boot keys and signing process before publishing the kernel for production use.
