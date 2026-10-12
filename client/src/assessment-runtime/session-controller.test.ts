import { describe, expect, it, vi } from 'vitest'
import { createApiClient } from '@/api/client'
import * as assessmentApi from './api'
import { createSessionController } from './session-controller'
import { MemoryOutbox } from './fact-store'
import { ProcessEventCollector } from './process-sender'
vi.mock('@/app/bootstrap', () => ({ getBootstrapContext: () => ({ csrf_token: 'csrf' }) }))

const process = { session_id: 7, attempt_id: 13, event_batch_url: '/api/v1/assessment/process/events/batch/',
  checkpoint_url: '', time_sync_create_url: '', time_sync_finalize_url: '', app_version: 'test',
  heartbeat_interval_seconds: 30, navigation_id: '00000000-0000-4000-8000-000000000010', capture_version: 'capture-v2',
  response_version: 0, writer_lease_acquire_url: '/api/v1/assessment/process/attempts/13/writer-lease/acquire/',
  writer_lease_renew_url: '/api/v1/assessment/process/attempts/13/writer-lease/renew/', writer_lease_release_url: '/api/v1/assessment/process/attempts/13/writer-lease/release/' }

it.each(['navigation', 'submission'])('initializes a reloaded page without awaiting its held %s snapshot', async kind => {
  const store = new MemoryOutbox()
  const capability = { enabled: true, version: 1 as const, max_events: 100, max_checkpoints: 100, max_bytes: 8388608 }
  const previous = new ProcessEventCollector({ sessionId: 7, attemptId: 13, endpoint: '/events/',
    checkpointEndpoint: '/checkpoints/', outbox: store, autoSchedule: false, captureBundle: capability })
  const event = await previous.record('document_visible', {})
  const frozen = kind === 'navigation'
    ? await previous.freezeNavigationSnapshot({ token: 'held-navigation', toAssignmentId: 14 })
    : await previous.freezeSubmissionSnapshot({ token: 'held-submission', answer: 'A' })
  const captureState = store.captureState.bind(store)
  vi.spyOn(store, 'captureState').mockImplementation(id => {
    if (id === previous.clientInstanceId) throw new Error('Initialization awaited the held recovery stream')
    return captureState(id)
  })
  const controller = createSessionController({ store, csrfToken: 'csrf', autoSchedule: false })
  controller.sender = new ProcessEventCollector({ sessionId: 7, attemptId: 13, endpoint: '/events/',
    checkpointEndpoint: '/checkpoints/', outbox: store, autoSchedule: false, captureBundle: capability,
    fetch: async (_url, init) => {
      const bundle = JSON.parse(String(init?.body)).capture_bundle
      return Response.json({ data: { success: true, committed: true,
        acknowledgements: bundle.events.map((row: { event_id: string; client_sequence_no: number }) => ({
          event_id: row.event_id || crypto.randomUUID(), client_sequence_no: row.client_sequence_no, server_ingest_sequence_no: row.client_sequence_no,
        })), checkpoint_ids: bundle.checkpoints.map((row: { checkpoint_id: string }) => row.checkpoint_id),
      }, meta: { request_id: 'test' } })
    } })
  try {
    const runtime = await controller.activate({ ...process, checkpoint_url: '/checkpoints/', capture_bundle: capability } as never, 'visited')
    expect(kind === 'navigation' ? runtime.restoredNavigation : runtime.restoredSubmission).toEqual(frozen)
    expect(await store.pendingEvents(previous.clientInstanceId, 99)).toContainEqual(event)
    expect(kind === 'navigation' ? await store.restoreNavigationIntent(7, 13) : await store.restoreSubmissionIntent(7, 13)).toEqual(frozen)
  } finally { controller.dispose() }
})

