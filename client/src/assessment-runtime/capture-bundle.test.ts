import { expect, it } from 'vitest'
import { ProcessEventCollector } from './process-sender'
import { MemoryOutbox } from './fact-store'

const capability = { enabled: true, version: 1 as const, max_events: 100, max_checkpoints: 100, max_bytes: 8388608, mutation_receipts: true }
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done }); return { promise, resolve } }
function collector(fetch: typeof globalThis.fetch) {
  return new ProcessEventCollector({ sessionId: 7, attemptId: 13, endpoint: '/events/', checkpointEndpoint: '/checkpoints/',
    outbox: new MemoryOutbox(), autoSchedule: false, fetch, captureBundle: capability } as never)
}
function receipt(bundle: { events: { client_sequence_no: number; event_id?: string }[]; checkpoints: { checkpoint_id: string }[] }) {
  return { success: true, committed: true, acknowledgements: bundle.events.map(event => ({ client_sequence_no: event.client_sequence_no,
    event_id: event.event_id || crypto.randomUUID(), server_ingest_sequence_no: event.client_sequence_no })), checkpoint_ids: bundle.checkpoints.map(row => row.checkpoint_id) }
}
function response(body: unknown) { return Response.json({ data: body, meta: { request_id: 'test' } }) }

it.each(['offline', 'transport', 'incomplete-ack'])('returns an unconfirmed bundled flush after %s without silently retrying it', async mode => {
  let requests = 0
  let onlineChecks = 0
  const sender = collector(async (_url, init) => {
    const bundle = JSON.parse(String(init?.body)).capture_bundle
    requests++
    if (requests === 1 && mode === 'transport') throw new Error('network unavailable')
    if (requests === 1 && mode === 'incomplete-ack') return response({ ...receipt(bundle), acknowledgements: [] })
    return response(receipt(bundle))
  })
  if (mode === 'offline') sender.isOnline = () => onlineChecks++ > 0
  const event = await sender.record('document_visible', {})
  // The first check belongs to record() quality flags, not the upload.
  onlineChecks = 0
  expect(await sender.flushConfirmed()).toBe(false)
  expect(requests).toBe(mode === 'offline' ? 0 : 1)
  expect(await sender.outbox.pendingEvents(sender.clientInstanceId, 99)).toContainEqual(event)
  expect(await sender.flushConfirmed()).toBe(true)
})

it('freezes a draft request and original own-attempt facts locally, excludes background ownership, and replays after unknown outcome', async () => {
  const sender = collector(async () => { throw new Error('no network during local freeze') })
  const one = await sender.record('response_input_started', { question_type: 'single_choice' }, { eventId: crypto.randomUUID() })
  sender.attemptId = 14
  const foreign = await sender.record('response_input_started', { question_type: 'single_choice' })
  sender.attemptId = 13
  const checkpoint = await sender._createCheckpoint('heartbeat')
  const body = { answer: 'A', target_event_id: one.event_id, expected_version: 0, client_occurred_at: 'fixed-clock' }
  const intent = await sender.freezeCaptureMutation('/draft/', body)
  expect(intent.processSnapshot.events).toEqual([one])
  expect(intent.processSnapshot.checkpoints).toEqual([checkpoint])
  expect(await sender.outbox.peekBatch()).toEqual({ clientInstanceId: sender.clientInstanceId, events: [foreign] })
  await sender.record('activity_heartbeat', {})
  expect(await sender.freezeCaptureMutation('/draft/', body)).toEqual(intent)
  await expect(sender.acknowledgeCaptureMutation(intent, { ...receipt(intent.processSnapshot), acknowledgements: [] })).rejects.toThrow('confirmation')
  expect(await sender.outbox.count()).toBe(3)
  await sender.acknowledgeCaptureMutation(intent, receipt(intent.processSnapshot))
  expect(await sender.outbox.pendingEvents(sender.clientInstanceId, 99)).not.toContainEqual(one)
  expect(await sender.outbox.readMetadata('capture-mutation:7:13')).toBeNull()
})

