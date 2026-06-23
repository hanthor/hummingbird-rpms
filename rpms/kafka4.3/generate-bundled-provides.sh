#!/usr/bin/bash
# Generate bundled(name) = version Provides from Kafka's libs/ jars.
#
# Parses jar filenames from the built RPM (or a release tarball) and
# updates the spec file in-place between marker comments.
#
# Usage:
#   ./generate-bundled-provides.sh <rpm-or-tarball>
#
# Examples:
#   ./generate-bundled-provides.sh ../../../builds/kafka4.3/RPMS/kafka4.3-4.3.0-1.hum1.noarch.rpm
#   ./generate-bundled-provides.sh kafka_2.13-4.3.0.tgz

set -euo pipefail

input="${1:?Usage: $0 <rpm-or-tarball>}"
spec="$(dirname "$0")/kafka4.3.spec"

# Extract jar list depending on input type
if [[ "$input" == *.rpm ]]; then
    jars=$(rpm -qlp "$input" | grep '/libs/.*\.jar$')
elif [[ "$input" == *.tgz || "$input" == *.tar.gz ]]; then
    jars=$(tar -tzf "$input" | grep '/libs/.*\.jar$')
else
    echo "ERROR: expected .rpm or .tgz/.tar.gz" >&2
    exit 1
fi

# Generate provides lines
provides=$(echo "$jars" | while read -r path; do
    base=$(basename "$path" .jar)
    # Skip Kafka's own modules
    case "$base" in
        connect-*|kafka-*|kafka_*|server-*|clients-*|group-*|share-*|\
        transaction-*|raft-*|metadata-*|storage-*|coordinator-*|trogdor-*|\
        streams-*|tools-*)
            continue ;;
    esac
    if [[ "$base" =~ ^(.+)-([0-9]+\..*)$ ]]; then
        printf 'Provides:       bundled(%s) = %s\n' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
    else
        printf 'Provides:       bundled(%s)\n' "$base"
    fi
done | sort)

# Check if markers exist in spec
if grep -q '# BEGIN bundled provides' "$spec"; then
    # Replace between markers
    sed -i '/^# BEGIN bundled provides$/,/^# END bundled provides$/c\
# BEGIN bundled provides\
'"$(echo "$provides" | sed 's/$/\\/')"'
# END bundled provides' "$spec"
else
    # Insert before Conflicts: line
    sed -i "/^Conflicts:/i\\
# BEGIN bundled provides\\
$(echo "$provides" | sed 's/$/\\/')
# END bundled provides\\
" "$spec"
fi

count=$(echo "$provides" | wc -l)
echo "Updated ${spec}: ${count} bundled provides" >&2
