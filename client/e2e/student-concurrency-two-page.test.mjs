import assert from 'node:assert/strict'
import test from 'node:test'

async function subject() { return import('./student-concurrency-two-page.mjs') }
const account = id => ({ account_id: `ui-${id}`, username: `user-${id}`, password: 'fixture-only',
  session_id: id, courseId: 1, taskId: 2, initial_state: 'prepared',
  questions: [{ assignment_id: id * 10, attempt_id: id * 10 + 1 }, { assignment_id: id * 10 + 2, attempt_id: id * 10 + 3 }] })
const fixture = () => ({ isolated_demo: true, run_id: 'two-ui', accounts: [account(1), account(2)] })

test('runs exactly two explicit independent accounts only through the authorized local tunnel', async () => {
  const { validateTwoPageFixture } = await subject()
  assert.equal(validateTwoPageFixture(fixture(), 'http://localhost:18082').length, 2)
  for (const target of ['http://100.72.241.6:18082', 'http://localhost:28082', 'https://school.example/']) {
    assert.throws(() => validateTwoPageFixture(fixture(), target))
  }
  assert.throws(() => validateTwoPageFixture({ ...fixture(), isolated_demo: false }, 'http://localhost:18082'))
  assert.throws(() => validateTwoPageFixture({ ...fixture(), accounts: [account(1), account(1)] }, 'http://localhost:18082'))
})

test('a larger fixture requires exactly two named accounts and never becomes a browser load cohort', async () => {
  const { validateTwoPageFixture } = await subject()
  const large = { ...fixture(), accounts: Array.from({ length: 180 }, (_, index) => account(index + 1)) }
  assert.throws(() => validateTwoPageFixture(large, 'http://127.0.0.1:18082'))
  assert.deepEqual(validateTwoPageFixture(large, 'http://127.0.0.1:18082', ['ui-3', 'ui-7']).map(row => row.account_id), ['ui-3', 'ui-7'])
  assert.throws(() => validateTwoPageFixture(large, 'http://127.0.0.1:18082', ['ui-3', 'ui-7', 'ui-8']))
})

test('durable202 acceptance alone cannot count as a completed submission', async () => {
  const { validateTerminalSubmission } = await subject()
  const receipt = { submission_id: 'one', status_url: '/api/v1/student/sessions/1/submission/' }
  assert.throws(() => validateTerminalSubmission(1, 202, receipt, { status: 'queued', submission_id: 'one' }, '/student/sessions/1/complete/'))
  assert.throws(() => validateTerminalSubmission(1, 202, receipt, { status: 'completed', submission_id: 'other' }, '/student/sessions/1/complete/'))
  assert.throws(() => validateTerminalSubmission(1, 202, receipt, { status: 'completed', submission_id: 'one' }, '/student/sessions/2/complete/'))
  assert.equal(validateTerminalSubmission(1, 202, receipt, { status: 'completed', submission_id: 'one' }, '/student/sessions/1/complete/'), true)
})

test('the summary preserves both actual page outcomes and does not evaluate capacity', async () => {
  const { twoPageSummary } = await subject()
  const summary = twoPageSummary([{ account_id: 'a', status: 'completed' }, { account_id: 'b', status: 'failed', error: 'real timeout' }])
  assert.equal(summary.planned_pages, 2)
  assert.equal(summary.completed_pages, 1)
  assert.equal(summary.failed_pages, 1)
  assert.equal(summary.capacity_evaluated, false)
  assert.equal(summary.passed, false)
  assert.throws(() => twoPageSummary([{ account_id: 'a', status: 'completed' }]))
})

test('only the exact native favicon404 is classified while raw console and strict failure remain', async () => {
  const { classifyConsoleErrors } = await subject()
  const known = { type: 'error', text: 'Failed to load resource: the server responded with a status of 404 (Not Found)',
    location: { url: 'http://127.0.0.1:18082/favicon.ico' } }
  const errors = [known, { ...known, location: { url: 'http://127.0.0.1:18082/api/v1/student/tasks/' } },
    { ...known, location: { url: 'http://elsewhere/favicon.ico' } }, { ...known, text: 'application favicon404 failure' },
    { ...known, text: 'Failed to load resource: the server responded with a status of 500 (Internal Server Error)' }]
  const before = globalThis.structuredClone(errors)
  const result = classifyConsoleErrors(errors, 'http://127.0.0.1:18082')
  assert.equal(result.strict_console_passed, false)
  assert.deepEqual(result.known_asset_errors, [known])
  assert.equal(result.unexpected_console_errors.length, 4)
  assert.deepEqual(errors, before)
  assert.equal(classifyConsoleErrors([], 'http://127.0.0.1:18082').strict_console_passed, true)
})

test('rendered completion fields must show20 assigned and2 submitted without inventing an unanswered label', async () => {
  const { validateCompletionFields } = await subject()
  assert.deepEqual(validateCompletionFields({ '已正式提交': '2', '分配题目': '20' }),
    { submitted_count: 2, assigned_count: 20, unanswered_count_displayed: false })
  assert.throws(() => validateCompletionFields({ '已正式提交': '3', '分配题目': '20' }))
  assert.throws(() => validateCompletionFields({ '已正式提交': '2', '分配题目': '19' }))
  assert.throws(() => validateCompletionFields({}))
})

