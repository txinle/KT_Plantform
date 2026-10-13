import test from 'node:test'
import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import * as witness from './browser-network-witness.mjs'
import { digest } from './capture-protocol-witness.mjs'

const uuid = number => `${String(number).padStart(8, '0')}-0000-4000-8000-000000000000`
const clocks = { client_wall_time_utc: '2026-10-12T03:00:00.000Z', client_monotonic_ms: 12.125,
  client_time_origin: '2026-10-12T02:00:00.000Z', time_sync_id: null }

test('ordinary draft and tool mutations retain capture_bundle events checkpoints and real ACKs', async () => {
  for (const path of ['/api/v1/student/attempts/8/draft/', '/api/v1/assessment/process/capture-v2/attempts/8/tools/',
    '/api/v1/assessment/process/capture-v2/attempts/8/resolve/']) {
    const page = new EventEmitter()
    const capture = witness.observeBrowserProtocol(page)
    const event = { event_id: uuid(2), client_sequence_no: 1, session_id: 7, attempt_id: 8,
      event_type: 'response_transition', event_version: 3, app_version: 'capture-v2', ...clocks,
      payload: { after_state: { question_type: 'short_answer', value: 'PRIVATE ANSWER' } } }
    const checkpoint = { checkpoint_id: uuid(8), session_id: 7, client_instance_id: uuid(1), trigger: 'commit', ...clocks }
    const body = { writer_lease_token: 'PRIVATE LEASE', tool: 'check', stage: 'started', operation_id: uuid(3),
      capture_bundle: { version: 1, client_instance_id: uuid(1), events: [event], checkpoints: [checkpoint] } }
    const emitted = request(path, body)
    page.emit('request', emitted)
    page.emit('response', response(emitted, { success: true, acknowledgements: [
      { event_id: uuid(2), client_sequence_no: 1, server_ingest_sequence_no: 22 }], checkpoint_ids: [uuid(8)] }))
    page.emit('requestfinished', emitted)
    await capture.settle(100)
    const saved = capture.evidence.requests[0]
    assert.equal(saved.client_instance_id, uuid(1))
    assert.equal(saved.events.length, 1)
    assert.equal(saved.events[0].material_sha256, digest(event))
    assert.equal(saved.checkpoints[0].material_sha256, digest(checkpoint))
    assert.equal(saved.material_sha256, digest(body))
    assert.equal(saved.acknowledgements[0].event_id, uuid(2))
    assert.deepEqual(saved.checkpoint_ids, [uuid(8)])
    assert.equal(capture.evidence.settlement.complete, true)
    assert.doesNotMatch(JSON.stringify(capture.evidence), /PRIVATE/)
  }
})

test('entry clock retains the actual decimal-string wire representation', () => {
  const path = '/api/v1/student/sessions/7/questions/11/'
  const body = { navigation_id: uuid(4), reason: 'navigation_click', client_occurred_at: clocks.client_wall_time_utc,
    client_monotonic_ms: '12.125', client_time_origin: clocks.client_time_origin }
  assert.equal(witness.browserRequestMaterial(path, body).entry.client_monotonic_ms, '12.125')
  for (const invalid of ['', 'PRIVATE', 'Infinity', 'NaN', {}, -1]) {
    assert.equal(witness.browserRequestMaterial(path, { ...body, client_monotonic_ms: invalid }).entry.client_monotonic_ms, null)
  }
})
function request(path, body) {
  return { method: () => 'POST', url: () => `https://host${path}?private=PRIVATE`, postDataJSON: () => body,
    headers: () => ({ cookie: 'PRIVATE COOKIE', 'x-request-id': uuid(9), 'x-kt-operation-id': uuid(10) }) }
}
function response(row, data, json = async () => ({ data, meta: { request_id: uuid(9) } })) {
  return { request: () => row, status: () => 200, headers: () => ({ 'x-request-id': uuid(9) }), json }
}

