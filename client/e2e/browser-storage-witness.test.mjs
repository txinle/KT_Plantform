import test from 'node:test'
import assert from 'node:assert/strict'
import * as storage from './browser-storage-witness.mjs'
import { browserRequestMaterial, browserStorageContract } from './browser-network-witness.mjs'
const snapshotStorage = options => storage.readOnlyBrowserStorage({ ...browserStorageContract(), ...options })

const uuid = number => `${String(number).padStart(8, '0')}-0000-4000-8000-000000000000`
const rawEvent = sequence => ({ event_id: uuid(sequence + 10), client_sequence_no: sequence, session_id: 7, attempt_id: 8,
  event_type: 'response_transition', event_version: 1, client_wall_time_utc: '2026-10-12T03:00:00.000Z',
  client_time_origin: '2026-10-12T02:00:00.000Z', client_monotonic_ms: 12.125, time_sync_id: null,
  app_version: 'capture-v2', quality_flags: [], payload: { after_state: { value: 'PRIVATE ANSWER' } } })
const record = sequence => ({ client_instance_id: uuid(1), client_sequence_no: sequence, event: rawEvent(sequence) })
function fakeDatabase(rows, { hang = false, failed = false, absent = false } = {}) {
  const calls = []
  const database = { version: 2, objectStoreNames: { contains: name => name in rows }, close: () => calls.push('close'),
    transaction: (names, mode) => {
      calls.push({ names, mode })
      assert.equal(mode, 'readonly')
      const transaction = { abort: () => { calls.push('abort'); transaction.onabort?.() }, objectStore: name => ({ getAll: () => {
        calls.push(`getAll:${name}`)
        const request = {}
        globalThis.queueMicrotask(() => {
          if (hang) return
          if (failed) { request.onerror?.(); return }
          request.result = globalThis.structuredClone(rows[name])
          request.onsuccess?.()
          if (++completed === names.length) globalThis.queueMicrotask(() => transaction.oncomplete?.())
        })
        return request
      } }) }
      let completed = 0
      return transaction
    } }
  return { calls, indexedDB: { databases: async () => absent ? [] : [{ name: 'kt-process-events-v1', version: 2 }], open: (...args) => {
    calls.push({ open: args })
    const request = {}
    globalThis.queueMicrotask(() => { request.result = database; request.onsuccess?.() })
    return request
  } } }
}
async function withDatabase(fixture, operation) {
  const original = globalThis.indexedDB
  globalThis.indexedDB = fixture.indexedDB
  try { return await operation() } finally {
    if (original === undefined) delete globalThis.indexedDB
    else globalThis.indexedDB = original
  }
}
function rows() {
  return { events: [record(2)], dead_letters: [{ ...record(3), rejection: { message: 'PRIVATE ERROR' } }], checkpoints: [],
    meta: [{ key: `next-sequence:${uuid(1)}`, value: 5 }, { key: `acknowledged:${uuid(1)}`, value: [1] },
      { key: 'username', value: 'PRIVATE ACCOUNT' }] }
}

test('one readonly transaction hashes unsent and failed facts without exposing raw values or altering storage', async () => {
  assert.equal(typeof storage.readOnlyBrowserStorage, 'function')
  const data = rows()
  const before = JSON.stringify(data)
  const fixture = fakeDatabase(data)
  const result = await withDatabase(fixture, () => snapshotStorage({ sessionIds: [7], clientInstanceIds: [uuid(1)] }))
  assert.equal(result.read_complete, true)
  assert.equal(result.scope_complete, true)
  assert.deepEqual(result.events.map(event => event.delivery_status), ['pending', 'failed'])
  const network = browserRequestMaterial('/api/v1/assessment/process/capture-v2/batch/', { client_instance_id: uuid(1), events: [rawEvent(2)] })
  for (const [key, value] of Object.entries(network.events[0])) assert.deepEqual(result.events[0][key], value, key)
  assert.deepEqual(result.streams[0].acknowledged_sequence_nos, [1])
  assert.equal(result.streams[0].highest_emitted_sequence_no, 4)
  assert.equal(result.streams[0].highest_contiguous_acked_sequence_no, 1)
  assert.deepEqual(result.streams[0].missing_local_sequence_ranges, [[4, 4]])
  assert.equal(result.boundary.pending_event_count, 1)
  assert.equal(result.boundary.failed_event_count, 1)
  assert.doesNotMatch(JSON.stringify(result), /PRIVATE|username|cookie/)
  assert.equal(JSON.stringify(data), before)
  assert.equal(fixture.calls.filter(call => typeof call === 'object' && call.mode).length, 1)
})