test('an unexpected console error prevents a passing summary while the exact known asset issue stays separate', async () => {
  const { twoPageSummary } = await subject()
  const records = [{ account_id: 'one', status: 'completed', strict_console_passed: false, known_asset_errors: [{ text: 'favicon404' }], unexpected_console_errors: [] },
    { account_id: 'two', status: 'completed', strict_console_passed: true, known_asset_errors: [], unexpected_console_errors: [] }]
  assert.equal(twoPageSummary(records).passed, true)
  assert.equal(twoPageSummary(records).strict_console_passed, false)
  records[1].unexpected_console_errors.push({ text: 'application failure' })
  assert.equal(twoPageSummary(records).passed, false)
})


function leaseRecoveryRecord() {
  const head = '00000000-0000-4000-8000-000000000099'
  const client = 'observed-client-sha256'
  const root = '/api/v1/assessment/process/attempts/13/writer-lease/'
  const offline = { started_at: 1000, completed_at: 2000, status: 'completed', server_confirmed_target_event_id: head,
    server_confirmed_response_version: 5, application_errors: [] }
  const requests = [
    { source: 'page', method: 'POST', path: '/api/v1/assessment/process/capture-v2/batch/', at: 1100, status: 200,
      capture_request: { client_instance_sha256: client, events: [{ event_id: head, event_type: 'response_transition', client_sequence_no: 12 }] } },
    { source: 'page', method: 'POST', path: root + 'renew/', at: 1200, status: 409,
      capture_request: { client_instance_sha256: client }, error_evidence: { body: { error: { code: 'writer_lease_conflict' } } } },
    { source: 'page', method: 'POST', path: root + 'acquire/', at: 1300, status: 201,
      capture_request: { client_instance_sha256: client }, lease_response: { success: true, client_instance_sha256: client } },
    { source: 'page', method: 'POST', path: '/api/v1/student/attempts/13/draft/', at: 1400, status: 200,
      capture_request: { client_instance_sha256: client, target_event_id: head },
      capture_response: { success: true, active_response_event_id: head, response_version: 5 } },
  ]
  const console = [{ type: 'error', at: 1250, text: 'Failed to load resource: the server responded with a status of 409 (Conflict)',
    location: { url: 'http://127.0.0.1:18082' + root + 'renew/' } }]
  return { console, requests, page_errors: [], offline_recovery: offline }
}

test('classifies only a directly observed same-client lease recovery ending at the original durable offline head', async () => {
  const { classifyCapacityConsoleErrors } = await subject()
  const record = leaseRecoveryRecord()
  const before = globalThis.structuredClone(record)
  const result = classifyCapacityConsoleErrors(record.console, 'http://127.0.0.1:18082', record.offline_recovery, null, record)
  assert.equal(result.expected_lease_recovery_console.length, 1)
  assert.equal(result.unexpected_console_errors.length, 0)
  assert.equal(result.strict_console_passed, false)
  assert.equal(result.lease_recovery_chains[0].renew_to_durable_ms, 800)
  assert.deepEqual(record, before)
})

test('keeps incomplete, different-client, different-head or application-error lease recoveries unexpected', async () => {
  const { classifyCapacityConsoleErrors } = await subject()
  const mutations = [
    r => { delete r.requests[1].error_evidence },
    r => { r.requests[1].error_evidence.body.error.code = 'other_conflict' },
    r => { r.requests[2].capture_request.client_instance_sha256 = 'another-client' },
    r => { r.requests[2].lease_response.client_instance_sha256 = 'another-client' },
    r => { r.requests[3].capture_response.active_response_event_id = 'another-head' },
    r => { r.requests[3].status = 400 },
    r => { r.page_errors.push({ message: 'unhandled' }) },
    r => { r.offline_recovery.application_errors.push('草稿保存失败') },
    r => { r.requests[1].path = '/api/v1/student/attempts/13/draft/' },
  ]
  for (const change of mutations) {
    const record = leaseRecoveryRecord()
    change(record)
    const result = classifyCapacityConsoleErrors(record.console, 'http://127.0.0.1:18082', record.offline_recovery, null, record)
    assert.equal(result.unexpected_console_errors.length, 1)
    assert.equal(result.expected_lease_recovery_console.length, 0)
  }
})

test('lease diagnostics preserve only a client digest and never authentication material', async () => {
  const { captureRequestSummary } = await subject()
  const summary = captureRequestSummary('/api/v1/assessment/process/attempts/13/writer-lease/renew/',
    { client_instance_id: 'private-client-id', lease_token: 'private-lease-token', password: 'private-password' })
  assert.equal(typeof summary.client_instance_sha256, 'string')
  assert.equal(summary.client_instance_sha256.length, 64)
  assert(!JSON.stringify(summary).includes('private'))
})

test('v3 bridge dispatches before legacy fixture validation and preserves the original values', async () => {
  const {runTwoPageWitness}=await subject()
  const values={'run-manifest':'owned.json',fixture:'fixture.json',plan:'plan.json'}
  let actual
  const result=await runTwoPageWitness(values,{runRetest:async options=>{actual=options;return {v3:true}}})
  assert.deepEqual(result,{v3:true});assert.equal(actual,values)
})
