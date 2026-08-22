---
title: Testing RPMs with rpmtest
description: Run fresh-environment tests against local or published Hummingbird RPMs
weight: 65
---

`rpmtest` installs RPMs in fresh, minimal Podman environments and runs the
checks declared in a YAML specification. The pilot specifications live under
`test/rpmtest/` and cover GNU Hello, `curl`, `gcc`, Go 1.26 and 1.27, Rust,
Python 3.14, Node.js 26, Ruby 4.0, Java 25, and .NET 10.

## Build rpmtest

rpmtest requires Podman and `slirp4netns`. It also requires Go 1.26.5 to build.
Clone it over SSH and build it directly, or use a Go container if that
toolchain is not installed locally.

```console
git clone \
  git@gitlab.com:redhat/hummingbird/experimental/rpmtest.git \
  .cache/rpmtest-src
podman run --rm \
  --volume "$PWD/.cache:/workspace:z" \
  --workdir /workspace/rpmtest-src \
  --env CGO_ENABLED=0 \
  docker.io/library/golang:1.26.5 \
  go build -buildvcs=false -o /workspace/rpmtest ./cmd/rpmtest
```

The current rpmtest prototype expects its pinned builder image to exist in
local Podman storage. Pull it once before the first plan or test.

```console
podman pull \
  quay.io/hummingbird-rawhide/builder@sha256:766cf050ed9ef2afadc380cf066fca0f6ef25241bfe788de9c5181053cc01b72
```

## Run a pilot test

Pass all related binary RPMs from a build. rpmtest uses matching local RPMs
and resolves the remaining packages and dependencies from the public
Hummingbird repository.

```console
.cache/rpmtest test \
  --spec test/rpmtest/hello/rpmtest.yaml \
  builds/hello/RPMS/hello-[0-9]*.rpm
```

Omit the RPM arguments to test the package already published in the public
repository.

```console
.cache/rpmtest test --spec test/rpmtest/gcc/rpmtest.yaml
```

Use `list` to inspect the concrete suites produced by parameterized fragments
without resolving repositories or starting containers.

```console
.cache/rpmtest list --spec test/rpmtest/golang1.26/rpmtest.yaml
```

Use `--parallelism` to set concurrency for the runner. Test specifications
declare resource requirements, but do not choose runner concurrency.

```console
.cache/rpmtest test \
  --parallelism 4 \
  --spec test/rpmtest/golang1.27/rpmtest.yaml
```

## Add a pilot package

Create `test/rpmtest/<package>/rpmtest.yaml`. Put fixtures beside the YAML so
fixture paths remain relative to the specification. For versioned package
families, set `include-root: ..` and keep shared parameterized fragments and
fixtures in a family directory. An include-only root does not need an empty
`suites` list. Declare the package under
`environment.contents.packages`; rpmtest automatically selects a supplied local
RPM with the same package name.

## Gate requirements

Production-gating specifications must cover the package contract, primary
behavior, and important failure paths. Development packages must compile and
run a fixture against their public interface. Security-sensitive local changes
need a direct regression test.

Keep every runtime suite offline. Use repository packages only while rpmtest
constructs the environment. Do not call public services, read the wall clock,
depend on random ordering, use unbounded retries, or assert diagnostic wording
that is not part of a documented interface. Declare every test tool as an
environment package. Set explicit CPU, memory, and PID limits.

Before promoting a specification to a production gate:

1. Run it against the published package at least three consecutive times on
   each supported architecture.
2. Run it against the candidate RPMs and confirm the report identifies them as
   candidate packages.
3. Introduce a known-bad RPM or fixture and confirm the intended suite fails.
4. Review package changes for new interfaces or regressions that need tests.

rpmtest results cover package behavior. Production policy must separately
verify signatures, build provenance, vulnerability status, and promotion
approval.
