#!/usr/bin/env bash
# Generate Fedora-style bundled Node.js provides from package.json files.

normalize_version() {
    local dependency="$1"
    local version="$2"
    if [[ "$version" == workspace:* ]]; then
        if [[ -z "${NPM_PROMETHEUS_ROOT:-}" ]]; then
            echo "NPM_PROMETHEUS_ROOT is required for workspace dependency $dependency" >&2
            return 1
        fi
        find "$NPM_PROMETHEUS_ROOT" -name package.json -exec \
            jq -r --arg dependency "$dependency" \
            'select(.name == $dependency) | .version // empty' {} + | head -n1
    else
        sed 's/^[^0-9]*//' <<<"$version"
    fi
}

normalize_name() {
    sed 's|/|-|g' <<<"${1#@}"
}

for package_json in "$@"; do
    name=$(jq -r '.name // empty' "$package_json")
    version=$(jq -r '.version // empty' "$package_json")
    if [[ -n "$name" && -n "$version" ]]; then
        echo "Provides: npm($(normalize_name "$name")) = $version"
    fi

    for section in dependencies optionalDependencies; do
        jq -r ".$section // {} | to_entries[] | \"\(.key) \(.value)\"" "$package_json" |
            while read -r dependency version; do
                echo "Provides: bundled(nodejs-$(normalize_name "$dependency")) = $(normalize_version "$dependency" "$version")"
            done
    done
done | sort -u