test('ordinary stored events use the durable client and sequence before a server UUID exists', async () => {
  const data = rows()
  delete data.events[0].event.event_id
  delete data.dead_letters[0].event.event_id
  const result = await withDatabase(fakeDatabase(data), () => snapshotStorage({ sessionIds: [7], clientInstanceIds: [uuid(1)] }))
  assert.equal(result.read_complete, true)
  assert.equal(result.scope_complete, true)
  assert.deepEqual(result.events.map(row => [row.event_id, row.client_sequence_no]), [[null, 2], [null, 3]])
  data.events[0].event.event_id = 'invalid supplied UUID'
  const invalid = await withDatabase(fakeDatabase(data), () => snapshotStorage({ sessionIds: [7], clientInstanceIds: [uuid(1)] }))
  assert.equal(invalid.read_complete, false)
})

test('snapshot retains unconfirmed original entry, navigation and submission material hashes', async () => {
  const data = rows()
  const snapshot = { client_instance_id: uuid(1), event_boundary: 2, events: [rawEvent(2)], checkpoints: [] }
  data.meta.push({ key: 'navigation-intent:7:8', value: { token: uuid(21), clickedAt: '2026-10-12T03:00:00.000Z',
    head: uuid(12), leaveEventId: uuid(13), toAssignmentId: 11, expectedResponseVersion: 5, processSnapshot: snapshot, writerLeaseToken: 'PRIVATE LEASE' } },
  { key: 'submission-intent:7:8', value: { token: uuid(22), head: uuid(12), answer: 'PRIVATE ANSWER', expectedResponseVersion: 5, confidence: null, processSnapshot: snapshot } },
  { key: `entry-intent:7:${uuid(23)}`, value: { navigationId: uuid(24), assignmentId: 11, reason: 'navigation_click',
    clientOccurredAt: '2026-10-12T03:00:00.000Z', clientMonotonicMs: 12.125, clientTimeOrigin: '2026-10-12T02:00:00.000Z', targetUrl: '/student/private/' } })
  const result = await withDatabase(fakeDatabase(data), () => snapshotStorage({ sessionIds: [7], clientInstanceIds: [uuid(1)] }))
  assert.equal(result.intents.length, 3)
  const navigation = result.intents.find(intent => intent.kind === 'navigation')
  const expected = browserRequestMaterial('/api/v1/student/attempts/8/draft/', { navigation_token: uuid(21), leave_event_id: uuid(13),
    target_event_id: uuid(12), to_assignment_id: 11, client_clicked_at: '2026-10-12T03:00:00.000Z',
    save_and_leave: { version: 1, ...snapshot, expected_version: 5 } })
  assert.equal(navigation.material_sha256, expected.navigation.material_sha256)
  assert.deepEqual(navigation.material, expected.navigation)
  assert.equal(navigation.snapshot.events[0].payload_sha256, expected.events[0].payload_sha256)
  const submission = browserRequestMaterial('/api/v1/student/attempts/8/submit/', { submission_token: uuid(22), target_event_id: uuid(12),
    atomic_submission: { version: 1, ...snapshot, expected_version: 5 } })
  assert.deepEqual(result.intents.find(intent => intent.kind === 'submission').material, submission.submission)
  const entry = browserRequestMaterial('/api/v1/student/sessions/7/questions/11/', { navigation_id: uuid(24), reason: 'navigation_click',
    client_occurred_at: '2026-10-12T03:00:00.000Z', client_monotonic_ms: '12.125', client_time_origin: '2026-10-12T02:00:00.000Z' })
  assert.deepEqual(result.intents.find(intent => intent.kind === 'entry').material, entry.entry)
  assert.doesNotMatch(JSON.stringify(result), /PRIVATE|student\/private/)
})

