const fs = require('node:fs')
const path = require('node:path')
const { createRequire } = require('node:module')

function nodejsRequirement(range, semver) {
  if (typeof range !== 'string' || !range.trim()) {
    throw new Error('package.json must declare engines.node')
  }
  // Use the semver implementation shipped by the npm release.
  const alternatives = new semver.Range(range).set.map(comparators => {
    const requirements = comparators.filter(c => c.value).map(comparator => {
      const version = comparator.semver
      const release = `${version.major}.${version.minor}.${version.patch}`
      const prerelease = version.prerelease.length ? `~${version.prerelease.join('.')}` : ''
      return `nodejs(engine) ${comparator.operator || '='} ${release}${prerelease}`
    })
    if (!requirements.length) return 'nodejs(engine)'
    return requirements.length === 1 ? requirements[0] : `(${requirements.join(' with ')})`
  })
  return alternatives.length === 1 ? alternatives[0] : `(${alternatives.join(' or ')})`
}

if (require.main === module) {
  const [npmDirectory, output] = process.argv.slice(2)
  const npmRequire = createRequire(path.join(path.resolve(npmDirectory), 'package.json'))
  const { engines } = npmRequire('./package.json')
  const requirement = nodejsRequirement(engines?.node, npmRequire('semver'))
  const range = engines.node.replace(/\s+/g, ' ')
  fs.writeFileSync(output, `# Generated from npm package.json engines.node: ${range}\nRequires:       ${requirement}\n`)
}

module.exports = { nodejsRequirement }
