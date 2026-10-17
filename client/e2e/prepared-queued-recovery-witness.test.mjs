import assert from 'node:assert/strict'
import test from 'node:test'

test('activation gates never forward before release and release only once', async () => {
  const { createActivationGate } = await import('./prepared-queued-recovery-witness.mjs')
  let forwarded = 0, callback, now = 0
  const gate = createActivationGate({ now: () => now, setTimer: fn => { callback = fn; return 1 }, clearTimer: () => {} })
  const pending = gate.intercept({ continue: async () => { forwarded++ } })
  await gate.reached
  assert.equal(forwarded, 0)
  now = 1200; gate.release('third_online'); await pending
  gate.release('cleanup'); callback()
  assert.equal(forwarded, 1)
  assert.equal(gate.evidence.held_ms, 1200)
  assert.equal(gate.evidence.release_reason, 'third_online')
})

test('the automatic gate failsafe is within eight seconds without driver cleanup', async () => {
  const { createActivationGate } = await import('./prepared-queued-recovery-witness.mjs')
  let callback, delay, now = 0, forwarded = false
  const gate = createActivationGate({ now: () => now, setTimer: (fn, ms) => { callback = fn; delay = ms }, clearTimer: () => {} })
  const pending = gate.intercept({ continue: async () => { forwarded = true } })
  await gate.reached
  assert.ok(delay <= 8000)
  now = delay; callback(); await pending
  assert.equal(forwarded, true)
  assert.equal(gate.evidence.release_reason, 'automatic_failsafe')
  assert.ok(gate.evidence.held_ms <= 8000)
})

test('a real offline reload failure restores online and releases both gates', async () => {
  const { offlineReloadAndRelease } = await import('./prepared-queued-recovery-witness.mjs')
  const order = []
  const result = await offlineReloadAndRelease({ setOffline: async value => order.push(value ? 'offline' : 'online') },
    { reload: async () => { order.push('real-reload'); throw new Error('net::ERR_INTERNET_DISCONNECTED') } },
    () => { order.push('release-both-gates') }, () => 10000)
  assert.deepEqual(order, ['offline', 'real-reload', 'online', 'release-both-gates'])
  assert.equal(result.offline_reload_failed, true)
  assert.equal(result.offline_error_code, 'net::ERR_INTERNET_DISCONNECTED')
})

test('a failed return-online operation still cannot leave either activation gate held', async () => {
  const { offlineReloadAndRelease } = await import('./prepared-queued-recovery-witness.mjs')
  let released = false
  await assert.rejects(offlineReloadAndRelease({ setOffline: async value => { if (!value) throw new Error('closed') } },
    { reload: async () => { throw new Error('net::ERR_INTERNET_DISCONNECTED') } }, () => { released = true }, () => 10000))
  assert.equal(released, true)
})

test('third-person recovery rejects a new application or changed token and preserves unknown evidence', async () => {
  const { assessQueuedRecovery } = await import('./prepared-queued-recovery-witness.mjs')
  const valid = { originalTokenHash: 'original', restoredTokenHash: 'original', recoveryPosts: [{ admission_token_sha256: 'original' }], canonicalReceiptCount: 3 }
  assert.equal(assessQueuedRecovery(valid).passed, true)
  assert.equal(assessQueuedRecovery({ ...valid, restoredTokenHash: 'replacement' }).passed, false)
  assert.equal(assessQueuedRecovery({ ...valid, recoveryPosts: [{ admission_token_sha256: null }] }).new_application_observed, true)
  assert.equal(assessQueuedRecovery({ ...valid, recoveryPosts: [], canonicalReceiptCount: 2 }).passed, false)
})