it('recovers an already dispatched bundled draft before a later navigation and promotes CAS only after complete ACK', async () => {
  const store = new MemoryOutbox()
  const controller = createSessionController({ store, csrfToken: 'csrf', autoSchedule: false })
  const runtime = await controller.activate({ ...process, capture_bundle: { enabled: true, version: 1, max_events: 100, max_checkpoints: 100, max_bytes: 8388608, mutation_receipts: true } } as never, 'visited')
  const event = await runtime.record('response_input_started', { question_type: 'single_choice' }, { eventId: crypto.randomUUID() })
  const body = { answer: 'A', target_event_id: event.event_id, expected_version: 0, client_occurred_at: 'original-clock',
    writer_lease_token: 'expired-after-commit', writer_client_instance_id: runtime.clientInstanceId }
  await expect(runtime.captureMutation!('/draft/', body, async () => { throw new Error('unknown outcome') })).rejects.toThrow('unknown')
  const saved = await store.readMetadata<import('./capture-contract').CaptureMutation>('capture-mutation:7:13')
  runtime.writerLease?.invalidate()
  expect(saved?.body).toMatchObject(body)
  expect(saved?.body.capture_operation_id).toEqual(expect.any(String))
  const post = vi.fn().mockImplementation(async (_url, frozen) => ({ success: true, committed: true, active_response_event_id: event.event_id, response_version: 1,
    acknowledgements: [], checkpoint_ids: frozen.capture_bundle.checkpoints.map((row: { checkpoint_id: string }) => row.checkpoint_id) }))
  const spy = vi.spyOn(assessmentApi, 'getStudentAssessmentApi').mockReturnValue({ post } as never)
  try {
    await expect(runtime.pointerWritesSettled()).rejects.toThrow('confirmation')
    expect(runtime.responseVersion).toBe(0)
    expect(await store.readMetadata('capture-mutation:7:13')).toEqual(saved)
    post.mockImplementation(async (_url, frozen) => ({ success: true, committed: true, active_response_event_id: event.event_id, response_version: 1,
      acknowledgements: frozen.capture_bundle.events.map((row: { client_sequence_no: number; event_id?: string }) => ({ client_sequence_no: row.client_sequence_no, event_id: row.event_id || crypto.randomUUID(), server_ingest_sequence_no: row.client_sequence_no })),
      checkpoint_ids: frozen.capture_bundle.checkpoints.map((row: { checkpoint_id: string }) => row.checkpoint_id) }))
    await runtime.pointerWritesSettled()
    expect(post).toHaveBeenLastCalledWith('/draft/', { ...saved?.body, capture_bundle: saved?.processSnapshot })
    expect(runtime.responseVersion).toBe(1)
    expect(await store.readMetadata('capture-mutation:7:13')).toBeNull()
  } finally { spy.mockRestore(); controller.dispose() }
})

it('confirms the previous document stream after draft replay before sending the reloaded document pointer', async () => {
  const store = new MemoryOutbox()
  const capability = { enabled: true, version: 1 as const, max_events: 100, max_checkpoints: 100, max_bytes: 8388608, mutation_receipts: true }
  const previous = new ProcessEventCollector({ sessionId: 7, attemptId: 13, endpoint: '/events/',
    checkpointEndpoint: '/checkpoints/', outbox: store, autoSchedule: false, captureBundle: capability })
  const oldHead = await previous.record('response_input_started', { question_type: 'short_answer' }, { eventId: crypto.randomUUID() })
  const pending = await previous.freezeCaptureMutation('/draft/', { answer: 'A', target_event_id: oldHead.event_id,
    expected_version: 0, writer_lease_token: 'old-lease', writer_client_instance_id: previous.clientInstanceId })
  const parent = await previous.record('activity_heartbeat', {})
  const acknowledgement = (bundle: typeof pending.processSnapshot) => ({ success: true, committed: true,
    acknowledgements: bundle.events.map(row => ({ event_id: row.event_id || crypto.randomUUID(), client_sequence_no: row.client_sequence_no, server_ingest_sequence_no: row.client_sequence_no })),
    checkpoint_ids: bundle.checkpoints.map(row => row.checkpoint_id) })
  let replayed = false
  const controller = createSessionController({ store, csrfToken: 'csrf', autoSchedule: false })
  controller.sender = new ProcessEventCollector({ sessionId: 7, attemptId: 13, endpoint: '/events/',
    checkpointEndpoint: '/checkpoints/', outbox: store, autoSchedule: false, captureBundle: capability,
    fetch: async (_url, init) => {
      const bundle = JSON.parse(String(init?.body)).capture_bundle
      if (bundle.client_instance_id === previous.clientInstanceId && !replayed) return Response.json({ error: { code: 'parent_not_received' } }, { status: 409 })
      return Response.json({ data: acknowledgement(bundle), meta: { request_id: 'test' } })
    } })
  const spy = vi.spyOn(assessmentApi, 'getStudentAssessmentApi').mockReturnValue({ post: async () => {
    replayed = true
    return { ...acknowledgement(pending.processSnapshot), active_response_event_id: oldHead.event_id, response_version: 1 }
  } } as never)
  try {
    const runtime = await controller.activate({ ...process, checkpoint_url: '/checkpoints/', capture_bundle: capability } as never, 'visited')
    await controller.sender.flushPromise
    const head = await runtime.record('response_input_started', { question_type: 'short_answer' }, { eventId: crypto.randomUUID() })
    expect(await store.pendingEvents(previous.clientInstanceId, 99)).toContainEqual(parent)
    await runtime.captureMutation!('/draft/', { answer: 'AB', target_event_id: head.event_id, expected_version: 0,
      writer_lease_token: 'new-lease', writer_client_instance_id: runtime.clientInstanceId }, async body => {
      expect(await store.pendingEvents(previous.clientInstanceId, 99)).toEqual([])
      expect(body.expected_version).toBe(1)
      return { ...acknowledgement(body.capture_bundle as typeof pending.processSnapshot), active_response_event_id: head.event_id, response_version: 2 }
    })
    expect(runtime.responseVersion).toBe(2)
  } finally { spy.mockRestore(); controller.dispose() }
})

