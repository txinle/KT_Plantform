import assert from 'node:assert/strict'
import test from 'node:test'
import { existsSync } from 'node:fs'
import { URL } from 'node:url'

async function subject() {
  assert.ok(existsSync(new URL('./student-concurrency-witness.mjs', import.meta.url)), 'Concurrency runner is missing')
  return import('./student-concurrency-witness.mjs')
}

test('login preparation fills credentials without authenticating before shared release', async () => {
  const { prepareLogin, releaseLogin } = await subject()
  const events = []
  const page = { goto: async () => events.push('goto'), getByLabel: label => ({ fill: async () => events.push(label) }),
    getByRole: () => ({ click: async () => events.push('click') }), waitForResponse: async () => ({ ok: () => true,
      status: () => 200, finished: async () => null, request: () => ({ timing: () => ({ requestStart: 1, responseEnd: 4 }) }) }) }
  await prepareLogin(page, { username: 'u', password: 'p' })
  assert.deepEqual(events, ['goto', '账号或学号', '密码'])
  const result = await releaseLogin(page, {}, { waitReady: async () => events.push('ready') })
  assert.deepEqual(events, ['goto', '账号或学号', '密码', 'click', 'ready'])
  assert.ok(result.login_ms >= 0)
  assert.equal(result.login_http_ms, 3)
})

test('login witness waits for the applications own LOGIN_BUSY retries without clicking again', async () => {
  const { releaseLogin } = await subject()
  let clicks = 0
  const attempts = [429, 429, 200]
  const page = { getByRole: () => ({ click: async () => { clicks++ } }),
    waitForResponse: async predicate => {
      for (const status of attempts) {
        const response = { url: () => 'http://127.0.0.1:18082/api/v1/auth/login/', status: () => status,
          ok: () => status === 200, finished: async () => null,
          json: async () => ({ error: { code: 'LOGIN_BUSY' } }),
          request: () => ({ method: () => 'POST', timing: () => ({ requestStart: 1, responseEnd: 2 }) }) }
        if (await predicate(response)) return response
      }
      throw new Error('No terminal authentication response')
    } }
  const result = await releaseLogin(page, {}, { waitReady: async () => undefined })
  assert.equal(clicks, 1)
  assert.equal(result.login_http_status, 200)
  assert.deepEqual(result.login_http_attempts.map(row => row.status), attempts)
})

test('same host or mismatched local machine cannot satisfy independent load attestation', async () => {
  const { validateAttestation } = await subject()
  const good = { load_machine_id: 'load', dut_machine_id: 'dut', collected_at: '2026-10-15T00:00:00Z',
    dut_identity_command: 'cat /etc/machine-id', load_identity_command: 'cat /etc/machine-id', resource_evidence_sha256: 'a'.repeat(64) }
  assert.equal(validateAttestation(good, 'load'), true)
  assert.throws(() => validateAttestation({ ...good, dut_machine_id: 'load' }, 'load'))
  assert.throws(() => validateAttestation(good, 'other'))
})

test('diagnostic roster keeps every selected account and refuses accidental small acceptance', async () => {
  const { selectRoster } = await subject()
  const rows = Array.from({ length: 180 }, (_, index) => ({ account_id: String(index), initial_state: index < 60 ? 'active' : 'prepared' }))
  assert.equal(selectRoster(rows, 'login-mixed', 3).filter(row => row.role === 'save').length, 1)
  assert.equal(selectRoster(rows, 'login-mixed', 180).filter(row => row.role === 'login').length, 120)
  assert.throws(() => selectRoster(rows.slice(0, 3), 'login', 180))
})

test('startup mixed selects 120 fresh startup contexts and 60 sustained savers', async () => {
  const { selectRoster } = await subject()
  const accounts = Array.from({ length: 180 }, (_, i) => ({ account_id: String(i), initial_state: i < 60 ? 'active' : 'prepared' }))
  const rows = selectRoster(accounts, 'startup-mixed', 180)
  assert.equal(rows.filter(row => row.role === 'startup').length, 120)
  assert.equal(rows.filter(row => row.role === 'save').length, 60)
  assert.equal(rows.filter(row => row.role === 'login').length, 0)
})

