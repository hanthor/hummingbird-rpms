#!/bin/bash
set -euo pipefail

npm_dir=$(realpath "$1")
test_dir=$(mktemp -d)
trap 'rm -rf "$test_dir"' EXIT
export npm_config_cache="$test_dir/cache"
cd "$test_dir"

# Check npm's configured external tool and prove that it belongs to the RPM.
npm() { node "$npm_dir/bin/npm-cli.js" "$@"; }
node_gyp=$(npm config get node-gyp)
test "$(realpath "$node_gyp")" = /usr/lib/node_modules/node-gyp/bin/node-gyp.js
test ! -e "$npm_dir/node_modules/node-gyp"
test ! -L "$npm_dir/node_modules/node-gyp"
rpm -qf "$node_gyp" | grep '^node-gyp-'

mkdir addon
cat > addon/package.json <<'EOF'
{"name":"native-addon","version":"1.0.0","scripts":{"install":"node-gyp rebuild --nodedir=/usr --python=/usr/bin/python3"}}
EOF
cat > addon/binding.gyp <<'EOF'
{"targets":[{"target_name":"hello","sources":["hello.c"]}]}
EOF
cat > addon/hello.c <<'EOF'
#include <node_api.h>
static napi_value init(napi_env env, napi_value exports) {
    napi_value value;
    if (napi_create_string_utf8(env, "external node-gyp works", NAPI_AUTO_LENGTH, &value) != napi_ok)
        return NULL;
    if (napi_set_named_property(env, exports, "message", value) != napi_ok)
        return NULL;
    return exports;
}
NAPI_MODULE(NODE_GYP_MODULE_NAME, init)
EOF

(cd addon && npm pack --offline --ignore-scripts)
cat > package.json <<'EOF'
{"name":"npm11-smoke-test","version":"1.0.0","private":true}
EOF
npm install --offline --ignore-scripts --no-audit --no-fund ./addon/native-addon-1.0.0.tgz
npm rebuild --offline --foreground-scripts native-addon
node -e 'require("node:assert/strict").equal(require("./node_modules/native-addon/build/Release/hello.node").message, "external node-gyp works")'
