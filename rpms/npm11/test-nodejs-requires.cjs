const assert = require('node:assert/strict')
const path = require('node:path')
const { createRequire } = require('node:module')
const { nodejsRequirement } = require('./generate-nodejs-requires.cjs')

const npmRequire = createRequire(path.join(path.resolve(process.argv[2]), 'package.json'))
const semver = npmRequire('semver')
const convert = range => nodejsRequirement(range, semver)

assert.equal(convert('^22.22.2'), '(nodejs(engine) >= 22.22.2 with nodejs(engine) < 23.0.0~0)')
assert.equal(convert('^22.22.2 || >=26.0.0'),
  '((nodejs(engine) >= 22.22.2 with nodejs(engine) < 23.0.0~0) or nodejs(engine) >= 26.0.0)')
assert.equal(convert('^0.2.3'), '(nodejs(engine) >= 0.2.3 with nodejs(engine) < 0.3.0~0)')
assert.equal(convert('~24.15.0'), '(nodejs(engine) >= 24.15.0 with nodejs(engine) < 24.16.0~0)')
assert.equal(convert('26.0.0'), 'nodejs(engine) = 26.0.0')
assert.equal(convert('*'), 'nodejs(engine)')
assert.throws(() => convert('not a version'))
assert.throws(() => convert(''))
assert.throws(() => convert(undefined))