it('keeps the legacy sender path when the question has no bundle capability', async () => {
  const controller = createSessionController({ store: new MemoryOutbox(), csrfToken: 'csrf', autoSchedule: false })
  try { expect((await controller.activate(process as never, 'visited')).captureMutation).toBeUndefined() }
  finally { controller.dispose() }
})

it('freezes original draft facts before waiting for a lease renewal', async () => {
  const store = new MemoryOutbox()
  const controller = createSessionController({ store, csrfToken: 'csrf', autoSchedule: false })
  const runtime = await controller.activate({ ...process, capture_bundle: { enabled: true, version: 1, max_events: 100, max_checkpoints: 100, max_bytes: 8388608, mutation_receipts: true } } as never, 'visited')
  const event = await runtime.record('response_input_started', { question_type: 'single_choice' }, { eventId: crypto.randomUUID() })
  let renew!: (credentials: Record<string, unknown>) => void
  const lease = new Promise<Record<string, unknown>>(resolve => { renew = resolve })
  const send = vi.fn(async frozen => ({ success: true, committed: true, active_response_event_id: event.event_id, response_version: 1,
    acknowledgements: frozen.capture_bundle.events.map((row: { client_sequence_no: number; event_id?: string }) => ({ client_sequence_no: row.client_sequence_no, event_id: row.event_id || crypto.randomUUID(), server_ingest_sequence_no: row.client_sequence_no })), checkpoint_ids: [] }))
  try {
    const saving = runtime.captureMutation!('/draft/', { answer: 'A', target_event_id: event.event_id, expected_version: 0 }, send, undefined, () => lease)
    await new Promise(resolve => setTimeout(resolve, 0))
    const intent = await store.readMetadata<import('./capture-contract').CaptureMutation>('capture-mutation:7:13')
    expect(intent?.processSnapshot.event_boundary).toBe(event.client_sequence_no)
    const later = await runtime.record('activity_heartbeat', {})
    expect(send).not.toHaveBeenCalled()
    renew({ writer_lease_token: 'renewed', writer_client_instance_id: runtime.clientInstanceId })
    await saving
    expect(send).toHaveBeenCalledOnce()
    expect(send.mock.calls[0]?.[0]).toMatchObject({ writer_lease_token: 'renewed', capture_operation_id: intent?.body.capture_operation_id,
      capture_bundle: { event_boundary: event.client_sequence_no } })
    expect(await store.pendingEvents(runtime.clientInstanceId!, 99)).toEqual([later])
  } finally { controller.dispose() }
})

