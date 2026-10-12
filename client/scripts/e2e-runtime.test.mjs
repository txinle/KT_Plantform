import assert from 'node:assert/strict'
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'

test('E2E uses the server-owned environment and fails when it is missing', async () => {
  assert.ok(existsSync(join(import.meta.dirname, 'e2e-runtime.mjs')), 'Server runtime resolver is required')
  const { requireServerPython, serverDir } = await import('./e2e-runtime.mjs')
  assert.equal(serverDir, join(import.meta.dirname, '..', '..', 'server'))
  const fixture = mkdtempSync(join(tmpdir(), 'kt-server-runtime-'))
  try {
    assert.throws(() => requireServerPython(fixture), /Server Python environment is missing/)
    const executable = join(fixture, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
    mkdirSync(join(executable, '..'), { recursive: true })
    writeFileSync(executable, '')
    assert.equal(requireServerPython(fixture), executable)
  } finally {
    rmSync(fixture, { recursive: true, force: true })
  }
})