test('passive witness never wraps browser fetch and settles event hashes and exact ACK identity', async () => {
  const page = new EventEmitter()
  page.addInitScript = page.exposeBinding = () => { throw new Error('must remain passive') }
  assert.equal(typeof witness.observeBrowserProtocol, 'function')
  const capture = witness.observeBrowserProtocol(page)
  const row = request('/api/v1/assessment/process/capture-v2/batch/', { username: 'PRIVATE ACCOUNT', client_instance_id: uuid(1), events: [{
    event_id: uuid(2), client_sequence_no: 1, session_id: 7, attempt_id: 8, event_type: 'response_transition', event_version: 3,
    app_version: 'capture-v2', correlation_id: uuid(3), ...clocks, payload: { after_state: { value: 'PRIVATE ANSWER' } },
  }] })
  let release
  page.emit('request', row)
  assert.equal(page.emit('response', response(row, {}, () => new Promise(resolve => { release = resolve }))), true)
  let followingFetchCompleted = false
  await Promise.resolve().then(() => { followingFetchCompleted = true })
  assert.equal(followingFetchCompleted, true)
  page.emit('requestfinished', row)
  release({ data: { success: true, acknowledgements: [{ event_id: uuid(2), client_sequence_no: 1, server_ingest_sequence_no: 22 }] } })
  await capture.settle(100)
  const saved = capture.evidence.requests[0]
  assert.equal(saved.events[0].event_id, uuid(2))
  assert.equal(saved.events[0].client_monotonic_ms, 12.125)
  assert.match(saved.events[0].payload_sha256, /^[a-f0-9]{64}$/)
  assert.deepEqual(saved.acknowledgements, [{ event_id: uuid(2), client_sequence_no: 1, server_ingest_sequence_no: 22 }])
  assert.equal(capture.evidence.settlement.complete, true)
  assert.doesNotMatch(JSON.stringify(capture.evidence), /PRIVATE/)
})

test('entry, navigation, submission and checkpoint evidence contains immutable IDs and hashes without credentials', async () => {
  const page = new EventEmitter()
  const capture = witness.observeBrowserProtocol(page)
  const entry = request('/api/v1/student/sessions/7/questions/11/', { navigation_id: uuid(4), reason: 'navigation_click',
    client_occurred_at: clocks.client_wall_time_utc, client_monotonic_ms: 12.125, client_time_origin: clocks.client_time_origin })
  page.emit('request', entry)
  const receipt = { navigation_id: uuid(4), session_id: 7, assignment_id: 11, attempt_id: 8, visit_id: 9, visit_index: 1,
    entry_event_id: uuid(5), entered_at: clocks.client_wall_time_utc }
  page.emit('response', response(entry, { entry_receipt: { ...receipt, answer: 'PRIVATE' }, question: { text: 'PRIVATE' } }))
  page.emit('requestfinished', entry)
  const leave = request('/api/v1/student/attempts/8/draft/', { navigation_token: uuid(6), leave_event_id: uuid(7), target_event_id: uuid(2),
    to_assignment_id: 11, client_clicked_at: clocks.client_wall_time_utc, writer_lease_token: 'PRIVATE LEASE',
    save_and_leave: { version: 1, client_instance_id: uuid(1), events: [], checkpoints: [{ checkpoint_id: uuid(8),
      session_id: 7, client_instance_id: uuid(1), trigger: 'navigation', highest_emitted_sequence_no: 2, pending_event_count: 1 }] } })
  page.emit('request', leave)
  page.emit('response', response(leave, { success: true, navigation_token: uuid(6), response_version: 1,
    active_response_event_id: uuid(2), writer_lease_released: true, acknowledgements: [], checkpoint_ids: [uuid(8)] }))
  page.emit('requestfinished', leave)
  const submit = request('/api/v1/student/attempts/8/submit/', { submission_token: uuid(12), answer: 'PRIVATE ANSWER',
    atomic_submission: { version: 1, client_instance_id: uuid(1), events: [], checkpoints: [] } })
  page.emit('request', submit)
  page.emit('response', response(submit, { success: true, submission_token: uuid(12), status: 'submitted' }))
  page.emit('requestfinished', submit)
  page.emit('request', request('/api/v1/auth/login/', { username: 'PRIVATE', password: 'PRIVATE' }))
  await capture.settle(100)
  assert.equal(capture.evidence.requests.length, 3)
  assert.deepEqual(capture.evidence.requests[0].entry_receipt, receipt)
  assert.equal(capture.evidence.requests[0].success, null, 'full entry DTO does not advertise a mutation success flag')
  assert.equal(capture.evidence.requests[1].navigation.token, uuid(6))
  assert.match(capture.evidence.requests[1].checkpoints[0].material_sha256, /^[a-f0-9]{64}$/)
  assert.equal(capture.evidence.requests[2].submission.token, uuid(12))
  assert.notEqual(capture.evidence.requests[2].submission.material_sha256, capture.evidence.requests[2].submission.bundle_sha256)
  assert.doesNotMatch(JSON.stringify(capture.evidence), /PRIVATE|cookie|username|password|"question"/)
})