describe('session controller ownership', () => {
  it('preserves one sender/store/client identity through A to B to A and isolates response versions', async () => {
    const store = new MemoryOutbox()
    const controller = createSessionController({ store, csrfToken: 'csrf', autoSchedule: false })
    const a = await controller.activate(process as never, 'visited')
    a.responseVersion = 4
    const b = await controller.activate({ ...process, attempt_id: 14, response_version: 9 } as never, 'visited')
    b.responseVersion = 10
    const returned = await controller.activate({ ...process, response_version: 5 } as never, 'visited')
    expect(a.clientInstanceId).toBe(b.clientInstanceId)
    expect(returned.clientInstanceId).toBe(a.clientInstanceId)
    expect(controller.store).toBe(store)
    expect(controller.sender).toBe(a.sender)
    expect(returned.responseVersion).toBe(5)
    expect(b.responseVersion).toBe(10)
    controller.dispose()
  })

  it('freezes queued raw event context before an active assignment is replaced', async () => {
    const store = new MemoryOutbox()
    const controller = createSessionController({ store, csrfToken: 'csrf', autoSchedule: false })
    const a = await controller.activate(process as never, 'visited')
    const record = a.record('item_left', { leave_reason: 'navigation', to_assignment_id: 12 })
    await controller.activate({ ...process, attempt_id: 14 } as never, 'visited')
    expect((await record).attempt_id).toBe(13)
    await expect(a.record('response_input_started', { question_type: 'single_choice' })).rejects.toThrow()
    expect(await store.count()).toBeGreaterThan(0)
    controller.dispose()
  })

  it('does not let an obsolete draft update the returned attempt CAS version', async () => {
    const controller = createSessionController({ store: new MemoryOutbox(), csrfToken: 'csrf', autoSchedule: false })
    const a = await controller.activate(process as never, 'visited')
    const draft = vi.fn(async () => { a.responseVersion = 6 })
    await controller.activate({ ...process, attempt_id: 14, response_version: 9 } as never, 'visited')
    const returned = await controller.activate({ ...process, response_version: 8 } as never, 'visited')
    await draft()
    expect(returned.responseVersion).toBe(8)
    controller.dispose()
  })
})


