#!/usr/bin/env node
// Adapter for `ignore` (https://github.com/kaelzhang/node-ignore).  npm install ignore
//
// Pass --no-ignorecase to turn off the library's default case-insensitive matching. The default
// is left on here because that is what a caller gets by writing `ignore()`, and the bench is
// meant to report what people actually run, not the flattering configuration.

const readline = require('readline')
const ignore = require('ignore')

const caseSensitive = process.argv.includes('--no-ignorecase')

const rl = readline.createInterface({ input: process.stdin, terminal: false })

rl.on('line', (line) => {
  if (!line.trim()) return
  const req = JSON.parse(line)
  const ig = ignore(caseSensitive ? { ignorecase: false } : undefined).add(req.patterns)
  const answers = req.queries.map((q) => {
    try {
      return ig.ignores(q)
    } catch (e) {
      return null // node-ignore throws on paths it refuses to judge; that is a gap, not a verdict
    }
  })
  process.stdout.write(JSON.stringify({ id: req.id, ignored: answers }) + '\n')
})
