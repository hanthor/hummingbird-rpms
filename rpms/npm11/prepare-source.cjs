const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { createRequire } = require('node:module')

const directory = path.resolve(process.argv[2])
const npmRequire = createRequire(path.join(directory, 'package.json'))
const semver = npmRequire('semver')
const manifest = npmRequire('./package.json')
const nodeGyp = npmRequire('node-gyp/package.json')
assert.equal(nodeGyp.version, '13.1.0')
assert.equal(npmRequire('glob/package.json').version, '13.0.7')
// The external node-gyp runtime must also satisfy npm's upstream engine range.
assert(semver.subset(nodeGyp.engines.node, manifest.engines.node),
  'node-gyp requires a runtime outside npm support; review engine requirements')
manifest.dependencies['node-gyp'] = `^${nodeGyp.version}`
manifest.engines.node = nodeGyp.engines.node
fs.writeFileSync(path.join(directory, 'package.json'), JSON.stringify(manifest, null, 2) + '\n')