describe('immutable entry and recovery ownership', () => {
  it.each(['matched', 'wrong-token', 'incomplete-ack'] as const)('reconciles a closed submission with original material only: %s', async (mode) => {
    const store = new MemoryOutbox()
    const sender = new ProcessEventCollector({ sessionId: 7, attemptId: 13, endpoint: '/events/', outbox: store, autoSchedule: false })
    const event = await sender.record('response_input_started', { question_type: 'single_choice' })
    const snapshot = { client_instance_id: sender.clientInstanceId, event_boundary: 1, events: [event], checkpoints: [] }
    const intent = { token: 'original-token', clickedAt: '2026-10-12T00:00:00Z', answer: '"B"', processSnapshot: snapshot }
    await store.saveSubmissionIntent(7, 13, intent)
    const later = await sender.record('activity_heartbeat', {})
    const receipt = { success: true, status: 'submitted', next_url: '/student/sessions/7/questions/12/', confidence_required: false,
      committed: true, acknowledgements: mode === 'incomplete-ack' ? [] : [{ event_id: event.event_id, client_sequence_no: event.client_sequence_no }], checkpoint_ids: [] }
    const recover = vi.fn().mockResolvedValue({ result: receipt, submission_matches: mode !== 'wrong-token', can_continue: false })
    const apiSpy = vi.spyOn(assessmentApi, 'getStudentAssessmentApi').mockReturnValue({ recover } as never)
    const controller = createSessionController({ store, csrfToken: 'csrf', autoSchedule: false })
    try {
      if (mode === 'incomplete-ack') await expect(controller.recoverClosedSubmission(process as never)).rejects.toThrow('confirmation')
      else expect(await controller.recoverClosedSubmission(process as never)).toEqual(mode === 'matched' ? receipt : null)
      expect(recover).toHaveBeenCalledExactlyOnceWith(13, 'original-token')
      expect(await store.restoreSubmissionIntent(7, 13)).toEqual(mode === 'matched' ? null : intent)
      expect(await store.pendingEvents(sender.clientInstanceId, 99)).toEqual(mode === 'matched' ? [later] : [event, later])
      expect(controller.sender).toBeUndefined()
    } finally { controller.dispose(); apiSpy.mockRestore() }
  })

  it('does not query a closed attempt without a durable submission intent', async () => {
    const recover = vi.fn()
    const apiSpy = vi.spyOn(assessmentApi, 'getStudentAssessmentApi').mockReturnValue({ recover } as never)
    const controller = createSessionController({ store: new MemoryOutbox(), csrfToken: 'csrf', autoSchedule: false })
    try {
      expect(await controller.recoverClosedSubmission(process as never)).toBeNull()
      expect(recover).not.toHaveBeenCalled()
      expect(controller.sender).toBeUndefined()
    } finally { controller.dispose(); apiSpy.mockRestore() }
  })

  it('replays the exact confirmed entry material on reload and freezes a new token for a real return', async () => {
    const store = new MemoryOutbox()
    const controller = createSessionController({ store, csrfToken: 'csrf', tabId: 'tab', autoSchedule: false })
    const entry = await controller.freezeEntry(7, 13, 'navigation_click')
    await controller.confirmEntry(7)
    const reloaded = createSessionController({ store, csrfToken: 'csrf', tabId: 'tab', autoSchedule: false })
    expect(await reloaded.freezeEntry(7, 13, 'resume')).toEqual(entry)
    await reloaded.confirmEntry(7)
    const b = await reloaded.freezeEntry(7, 14, 'navigation_click')
    expect(b.navigationId).not.toBe(entry.navigationId)
    await reloaded.confirmEntry(7)
    expect((await reloaded.freezeEntry(7, 13, 'navigation_click')).navigationId).not.toBe(entry.navigationId)
  })

  it('cannot replace an entry with unknown outcome or cancel it using another operation token', async () => {
    const store = new MemoryOutbox()
    const controller = createSessionController({ store, csrfToken: 'csrf', tabId: 'tab', autoSchedule: false })
    const entry = await controller.freezeEntry(7, 13, 'navigation_click')
    await controller.cancelEntry(7, crypto.randomUUID())
    await expect(controller.freezeEntry(7, 14, 'navigation_click')).rejects.toThrow('尚未确认')
    expect(await controller.freezeEntry(7, 13, 'resume')).toEqual(entry)
  })

  it('rejects a mismatched entry receipt without deleting raw events or pending intent', async () => {
    const store = new MemoryOutbox()
    const controller = createSessionController({ store, csrfToken: 'csrf', tabId: 'tab', autoSchedule: false })
    const entry = await controller.freezeEntry(7, 13, 'navigation_click')
    const runtime = await controller.activate(process as never, 'visited')
    await runtime.record('response_input_started', { question_type: 'single_choice' })
    const count = await store.count()
    expect(() => controller.validateEntry(7, entry, { assignment: { attempt_id: 13 }, process: { visit_id: 1, visit_index: 1, entry_event_id: 'entry' }, entry_receipt: { navigation_id: crypto.randomUUID(), session_id: 7, assignment_id: 13, attempt_id: 13, visit_id: 1, visit_index: 1, entry_event_id: 'entry', entered_at: '2026-10-12T00:00:00Z' } } as never)).toThrow('进入回执')
    expect(await store.count()).toBe(count)
    expect(await controller.freezeEntry(7, 13, 'resume')).toEqual(entry)
    controller.dispose()
  })

  it('refuses formal entry before server timing starts when persistent storage failed', async () => {
    const store = new MemoryOutbox()
    store.isFallback = true
    const controller = createSessionController({ store, csrfToken: 'csrf', tabId: 'tab', autoSchedule: false })
    await expect(controller.freezeEntry(7, 13, 'navigation_click')).rejects.toThrow('本地持久化')
    expect(await store.readMetadata('entry-intent:7:tab')).toBeNull()
  })

  it('records page lifecycle and resume checkpoint once across SPA visits', async () => {
    const store = new MemoryOutbox()
    const controller = createSessionController({ store, csrfToken: 'csrf', autoSchedule: false })
    const checkpointProcess = { ...process, checkpoint_url: '/api/v1/assessment/process/checkpoints/' }
    const runtime = await controller.activate(checkpointProcess as never, 'visited')
    await controller.activate({ ...checkpointProcess, attempt_id: 14 } as never, 'visited')
    await controller.activate(checkpointProcess as never, 'visited')
    const events = await store.pendingEvents(runtime.clientInstanceId!, Number.MAX_SAFE_INTEGER)
    expect(events.filter(event => event.event_type === 'pageshow')).toHaveLength(1)
    expect(events.filter(event => event.event_type === 'document_visible' || event.event_type === 'document_hidden')).toHaveLength(1)
    expect((await store.pendingCheckpoints()).filter(checkpoint => checkpoint.trigger === 'resume')).toHaveLength(1)
    controller.dispose()
  })
})


