import assert from 'node:assert/strict'
import test from 'node:test'

const fixture = () => ({ isolated_demo: true, run_id: 'capacity-v1', tasks: [
  { course_id: 7, task_id: 8, experiment_id: 9, class_id: 10 },
], accounts: [{ account_id: 'one', username: 'student-one', password: 'synthetic', courseId: 7, taskId: 8 }] })

test('teacher identity and scope come only from the owned isolated fixture', async () => {
  const { capacityTeacherAccount } = await import('./student-capacity-visual.mjs')
  const teacher = capacityTeacherAccount(fixture(), fixture().accounts[0])
  assert.equal(teacher.username, 'pp-capacity-v1-teacher')
  assert.equal(teacher.password, 'synthetic')
  assert.equal(teacher.path, '/teacher/courses/7/data/advanced/?experiment_id=9&class_id=10')
  assert.throws(() => capacityTeacherAccount({ ...fixture(), isolated_demo: false }, fixture().accounts[0]))
  assert.throws(() => capacityTeacherAccount(fixture(), { ...fixture().accounts[0], courseId: 70 }))
})

test('count verification accepts overlap but refuses fresh-complete counts larger than readable data', async () => {
  const { validatePreparationCounts } = await import('./student-capacity-visual.mjs')
  const counts = { total_count: 10, ready_count: 8, latest_complete_count: 3,
    input_incomplete_count: 5, input_unverified_count: 2, stale_count: 2,
    pending_count: 1, updating_count: 2, failed_count: 1 }
  assert.equal(validatePreparationCounts(counts).total_count, 10)
  assert.throws(() => validatePreparationCounts({ ...counts, latest_complete_count: 9 }))
  assert.throws(() => validatePreparationCounts({ ...counts, input_unverified_count: 6 }))
  assert.throws(() => validatePreparationCounts({ ...counts, total_count: 1 }))
  assert.throws(() => validatePreparationCounts({ ...counts, pending_count: -1 }))
})

test('expected offline network errors stay in strict evidence and do not hide other errors', async () => {
  const { classifyCapacityConsoleErrors } = await import('./student-concurrency-two-page.mjs')
  const message = { type: 'error', text: 'Failed to load resource: net::ERR_INTERNET_DISCONNECTED',
    location: { url: 'http://127.0.0.1:18082/api/v1/student/questions/1/' }, at: 20 }
  const result = classifyCapacityConsoleErrors([message, { ...message, at: 5 },
    { ...message, location: { url: 'https://other.example/' } },
    { ...message, text: 'application failure' }], 'http://127.0.0.1:18082', { started_at: 10, completed_at: 30 })
  assert.equal(result.strict_console_passed, false)
  assert.equal(result.expected_offline_console_errors.length, 1)
  assert.equal(result.unexpected_console_errors.length, 3)
})

test('successful application login retries classify only matching LOGIN_BUSY console responses', async () => {
  const { classifyCapacityConsoleErrors } = await import('./student-concurrency-two-page.mjs')
  const message = { type: 'error', text: 'Failed to load resource: the server responded with a status of 429 (Too Many Requests)',
    location: { url: 'http://127.0.0.1:18082/api/v1/auth/login/' }, at: 20 }
  const result = classifyCapacityConsoleErrors([message, { ...message, location: { url: 'http://127.0.0.1:18082/api/v1/student/attempts/1/draft/' } }],
    'http://127.0.0.1:18082', undefined, { login_http_status: 200, login_http_attempts: [{ status: 429, code: 'LOGIN_BUSY' }, { status: 200 }] })
  assert.equal(result.strict_console_passed, false)
  assert.equal(result.expected_login_busy_console_errors.length, 1)
  assert.equal(result.unexpected_console_errors.length, 1)
})

test('future failure evidence keeps event and checkpoint identities while excluding credentials', async () => {
  const { captureRequestSummary, captureErrorEvidence } = await import('./student-concurrency-two-page.mjs')
  const request = captureRequestSummary('/api/v1/student/attempts/1/draft/', {
    answer: 'synthetic', lease_token: 'lease-secret', capture_operation_id: 'operation-id',
    capture_bundle: { client_instance_id: 'client-id', event_boundary: 9,
      events: [{ event_id: 'event-id', event_type: 'response_transition', client_sequence_no: 9,
        payload: { parent_event_id: 'parent-id', before_state_hash: 'before', after_state_hash: 'after', password: 'password-secret' } }],
      checkpoints: [{ checkpoint_id: 'checkpoint-id', highest_emitted_sequence_no: 9, pending_event_count: 0 }] },
  })
  assert.equal(request.events[0].event_id, 'event-id')
  assert.equal(request.events[0].client_sequence_no, 9)
  assert.equal(request.events[0].parent_event_id, 'parent-id')
  assert.equal(request.checkpoints[0].highest_emitted_sequence_no, 9)
  const response = captureErrorEvidence(400, { 'x-request-id': 'request-id', 'set-cookie': 'cookie-secret' },
    { error: { code: 'INVALID_PROCESS_EVENT', fields: { lease_token: 'lease-secret', event_id: 'event-id' } }, password: 'password-secret' })
  assert.equal(response.request_id, 'request-id')
  assert.equal(response.body.error.code, 'INVALID_PROCESS_EVENT')
  for (const secret of ['lease-secret', 'password-secret', 'cookie-secret']) assert(!JSON.stringify({ request, response }).includes(secret))
  assert.equal(captureRequestSummary('/api/v1/auth/login/', { password: 'password-secret' }), undefined)
})

test('v3 teacher uses actual owned baseline credentials rather than synthesized legacy names', async () => {
  const {capacityTeacherAccount}=await import('./student-capacity-visual.mjs')
  const owned={...fixture(),source_ownership:{command:'prepare_student_capacity_baseline'},
    source_fixture_id:'source',baseline_snapshot_id:'snapshot',teacher_account:{user_id:42,username:'actualsource',password:'owned-private'}}
  const result=capacityTeacherAccount(owned,owned.accounts[0])
  assert.equal(result.username,'actualsource');assert.equal(result.password,'owned-private')
  assert.equal(result.path,'/teacher/courses/7/data/advanced/?experiment_id=9&class_id=10')
  assert.throws(()=>capacityTeacherAccount({...owned,teacher_account:null},owned.accounts[0]))
})
