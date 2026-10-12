import assert from 'node:assert/strict'
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'

test('development source changes run independent serialized builds with current source versions', async (t) => {
  const { watchClientBuild } = await import('./watch-client-build.mjs')
  const directory = await mkdtemp(join(tmpdir(), 'kt-build-watch-'))
  t.after(() => rm(directory, { recursive: true, force: true }))
  await mkdir(join(directory, 'src'))
  await writeFile(join(directory, 'src/main.ts'), 'initial')
  let release
  let started
  const first = new Promise((resolve) => { started = resolve })
  const builds = []
  const watcher = watchClientBuild({ directory, debounceMs: 5, build: async () => {
    builds.push(true)
    started()
    if (builds.length === 1) await new Promise((resolve) => { release = resolve })
  } })
  t.after(() => watcher.close())
  await writeFile(join(directory, 'src/main.ts'), 'version-one')
  await first
  await writeFile(join(directory, 'src/main.ts'), 'version-two')
  await new Promise((resolve) => globalThis.setTimeout(resolve, 40))
  assert.equal(builds.length, 1, 'a source edit cannot overlap the current build')
  release()
  for (let attempt = 0; builds.length < 2 && attempt < 30; attempt++) await new Promise((resolve) => globalThis.setTimeout(resolve, 10))
  assert.equal(builds.length, 2, 'a source edit during a build must receive a fresh independent build')
})