it('waits for the old attempt pointer write before installing another attempt', async () => {
  const controller = createSessionController({ store: new MemoryOutbox(), csrfToken: 'csrf', autoSchedule: false })
  const a = await controller.activate(process as never, 'visited')
  let complete!: () => void
  const pending = new Promise<void>(resolve => { complete = resolve })
  const write = a.serializePointerWrite(async () => { await pending; a.responseVersion = 3 })
  let installed = false
  const changing = controller.activate({ ...process, attempt_id: 14, response_version: 8 } as never, 'visited').then(runtime => { installed = true; return runtime })
  await new Promise(resolve => setTimeout(resolve, 0))
  expect(installed).toBe(false)
  complete()
  await write
  expect((await changing).responseVersion).toBe(8)
  controller.dispose()
})

it('uses the same lease client identity for A to B to A without bypassing server ownership', async () => {
  const owners = new Map<string, string>()
  const fetch = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
    const path = String(url)
    const body = JSON.parse(String(init?.body))
    const owner = owners.get(path)
    if (owner && owner !== body.client_instance_id) return Response.json({ error: { code: 'writer_lease_conflict', message: 'conflict', fields: {}, request_id: 'lease' } }, { status: 409 })
    owners.set(path, body.client_instance_id)
    return Response.json({ data: { success: true, lease_token: crypto.randomUUID(), client_instance_id: body.client_instance_id, expires_at: '2099-01-01T00:00:00Z' }, meta: { request_id: 'lease' } })
  })
  const apiSpy = vi.spyOn(assessmentApi, 'getStudentAssessmentApi').mockReturnValue(assessmentApi.createStudentAssessmentApi(createApiClient({ fetchImpl: fetch as typeof globalThis.fetch })))
  const controller = createSessionController({ store: new MemoryOutbox(), csrfToken: 'csrf', autoSchedule: false })
  try {
    const a = await controller.activate(process as never, 'visited')
    await a.writerLease!.beforePointerWrite()
    const b = await controller.activate({ ...process, attempt_id: 14, writer_lease_acquire_url: '/lease/B/' } as never, 'visited')
    await b.writerLease!.beforePointerWrite()
    const returned = await controller.activate(process as never, 'visited')
    await returned.writerLease!.beforePointerWrite()
    expect(new Set(fetch.mock.calls.map(([, init]) => JSON.parse(String(init?.body)).client_instance_id)).size).toBe(1)
  } finally { controller.dispose(); apiSpy.mockRestore() }
})


it('restores a source entry without consuming the unknown target entry after a reload', async () => {
  const store = new MemoryOutbox()
  const controller = createSessionController({ store, csrfToken: 'csrf', tabId: 'tab', autoSchedule: false })
  const a = await controller.freezeEntry(7, 13, 'navigation_click')
  await controller.confirmEntry(7, 13)
  const b = await controller.freezeEntry(7, 14, 'navigation_click', undefined, { sourceAssignmentId: 13, targetUrl: '/student/sessions/7/questions/14/' })
  const reloaded = createSessionController({ store, csrfToken: 'csrf', tabId: 'tab', autoSchedule: false })
  expect(await reloaded.freezeEntry(7, 13, 'resume')).toEqual(a)
  await reloaded.confirmEntry(7, 13)
  expect(await reloaded.pendingEntry(7)).toEqual(b)
  await reloaded.confirmLeave(7, b.navigationId)
  expect(await reloaded.pendingEntry(7)).toMatchObject({ ...b, leaveConfirmed: true })
  expect(await reloaded.freezeEntry(7, 14, 'navigation_click')).toMatchObject({ navigationId: b.navigationId, clientOccurredAt: b.clientOccurredAt, clientMonotonicMs: b.clientMonotonicMs, clientTimeOrigin: b.clientTimeOrigin })
})


it('serializes concurrent initialization so physical page facts keep their first attempt context', async () => {
  const store = new MemoryOutbox()
  const controller = createSessionController({ store, csrfToken: 'csrf', autoSchedule: false })
  const [a,b] = await Promise.all([controller.activate(process as never, 'visited'), controller.activate({ ...process, attempt_id: 14 } as never, 'visited')])
  const lifecycle = (await store.pendingEvents(b.clientInstanceId!, Number.MAX_SAFE_INTEGER)).filter(event => ['pageshow','document_visible','document_hidden','window_focused','window_blurred'].includes(event.event_type))
  expect(lifecycle).toHaveLength(3)
  expect(lifecycle.every(event => event.attempt_id === 13)).toBe(true)
  await expect(a.record('response_input_started', { question_type: 'single_choice' })).rejects.toThrow()
  expect(b.sender.attemptId).toBe(14)
  controller.dispose()
})
