import assert from 'node:assert/strict'
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'

test('build validation rejects missing hashed resources and mismatched versioned shells', async (t) => {
  const { checkBuild } = await import('./check-build.mjs')
  const directory = await mkdtemp(join(tmpdir(), 'kt-build-check-'))
  t.after(() => rm(directory, { recursive: true, force: true }))
  await mkdir(join(directory, '.vite'))
  await mkdir(join(directory, 'assets'))
  await writeFile(join(directory, 'index.html'), '<script src="/static/spa-assets/assets/app-Abc123.js"></script>')
  await writeFile(join(directory, 'capture-shell-01234567890123456789.html'), '<meta name="kt-shell-build" content="01234567890123456789"><script src="/static/spa-assets/assets/app-Abc123.js"></script>')
  await writeFile(join(directory, 'capture-v2-offline-worker.js'), 'worker')
  await writeFile(join(directory, '.vite/manifest.json'), JSON.stringify({ 'index.html': { file: 'assets/app-Abc123.js', isEntry: true } }))
  assert.throws(() => checkBuild(directory), /Missing build resource/)
  await writeFile(join(directory, 'assets/app-Abc123.js'), 'script')
  assert.equal(checkBuild(directory).version, '01234567890123456789')
  await writeFile(join(directory, 'capture-shell-01234567890123456789.html'), '<meta name="kt-shell-build" content="different-version">')
  assert.throws(() => checkBuild(directory), /version/)
})