test('checkpoint projection matches network hashes and session filtering excludes unrelated facts', async () => {
  const data = rows()
  const checkpoint = { checkpoint_id: uuid(40), client_instance_id: uuid(1), session_id: 7, trigger: 'commit',
    client_wall_time_utc: '2026-10-12T03:00:00.000Z', client_monotonic_ms: 12.125, client_time_origin: '2026-10-12T02:00:00.000Z',
    highest_emitted_sequence_no: 4, highest_acked_sequence_no: 1, highest_contiguous_acked_sequence_no: 1,
    pending_event_count: 1, failed_event_count: 1, app_version: 'PRIVATE VERSION', quality_flags: ['PRIVATE FLAG'], missing_local_sequence_ranges: [[4, 4]] }
  data.checkpoints.push(checkpoint, { ...checkpoint, session_id: 99, client_instance_id: uuid(99) })
  data.events.push({ ...record(9), event: { ...rawEvent(9), session_id: 99 } })
  const result = await withDatabase(fakeDatabase(data), () => snapshotStorage({ sessionIds: [7], clientInstanceIds: [uuid(1)] }))
  const network = browserRequestMaterial('/api/v1/assessment/process/capture-checkpoints/', checkpoint)
  for (const [key, value] of Object.entries(network.checkpoints[0])) assert.deepEqual(result.checkpoints[0][key], value, key)
  assert.equal(result.checkpoints.length, 1)
  assert.equal(result.events.length, 2)
  assert.doesNotMatch(JSON.stringify(result), /PRIVATE/)
})

test('incomplete intent material and unscoped streams remain unknown rather than an empty successful queue', async () => {
  const data = rows()
  data.meta.push({ key: 'submission-intent:7:8', value: { token: uuid(21), answer: 'PRIVATE ANSWER' } })
  let result = await withDatabase(fakeDatabase(data), () => snapshotStorage({ sessionIds: [7], clientInstanceIds: [uuid(1)] }))
  assert.equal(result.read_complete, true)
  assert.equal(result.status, 'unknown')
  assert.equal(result.scope_complete, false)
  assert.equal(result.intents[0].material, null)
  assert.equal(result.intents[0].material_sha256, null)
  data.meta.pop()
  data.meta.push({ key: `next-sequence:${uuid(99)}`, value: 5 })
  result = await withDatabase(fakeDatabase(data), () => snapshotStorage({ sessionIds: [7], clientInstanceIds: [uuid(1)] }))
  assert.equal(result.status, 'unknown')
  assert.equal(result.boundary.unscoped_client_count, 1)
  data.meta.pop()
  data.meta.find(row => row.key.startsWith('acknowledged:')).value = [100]
  result = await withDatabase(fakeDatabase(data), () => snapshotStorage({ sessionIds: [7], clientInstanceIds: [uuid(1)] }))
  assert.equal(result.read_complete, false)
  assert.equal(result.status, 'unknown')
})

test('page closure or an unresolved browser evaluation has a bounded unknown result', async () => {
  for (const page of [{ evaluate: async () => { throw new Error('PRIVATE URL') } }, { evaluate: () => new Promise(() => {}) }]) {
    const started = globalThis.performance.now()
    const result = await storage.captureBrowserStorage(page, { session_ids: [7], requests: [] }, 15)
    assert.equal(result.read_complete, false)
    assert.equal(result.status, 'unknown')
    assert.ok(globalThis.performance.now() - started < 250)
    assert.doesNotMatch(JSON.stringify(result), /PRIVATE/)
  }
})

test('corrupt sequence identity and entry clocks cannot masquerade as complete material', async () => {
  const data = rows()
  data.events[0].event.client_sequence_no = null
  let result = await withDatabase(fakeDatabase(data), () => snapshotStorage({ sessionIds: [7], clientInstanceIds: [uuid(1)] }))
  assert.equal(result.status, 'unknown')
  assert.equal(result.read_complete, false)
  data.events[0].event.client_sequence_no = 2
  data.meta.push({ key: `entry-intent:7:${uuid(23)}`, value: { navigationId: uuid(24), assignmentId: 11, reason: 'navigation_click',
    clientOccurredAt: 'not a clock', clientMonotonicMs: 12.125, clientTimeOrigin: '2026-10-12T02:00:00.000Z' } })
  result = await withDatabase(fakeDatabase(data), () => snapshotStorage({ sessionIds: [7], clientInstanceIds: [uuid(1)] }))
  assert.equal(result.status, 'unknown')
  assert.equal(result.intents[0].material, null)
})

test('absent, unreadable and hung storage report unknown and never create a database', async () => {
  for (const options of [{ absent: true }, { failed: true }, { hang: true }]) {
    const fixture = fakeDatabase(rows(), options)
    const started = globalThis.performance.now()
    const result = await withDatabase(fixture, () => snapshotStorage({ sessionIds: [7], clientInstanceIds: [uuid(1)], timeoutMs: 15 }))
    assert.equal(result.read_complete, false)
    assert.equal(result.status, 'unknown')
    assert.ok(globalThis.performance.now() - started < 250)
    assert.doesNotMatch(JSON.stringify(result), /PRIVATE/)
    if (options.absent) assert.equal(fixture.calls.length, 0)
  }
})
