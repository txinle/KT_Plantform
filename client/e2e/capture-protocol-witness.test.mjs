import assert from 'node:assert/strict'
import test from 'node:test'
import { digest, safeRequest, projectPayload } from './capture-protocol-witness.mjs'

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