it('freezes the submission boundary locally during an active upload before later heartbeats', async () => {
  const flight = deferred<void>()
  const started = deferred<void>()
  const sender = collector(async (_url, init) => { const body = JSON.parse(String(init?.body)); started.resolve(); await flight.promise; return response(receipt(body.capture_bundle || body)) })
  const first = await sender.record('document_visible', {})
  const uploading = sender.flush()
  await started.promise
  const freezing = sender.freezeSubmissionSnapshot({ token: 'fixed', answer: 'A' })
  let frozen = false
  void freezing.then(() => { frozen = true })
  await new Promise(resolve => setTimeout(resolve, 0))
  expect(frozen).toBe(true)
  const heartbeat = await sender.record('activity_heartbeat', {})
  const snapshot = (await freezing).processSnapshot
  expect(snapshot.event_boundary).toBe(first.client_sequence_no)
  expect(snapshot.events).toEqual([first])
  flight.resolve()
  await uploading
  expect((await sender.outbox.pendingEvents(sender.clientInstanceId, 99))).toContainEqual(heartbeat)
})

it('batches existing immutable checkpoint facts and creates ACK progress only after committed exact event ACK', async () => {
  const bodies: unknown[] = []
  const sender = collector(async (url, init) => { expect(String(url)).toBe('/events/'); const body = JSON.parse(String(init?.body)); bodies.push(body); return response(receipt(body.capture_bundle)) })
  const event = await sender.record('document_visible', {})
  const one = await sender._createCheckpoint('heartbeat')
  const two = await sender._createCheckpoint('resume')
  expect(await sender.flush()).toBe(true)
  expect(bodies).toHaveLength(2)
  expect(bodies[0]).toEqual({ capture_bundle: { version: 1, client_instance_id: sender.clientInstanceId, event_boundary: 1, events: [event], checkpoints: [one, two] } })
  expect(bodies[1]).toMatchObject({ capture_bundle: { events: [], checkpoints: [{ trigger: 'ack_progress', highest_contiguous_acked_sequence_no: 1 }] } })
  expect(await sender.outbox.pendingCheckpoints()).toEqual([])
})

it.each(['partial', 'wrong-event', 'unknown-checkpoint', 'missing-ordinal', 'missing-uuid'])('retains every frozen bundle fact on %s confirmation and replays identical material', async mode => {
  const bodies: unknown[] = []
  let valid = false
  const sender = collector(async (_url, init) => { const body = JSON.parse(String(init?.body)); bodies.push(body); const result = receipt(body.capture_bundle)
    if (!valid) {
      if (mode === 'partial') result.acknowledgements.pop()
      if (mode === 'wrong-event') result.acknowledgements[0]!.event_id = 'wrong'
      if (mode === 'unknown-checkpoint') result.checkpoint_ids.push('unknown')
      if (mode === 'missing-ordinal') delete (result.acknowledgements[0] as { server_ingest_sequence_no?: number }).server_ingest_sequence_no
      if (mode === 'missing-uuid') delete (result.acknowledgements[0] as { event_id?: string }).event_id
    }
    return response(result)
  })
  await sender.record('response_input_started', { question_type: 'single_choice' }, mode === 'missing-uuid' ? {} : { eventId: crypto.randomUUID() })
  const checkpoint = await sender._createCheckpoint('heartbeat')
  expect(await sender.flush()).toBe(false)
  expect(await sender.outbox.count()).toBe(1)
  expect(await sender.outbox.pendingCheckpoints()).toEqual([checkpoint])
  valid = true
  expect(await sender.flush()).toBe(true)
  expect(bodies[1]).toEqual(bodies[0])
})

it('a confirmed frozen boundary does not wait for a later background checkpoint flight', async () => {
  const flight = deferred<void>()
  const started = deferred<void>()
  let block = false
  const sender = collector(async (_url, init) => { const body = JSON.parse(String(init?.body)); if (block) { started.resolve(); await flight.promise } return response(receipt(body.capture_bundle)) })
  await sender.record('document_visible', {})
  expect(await sender.flush()).toBe(true)
  const boundary = { [sender.clientInstanceId]: 1 }
  await sender.record('activity_heartbeat', {})
  await sender._createCheckpoint('heartbeat')
  block = true
  const background = sender.flush()
  await started.promise
  let settled = false
  const required = sender.flushConfirmed({ boundary }).then(value => { settled = true; return value })
  await new Promise(resolve => setTimeout(resolve, 0))
  expect(settled).toBe(true)
  expect(await required).toBe(true)
  flight.resolve()
  await background
})

