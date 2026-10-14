import assert from 'node:assert/strict'
import test from 'node:test'
import { EventEmitter } from 'node:events'
import { captureProtocol, digest, safeRequest, projectPayload } from './capture-protocol-witness.mjs'

test('protocol settlement records interrupted bodies within its budget and freezes late evidence', { timeout: 1000 }, async () => {
  const page = new EventEmitter()
  page.exposeBinding = async () => {}
  page.addInitScript = async () => {}
  const witness = await captureProtocol(page)
  const request = { method: () => 'POST', url: () => 'http://local/api/v1/assessment/process/capture-v2/batch/',
    postDataJSON: () => ({ client_instance_id: 'client', events: [] }) }
  page.emit('request', request)
  let release
  page.emit('response', { request: () => request, status: () => 200,
    json: () => new Promise(resolve => { release = resolve }) })
  page.emit('requestfailed', request)
  const settlement = await witness.settle(15)
  assert.equal(settlement.complete, false)
  assert.equal(settlement.pending_response_count, 1)
  assert.equal(witness.requests[0].transport_failure, true)
  assert.equal(witness.requests[0].response_json_unavailable, true)
  const saved = JSON.stringify(witness)
  release({ data: { success: true, acknowledgements: [] } })
  await new Promise(resolve => globalThis.setTimeout(resolve, 5))
  page.emit('request', request)
  assert.equal(JSON.stringify(witness), saved)
})

test('protocol settlement retains normally resolved response evidence', async () => {
  const page = new EventEmitter()
  page.exposeBinding = async () => {}
  page.addInitScript = async () => {}
  const witness = await captureProtocol(page)
  const request = { method: () => 'POST', url: () => 'http://local/api/v1/assessment/process/capture-v2/batch/',
    postDataJSON: () => ({ client_instance_id: 'client', events: [] }) }
  page.emit('request', request)
  page.emit('response', { request: () => request, status: () => 200, json: async () => ({ data: {
    success: true, acknowledgements: [{ event_id: 'event', client_sequence_no: 1, server_ingest_sequence_no: 2 }] } }) })
  assert.equal((await witness.settle(100)).complete, true)
  assert.equal(witness.requests[0].success, true)
  assert.equal(witness.requests[0].acknowledgements[0].event_id, 'event')
})

test('bundled draft witnesses retain event identities and payload hashes without answer values', () => {
  const row = safeRequest('/api/v1/student/attempts/4/draft/', {
    answer: 'PRIVATE ANSWER', writer_lease_token: 'PRIVATE LEASE',
    capture_bundle: { version: 1, client_instance_id: 'client', events: [{
      event_id: 'target', client_sequence_no: 3, session_id: 7, attempt_id: 4,
      event_type: 'response_transition', payload: { after_state: { value: 'PRIVATE ANSWER' } },
    }] },
  })
  assert.equal(row.client_instance_id, 'client')
  assert.equal(row.events[0].event_id, 'target')
  assert.equal(row.events[0].payload_sha256, digest({ after_state: { value: 'PRIVATE ANSWER' } }))
  assert.ok(!JSON.stringify(row).includes('PRIVATE'))
})

test('witness excludes authentication, answers and lease tokens while retaining event identity', () => {
  assert.equal(safeRequest('/login/', { password: 'secret' }), null)
  const body = { writer_lease_token: 'secret-token', answer: 'secret-answer', client_instance_id: 'client', events: [
    { event_id: 'event', client_sequence_no: 2, session_id: 3, attempt_id: 4, event_type: 'response_transition', payload: { after_state: { value: 'secret-answer' } } },
  ] }
  const row = safeRequest('/api/v1/assessment/process/capture-v2/batch/', body)
  assert.equal(row.events[0].event_id, 'event')
  assert.equal(row.has_writer_lease, true)
  assert.doesNotMatch(JSON.stringify(row), /secret/)
})
test('hashes are order independent and explicit snapshot enrichment preserves semantic digest', () => {
  assert.equal(digest({ b: 2, a: 1 }), digest({ a: 1, b: 2 }))
  const payload = { before_state: { value: '' }, after_state: { value: 'A' } }
  assert.equal(digest(payload), digest(projectPayload({ ...payload, before_snapshot_id: 'before', after_snapshot_id: 'after' }, Object.keys(payload), 'response_transition')))
  assert.throws(() => projectPayload({ ...payload, unexpected: true }, Object.keys(payload), 'response_transition'))
  assert.notEqual(digest(payload), digest(projectPayload({ before_state: { value: '' }, after_state: { value: 'B' } }, Object.keys(payload), 'response_transition')))
})

test('atomic witness retains envelope event identities without recording answers or tokens', () => {
  const row = safeRequest('/api/v1/student/attempts/4/submit/', {
    submission_token: 'secret-submit', writer_lease_token: 'secret-lease', answer: 'secret-answer',
    atomic_submission: { version: 1, client_instance_id: 'atomic-client', events: [{
      event_id: 'atomic-event', client_sequence_no: 3, session_id: 7, attempt_id: 4,
      event_type: 'response_transition', payload: { after_state: { value: 'secret-answer' } },
    }] },
  })
  assert.equal(row.client_instance_id, 'atomic-client')
  assert.equal(row.events[0].event_id, 'atomic-event')
  assert.doesNotMatch(JSON.stringify(row), /secret/)
})
