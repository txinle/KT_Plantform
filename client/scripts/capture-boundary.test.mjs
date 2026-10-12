import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { URL } from 'node:url'

test('native collector excludes continuous keyboard and mouse capture', () => {
  const source = ['process-sender.ts', 'session-lifecycle.ts', 'session-controller.ts'].map(file => readFileSync(new URL(`../src/assessment-runtime/${file}`, import.meta.url), 'utf8')).join('\n')
  for (const prohibited of ['keydown', 'keyup', 'mousemove', 'mouse_x', 'mouse_y']) {
    assert.ok(!source.includes(prohibited), `Prohibited continuous capture: ${prohibited}`)
  }
})