it.each(['11111111111141118111111111111111', undefined])('retains the operation ID %s captured by a queued checkpoint when another operation starts', async originalId => {
  const flight = deferred<void>()
  const started = deferred<void>()
  const headers: Array<Record<string, string>> = []
  const sender = new ProcessEventCollector({ sessionId: 7, attemptId: 13, endpoint: '/events/', checkpointEndpoint: '/checkpoints/',
    outbox: new MemoryOutbox(), autoSchedule: false,
    fetch: async (_url, init) => { headers.push(init?.headers as Record<string, string>); const body = JSON.parse(String(init?.body)); if (headers.length === 1) { started.resolve(); await flight.promise } return response({ success: true, checkpoint_id: body.checkpoint_id }) } })
  sender.setOperationId(originalId)
  const first = sender.checkpoint('heartbeat')
  await started.promise
  const second = sender.checkpoint('resume')
  await new Promise(resolve => setTimeout(resolve, 0))
  sender.setOperationId('22222222222242228222222222222222')
  flight.resolve()
  await Promise.all([first, second])
  expect(headers.map(row => row['X-KT-Operation-ID'])).toEqual([originalId, originalId])
})

it('replays an owned committed bundle after the feature is disabled, then drains later facts through legacy requests', async () => {
  const requests: Array<{ url: string; body: string }> = []
  let committed = false
  let originalReceipt: ReturnType<typeof receipt> | undefined
  const sender = collector(async (url, init) => {
    const raw = String(init?.body)
    const body = JSON.parse(raw)
    requests.push({ url: String(url), body: raw })
    if (body.capture_bundle) {
      if (!committed) { committed = true; originalReceipt = receipt(body.capture_bundle); throw new Error('COMMIT response lost') }
      return response(originalReceipt)
    }
    if (String(url) === '/checkpoints/') return response({ success: true, checkpoint_id: body.checkpoint_id })
    expect(await sender.outbox.readMetadata('capture-flush:7')).toBeNull()
    expect(await sender.outbox.count()).toBe(1)
    return response(receipt({ events: body.events, checkpoints: [] }))
  })
  const original = await sender.record('document_visible', {})
  const checkpoint = await sender._createCheckpoint('heartbeat')
  expect(await sender.flush()).toBe(false)
  const frozen = await sender.outbox.readMetadata('capture-flush:7')
  expect(await sender.outbox.count()).toBe(1)
  expect(await sender.outbox.pendingCheckpoints()).toEqual([checkpoint])
  const later = await sender.record('activity_heartbeat', {})
  sender.captureBundle = { ...capability, enabled: false, recovery_supported: true }
  expect(await sender.flushConfirmed()).toBe(true)
  expect(requests[1]).toEqual(requests[0])
  expect(JSON.parse(requests[1]!.body).capture_bundle.events).toEqual([original])
  expect(JSON.parse(requests[2]!.body)).toEqual({ client_instance_id: sender.clientInstanceId, events: [later] })
  expect(requests.filter(row => JSON.parse(row.body).capture_bundle)).toHaveLength(2)
  expect(frozen).toBeTruthy()
  expect(await sender.outbox.count()).toBe(0)
  expect(await sender.outbox.pendingCheckpoints()).toEqual([])
})

it.each([undefined, { ...capability, enabled: false }])('fails closed on an owned bundle when rollback server lacks recovery support: %s', async rollbackCapability => {
  let calls = 0
  const sender = collector(async () => { calls += 1; throw new Error('COMMIT response lost') })
  const original = await sender.record('document_visible', {})
  expect(await sender.flush()).toBe(false)
  const frozen = await sender.outbox.readMetadata('capture-flush:7')
  sender.captureBundle = rollbackCapability
  expect(await sender.flush()).toBe(false)
  expect(await sender.flushConfirmed()).toBe(false)
  expect(calls).toBe(1)
  expect(await sender.outbox.readMetadata('capture-flush:7')).toEqual(frozen)
  expect(await sender.outbox.pendingEvents(sender.clientInstanceId, 99)).toEqual([original])
  // Local event ACK alone cannot confirm the still-owned packet on an old server.
  await sender.outbox.deleteAcknowledged(sender.clientInstanceId, [original.client_sequence_no])
  sender.checkpointEndpoint = ''
  expect(await sender.flushConfirmed()).toBe(false)
  expect(await sender.outbox.readMetadata('capture-flush:7')).toEqual(frozen)
})
