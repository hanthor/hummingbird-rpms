const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { createRequire } = require('node:module')
const { setTimeout: delay } = require('node:timers/promises')

const npmRequire = createRequire(path.join(path.resolve(process.argv[2]), 'package.json'))
const glob = npmRequire('glob')
const fixture = fs.mkdtempSync(path.join(os.tmpdir(), 'npm11-glob-'))

async function check() {
  for (let index = 0; index < 16; index++) {
    const directory = path.join(fixture, String(index), 'nested')
    fs.mkdirSync(directory, { recursive: true })
    fs.writeFileSync(path.join(directory, 'file.txt'), '')
  }

  // CVE-2026-118773: cancellation must stop background traversal.
  // Based on the upstream regression in isaacs/node-glob commit 4682e8c.
  const controller = new AbortController()
  const reason = new Error('cancel traversal')
  let reads = 0
  const instrumentedFs = {
    ...fs,
    readdir(...args) {
      if (++reads === 1) controller.abort(reason)
      return fs.readdir(...args)
    },
  }
  await assert.rejects(glob.glob('**/*', {
    cwd: fixture, signal: controller.signal, fs: instrumentedFs,
  }), reason)
  const readsAtRejection = reads
  await delay(100)
  assert.equal(reads, readsAtRejection, 'glob kept reading after cancellation')

  assert.equal(npmRequire('glob/package.json').version, '13.0.7')
  assert.equal(glob.globSync('**/*.txt', { cwd: fixture }).length, 16)
  // The raw entry point needs the complete replacement dependency tree too.
  assert.equal(npmRequire('glob/raw').globSync('**/*.txt', { cwd: fixture }).length, 16)
}

check().finally(() => fs.rmSync(fixture, { recursive: true, force: true }))
