import assert from 'node:assert/strict'
import test from 'node:test'

const fixture = { isolated_demo: true, accounts: [{ account_id: 'fresh-one', username: 'private-user', password: 'private-password', courseId: 2, taskId: 7 }] }
const response = visit => ({ data: { session_id: 9, runtime: { entry_receipt: {
  session_id: 9, assignment_id: 11, attempt_id: 13, visit_id: visit, visit_index: visit,
  navigation_id: 'private-navigation', entry_event_id: `private-event-${visit}`, entered_at: '2026-10-15T00:00:00Z',
}, question: { text: 'private-answer-material' }, initial_writer_lease: { writer_lease_token: 'private-lease' } } } })

test('direct recovery requires the original persisted navigation and client, rather than an absent admission token', async () => {
  const { pendingStartMatches } = await import('./prepared-recovery-witness.mjs')
  assert.equal(typeof pendingStartMatches, 'function')
  const body = { navigation_id: 'nav', client_instance_id: 'client' }
  assert.equal(pendingStartMatches({ startIntent: body }, body), true)
  assert.equal(pendingStartMatches({}, body), false)
  assert.equal(pendingStartMatches({ startIntent: { ...body, navigation_id: 'different' } }, body), false)
})

test('only explicit isolated loopback fixture/account inputs with a new output are accepted', async () => {
  const { validateRecoveryInputs } = await import('./prepared-recovery-witness.mjs')
  assert.equal(validateRecoveryInputs(fixture, 'http://127.0.0.1:18081', '0', false).taskId, 7)
  for (const url of ['https://production.example', 'http://127.0.0.1:5177', 'http://user:pass@localhost:18081']) {
    assert.throws(() => validateRecoveryInputs(fixture, url, '0', false))
  }
  assert.throws(() => validateRecoveryInputs({ ...fixture, isolated_demo: false }, 'http://localhost:18081', '0', false))
  for (const index of ['-1', '1', '0.5', '']) assert.throws(() => validateRecoveryInputs(fixture, 'http://localhost:18081', index, false))
  assert.throws(() => validateRecoveryInputs(fixture, 'http://localhost:18081', '0', true))
})

test('receipt equality binds the actual first visit and never serializes private content or credentials', async () => {
  const { receiptEvidence } = await import('./prepared-recovery-witness.mjs')
  const first = receiptEvidence(response(1))
  assert.equal(first.receipt_sha256, receiptEvidence(response(1)).receipt_sha256)
  assert.notEqual(first.receipt_sha256, receiptEvidence(response(2)).receipt_sha256)
  assert.equal(first.visit_id, 1)
  assert.ok(!JSON.stringify(first).includes('private-'))
  assert.throws(() => receiptEvidence({ data: { session_id: 99, runtime: response(1).data.runtime } }))
})

test('response loss occurs only after route.fetch has returned a genuine committed 201 and its receipt', async () => {
  const { loseCommittedActivationResponse } = await import('./prepared-recovery-witness.mjs')
  const order = []
  const real = { status: () => 201, json: async () => { order.push('read-committed-receipt'); return response(1) } }
  const result = await loseCommittedActivationResponse({ fetch: async () => { order.push('real-server-201'); return real },
    abort: async () => { order.push('browser-response-aborted') } }, 10000, async () => { order.push('durable-intent-observed') })
  assert.deepEqual(order, ['real-server-201', 'read-committed-receipt', 'durable-intent-observed', 'browser-response-aborted'])
  assert.equal(result.receipt.session_id, 9)
  let aborted = false, forwarded = false
  await assert.rejects(loseCommittedActivationResponse({ fetch: async () => ({ status: () => 503 }),
    abort: async () => { aborted = true }, fulfill: async () => { forwarded = true } }, 10000))
  assert.equal(aborted, false)
  assert.equal(forwarded, true)
})

test('all planned cases retain a failed or not-executed denominator and no action exceeds 60000ms', async () => {
  const { runRecoveryCase, newRecoveryCases } = await import('./prepared-recovery-witness.mjs')
  const rows = newRecoveryCases()
  await runRecoveryCase(rows[0], async () => { throw Object.assign(new Error('private-password'), { code: 'REAL_RECEIPT_MISMATCH' }) })
  assert.equal(rows[0].status, 'failed')
  assert.equal(rows[0].error_code, 'REAL_RECEIPT_MISMATCH')
  assert.ok(!JSON.stringify(rows).includes('private-password'))
  assert.equal(rows.length, 3)
  assert.equal(rows[2].status, 'not_executed')
  let stopped = false
  await runRecoveryCase(rows[1], () => new Promise(() => {}), { timeoutMs: 2, onTimeout: async () => { stopped = true } })
  assert.equal(stopped, true)
  assert.equal(rows[1].error_code, 'ACTION_TIMEOUT')
  await assert.rejects(runRecoveryCase(rows[2], async () => {}, { timeoutMs: 60001 }))
})

test('copied-tab evidence requires native copied storage, a new client and actual server lease conflict', async () => {
  const { assessCopiedTab } = await import('./prepared-recovery-witness.mjs')
  const input = { originalClient: 'client-one-secret', copiedClient: 'client-two-secret', originalTab: 'tab-one', inheritedTab: 'tab-one',
    copiedTab: 'tab-two', nativeLocks: true, leaseStatuses: [409], copiedDraftSuccesses: 0 }
  const result = assessCopiedTab(input)
  assert.equal(result.passed, true)
  assert.ok(!JSON.stringify(result).includes('client-one-secret'))
  assert.equal(assessCopiedTab({ ...input, copiedClient: input.originalClient }).passed, false)
  assert.equal(assessCopiedTab({ ...input, leaseStatuses: [200] }).passed, false)
  assert.equal(assessCopiedTab({ ...input, copiedDraftSuccesses: 1 }).passed, false)
})

test('the copied tab edits a different radio answer instead of checking an already selected control', async () => {
  const { answerDifferentControl } = await import('./prepared-recovery-witness.mjs')
  let changed = false
  const first = { getAttribute: async () => 'radio' }
  const unselected = { count: async () => 1, check: async () => { changed = true } }
  await answerDifferentControl({ locator: selector => ({ first: () => selector.includes(':not(:checked)') ? unselected : first }) }, {})
  assert.equal(changed, true)
})
