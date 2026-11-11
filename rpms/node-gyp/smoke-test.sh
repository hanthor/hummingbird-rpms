#!/bin/bash
set -euo pipefail

node_gyp=$(realpath "$1")
node_headers=${2:-/usr}
test_dir=$(mktemp -d)
trap 'rm -rf "$test_dir"' EXIT
cd "$test_dir"

cat > binding.gyp <<'EOF'
{"targets": [{"target_name": "hello", "sources": ["hello.c"]}]}
EOF
cat > hello.c <<'EOF'
#include <node_api.h>

static napi_value init(napi_env env, napi_value exports) {
    napi_value value;
    if (napi_create_string_utf8(env, "node-gyp works", NAPI_AUTO_LENGTH, &value) != napi_ok)
        return NULL;
    if (napi_set_named_property(env, exports, "message", value) != napi_ok)
        return NULL;
    return exports;
}

NAPI_MODULE(NODE_GYP_MODULE_NAME, init)
EOF

# Local headers make this test independent of network access.
node "$node_gyp" rebuild --nodedir="$node_headers" --python=/usr/bin/python3
node -e 'require("node:assert/strict").equal(require("./build/Release/hello.node").message, "node-gyp works")'