test('released startup reuses the legacy witness, preserving raw evidence and its click boundary', async () => {
  const { runReleased } = await subject()
  const raw = { status: 'completed', phase_origin_id: 'browser:1', timestamps_ms: { click: 100, operable: 4100 },
    raw_phase_samples: [{ phase: 'click' }, { phase: 'operable' }], activation_receipt_ids: ['9'] }
  const prepared = { account: { account_id: 'a' }, row: { role: 'startup', attempt_id: 'startup-a' }, context: { close: async () => {} } }
  let called = 0
  const record = await runReleased(prepared, { plan_id: 'p', run_id: 'r', mode: 'startup-mixed', operation_timeout_ms: 60000,
    completion_timeout_ms: 300000 }, Date.now(), { runStartup: async (current, binding, _release, close) => {
      assert.equal(current, prepared)
      assert.equal(binding.input_settings_hash, 'p')
      assert.equal(close, false)
      called++
      return raw
    } })
  assert.equal(called, 1)
  assert.equal(record.status, 'completed')
  assert.equal(record.startup_ms, 4000)
  assert.equal(record.startup_witness, raw)
  assert.equal(record.ui_login, undefined)
})

test('startup success without a valid same-origin click/operable clock is a failed account', async () => {
  const { runReleased } = await subject()
  const prepared = { account: { account_id: 'a' }, row: { role: 'startup' }, context: { close: async () => {} } }
  const record = await runReleased(prepared, { plan_id: 'p', run_id: 'r', mode: 'startup-mixed', operation_timeout_ms: 60000,
    completion_timeout_ms: 300000 }, Date.now(), { runStartup: async () => ({ status: 'completed', timestamps_ms: { click: 100, operable: 10 } }) })
  assert.equal(record.status, 'failed')
})

test('startup release skew includes delay until the reused witness actually starts clicking', async () => {
  const { runReleased } = await subject()
  const released = Date.now()
  const record = await runReleased({ account: { account_id: 'a' }, row: { role: 'startup' }, context: { close: async () => {} } },
    { plan_id: 'p', run_id: 'r', mode: 'startup-mixed', operation_timeout_ms: 60000, completion_timeout_ms: 300000 }, released,
    { runStartup: async () => ({ status: 'completed', phase_origin_id: 'clock', timestamps_ms: { click: 0, operable: 100 },
      observer_start_epoch_ms: released + 1100 }) })
  assert.equal(record.release_skew_ms, 1100)
})

test('release failures remain account records instead of rejecting the whole cohort', async () => {
  const { runReleased } = await subject()
  const page = { waitForResponse: async () => null, getByRole: () => ({ click: async () => { throw new Error('private credentials') } }) }
  const record = await runReleased({ page, account: { account_id: 'a' }, row: { role: 'login' }, context: { close: async () => {} } },
    { plan_id: 'p', run_id: 'r', mode: 'login', completion_timeout_ms: 1000 }, Date.now())
  assert.equal(record.status, 'failed')
  assert.equal(record.account_id, 'a')
  assert.ok(!JSON.stringify(record).includes('private credentials'))
})

test('frozen runner refuses missing full PostgreSQL allocation even for C2', async () => {
  const { validateInputs } = await subject()
  const accounts = Array.from({ length: 180 }, (_, index) => ({ account_id: String(index), username: String(index),
    initial_state: 'prepared', taskId: 1, courseId: 2, session_id: index + 1, password: 'p', questions: [{ assignment_id: 1 }, { assignment_id: 2 }] }))
  const roster = accounts.slice(0, 3).map(a => ({ account_id: a.account_id, role: 'login', task_id: 1, course_id: 2,
    session_id: a.session_id, answers: [{ assignment_id: 1 }, { assignment_id: 2 }] }))
  const attestation = { load_machine_id: 'load', dut_machine_id: 'dut', collected_at: new Date().toISOString(),
    dut_identity_command: 'cat /etc/machine-id', load_identity_command: 'cat /etc/machine-id',
    resource_evidence_sha256: 'a'.repeat(64), target_origin: 'http://localhost:8000' }
  assert.throws(() => validateInputs({ mode: 'login', count: 3, roster, source_sha256: {} },
    { accounts, isolated_demo: true }, attestation, { settings: { C: 2, R: 64 } }, 'http://localhost:8000', 'load'))
})