test('persisted submit receipt hash excludes dynamic success, feedback and next URL fields', async () => {
  const page = new EventEmitter()
  const capture = witness.observeBrowserProtocol(page)
  const body = { atomic_submission: { version: 1, client_instance_id: uuid(1), events: [] }, target_event_id: uuid(2), confidence: null, submission_token: uuid(12) }
  const row = request('/api/v1/student/attempts/8/submit/', body)
  const persisted = { committed: true, acknowledgements: [], checkpoint_ids: [], final_response_event_id: uuid(2), response_version: 5 }
  page.emit('request', row)
  page.emit('response', response(row, { ...persisted, success: true, next_url: '/student/private/', feedback: { answer: 'PRIVATE' } }))
  page.emit('requestfinished', row)
  await capture.settle(100)
  const saved = capture.evidence.requests[0]
  assert.equal(saved.receipt.persisted_material_sha256, digest(persisted))
  assert.equal(saved.receipt.final_response_event_id, uuid(2))
  assert.equal(saved.submission.material_sha256, digest({ atomic_submission: body.atomic_submission, target_event_id: uuid(2), confidence: null }))
  assert.doesNotMatch(JSON.stringify(capture.evidence), /PRIVATE|student\/private/)
})

test('finally settlement is bounded for unfinished requests and parsing and late responses cannot mutate evidence', async () => {
  const page = new EventEmitter()
  const capture = witness.observeBrowserProtocol(page)
  const row = request('/api/v1/assessment/process/capture-v2/batch/', { client_instance_id: uuid(1), events: [] })
  page.emit('request', row)
  let release
  page.emit('response', response(row, {}, () => new Promise(resolve => { release = resolve })))
  const started = globalThis.performance.now()
  await capture.settle(15)
  assert.ok(globalThis.performance.now() - started < 250)
  assert.equal(capture.evidence.settlement.complete, false)
  const frozen = JSON.stringify(capture.evidence)
  release({ data: { acknowledgements: [{ event_id: uuid(2), client_sequence_no: 1, server_ingest_sequence_no: 22 }] } })
  page.emit('requestfinished', row)
  await Promise.resolve()
  assert.equal(JSON.stringify(capture.evidence), frozen)
})

test('unknown private endpoints, event labels and identity values cannot enter the evidence', () => {
  assert.equal(witness.browserRequestMaterial('/api/v1/assessment/process/private-account/', {}), null)
  const row = witness.browserRequestMaterial('/api/v1/assessment/process/capture-v2/batch/', { client_instance_id: 'private-account', events: [{
    event_id: 'private-account', event_type: 'private_account', payload: { value: 'PRIVATE ANSWER' }, app_version: 'private-account',
  }] })
  assert.equal(row.events[0].event_type, null)
  assert.doesNotMatch(JSON.stringify(row), /private.account|PRIVATE/)
})

test('atomic commit checkpoints retain their legal trigger and candidate binding excludes private manifest fields', () => {
  const capture = witness.observeBrowserProtocol(new EventEmitter(), { candidate: { source_sha256: 'a'.repeat(64), username: 'PRIVATE' }, taskId: 4, probe: 2 })
  assert.equal(capture.evidence.candidate.source_sha256, 'a'.repeat(64))
  assert.equal(capture.evidence.task_id, 4)
  const row = witness.browserRequestMaterial('/api/v1/student/attempts/8/submit/', {
    atomic_submission: { checkpoints: [{ checkpoint_id: uuid(8), client_instance_id: uuid(1), session_id: 7, trigger: 'commit' }] },
  })
  assert.equal(row.checkpoints[0].trigger, 'commit')
  assert.doesNotMatch(JSON.stringify(capture.evidence), /PRIVATE|username/)
})
