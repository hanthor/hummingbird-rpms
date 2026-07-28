# Recreating the ClickHouse source archive

The source archive includes ClickHouse's pinned submodules because the GitHub
generated release archive contains only empty submodule directories. Recreate
it from the signed upstream tag without network access during the RPM build:

```sh
version=26.7.1.1315
mkdir -p .tmp
git clone --depth 1 --branch "v${version}-stable" --recurse-submodules \
  --shallow-submodules https://github.com/ClickHouse/ClickHouse.git \
  ".tmp/clickhouse-${version}"
TZ=UTC tar --sort=name --mtime='2026-07-22 21:14:29Z' \
  --owner=0 --group=0 --numeric-owner \
  --exclude='*/.git' --exclude='*/.git/*' -cf - \
  -C .tmp "clickhouse-${version}" | \
  zstd -10 -T0 -o "clickhouse-${version}.tar.zst"
sha512sum --tag "clickhouse-${version}.tar.zst" > sources
```

The timestamp is the publication time of release `v26.7.1.1315-stable`.
Nested `.gitmodules` files are retained because Cargo validates them against
the checksums in the vendored crate metadata.
