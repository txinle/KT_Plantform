import { describe, expect, it, vi } from 'vitest'
import { createApiClient } from '@/api/client'
import * as assessmentApi from './api'
import { createSessionController, assessmentClientInstanceId } from './session-controller'
import { MemoryOutbox } from './fact-store'
import { ProcessEventCollector } from './process-sender'
import type { EnteredQuestion, NextEntryOutcome } from './api'
import type { EntryIntent } from './session-controller'
vi.mock('@/app/bootstrap', () => ({ getBootstrapContext: () => ({ csrf_token: 'csrf' }) }))

const process = { session_id: 7, attempt_id: 13, event_batch_url: '/api/v1/assessment/process/events/batch/',
  checkpoint_url: '', time_sync_create_url: '', time_sync_finalize_url: '', app_version: 'test',
  heartbeat_interval_seconds: 30, navigation_id: '00000000-0000-4000-8000-000000000010', capture_version: 'capture-v2',
  response_version: 0, writer_lease_acquire_url: '/api/v1/assessment/process/attempts/13/writer-lease/acquire/',
  writer_lease_renew_url: '/api/v1/assessment/process/attempts/13/writer-lease/renew/', writer_lease_release_url: '/api/v1/assessment/process/attempts/13/writer-lease/release/' }

function enteredQuestion(intent: EntryIntent): EnteredQuestion {
  return { session_id: 7, entry_allowed: true, assignment: { assignment_id: intent.assignmentId, attempt_id: 14 },
    process: { ...process, attempt_id: 14, navigation_id: intent.navigationId, visit_id: 21, visit_index: 1, entry_event_id: 'target-entry' },
    entry_receipt: { session_id: 7, assignment_id: intent.assignmentId, attempt_id: 14, navigation_id: intent.navigationId,
      visit_id: 21, visit_index: 1, entry_event_id: 'target-entry', entered_at: '2026-10-13T00:00:00Z' } } as EnteredQuestion
}

describe('source close and target entry confirmation', () => {
  it.each([true, false])('confirms older-attempt facts before freezing navigation and preserves its leave fact on retry: %s', async succeedsInitially => {
    const store = new MemoryOutbox()
    const controller = createSessionController({ store, csrfToken: 'csrf', autoSchedule: false })
    let succeeds = succeedsInitially
    const sent: Array<{ capture_bundle: { events: import('./capture-contract').CaptureEvent[]; checkpoints: Array<{ checkpoint_id: string }> } }> = []
    const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, init) => {
      const body = JSON.parse(String(init?.body))
      sent.push(body)
      if (!succeeds) throw new Error('lost raw response')
      return new Response(JSON.stringify({ data: { success: true, committed: true,
        acknowledgements: body.capture_bundle.events.map((row: { event_id?: string; client_sequence_no: number }) => ({ event_id: row.event_id || crypto.randomUUID(), client_sequence_no: row.client_sequence_no, server_ingest_sequence_no: row.client_sequence_no })),
        checkpoint_ids: body.capture_bundle.checkpoints.map((row: { checkpoint_id: string }) => row.checkpoint_id),
      }, meta: { request_id: 'flush' } }))
    })
    const capability = { enabled: true, version: 1 as const, recovery_supported: true, max_events: 100, max_checkpoints: 100, max_bytes: 8388608, mutation_receipts: true }
    try {
      const source = await controller.activate({ ...process, capture_bundle: capability }, 'visited')
      await source.record('response_input_started', { question_type: 'single_choice' }, { eventId: crypto.randomUUID() })
      const runtime = await controller.activate({ ...process, attempt_id: 14, capture_bundle: capability }, 'visited')
      const token = crypto.randomUUID()
      const left = await runtime.prepareNavigation!(token, 99)
      const original = await store.pendingEvents(runtime.clientInstanceId!, Number.MAX_SAFE_INTEGER)
      const intent = { token, clickedAt: new Date().toISOString(), toAssignmentId: 99, leaveEventId: left.event_id, head: null, expectedResponseVersion: 0 }
      if (!succeedsInitially) {
        await expect(runtime.freezeNavigationSnapshot!(intent)).rejects.toThrow()
        expect(await store.restoreNavigationIntent(7, 14)).toBeNull()
        expect(await store.pendingEvents(runtime.clientInstanceId!, Number.MAX_SAFE_INTEGER)).toEqual(original)
        expect(await runtime.prepareNavigation!(token, 99)).toEqual(left)
        succeeds = true
      }
      const frozen = await runtime.freezeNavigationSnapshot!(intent)
      expect(sent.length).toBeGreaterThan(0)
      expect(sent[0]!.capture_bundle.events).toEqual(original)
      if (!succeedsInitially) expect(sent[1]).toEqual(sent[0])
      expect(frozen).toMatchObject(intent)
      expect(frozen.processSnapshot!.events.every(event => event.attempt_id === 14 || event.attempt_id === null)).toBe(true)
      expect(await store.captureState(runtime.clientInstanceId!)).toMatchObject({ pending: 0, failed: 0 })
    } finally { controller.dispose(); fetch.mockRestore() }
  })

  it('does not upload a second copy of evidence while its draft bundle awaits confirmation', async () => {
    const store = new MemoryOutbox()
    const controller = createSessionController({ store, csrfToken: 'csrf', autoSchedule: false })
    const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, init) => {
      const body = JSON.parse(String(init?.body))
      return new Response(JSON.stringify({ data: { committed: true,
        acknowledgements: body.capture_bundle.events.map((row: { event_id: string; client_sequence_no: number }) => ({ ...row, server_ingest_sequence_no: row.client_sequence_no })),
        checkpoint_ids: body.capture_bundle.checkpoints.map((row: { checkpoint_id: string }) => row.checkpoint_id),
      }, meta: { request_id: 'flush' } }))
    })
    let confirm!: (value: never) => void
    const send = vi.fn((body: Record<string, unknown>) => { void body; return new Promise<never>(resolve => { confirm = resolve }) })
    try {
      const clientId = await assessmentClientInstanceId(store)
      const runtime = await controller.activate({ ...process, capture_bundle: { enabled: true, version: 1,
        max_events: 100, max_checkpoints: 100, max_bytes: 8388608, mutation_receipts: true } }, 'visited', {
        writer_lease_token: 'initial', writer_client_instance_id: clientId, expires_at: new Date(Date.now() + 60000).toISOString(),
      })
      await runtime.record('response_input_started', { question_type: 'single_choice' }, { eventId: crypto.randomUUID() })
      const mutation = runtime.captureMutation!('/draft/', { target_event_id: 'answer-head', expected_version: 0 }, send)
      await vi.waitFor(() => expect(send).toHaveBeenCalledOnce())
      const frozen = send.mock.calls[0]![0] as unknown as { capture_bundle: { events: Array<{ event_id: string; client_sequence_no: number }>; checkpoints: Array<{ checkpoint_id: string }> } }
      const flush = runtime.sender.flush({ ignoreVisibility: true })
      await new Promise(resolve => setTimeout(resolve, 0))
      expect(fetch).not.toHaveBeenCalled()
      confirm({ committed: true, response_version: 1, active_response_event_id: 'answer-head',
        acknowledgements: frozen.capture_bundle.events.map(row => ({ event_id: row.event_id || crypto.randomUUID(), client_sequence_no: row.client_sequence_no, server_ingest_sequence_no: row.client_sequence_no })),
        checkpoint_ids: frozen.capture_bundle.checkpoints.map(row => row.checkpoint_id),
      } as never)
      await mutation
      expect(await flush).toBe(true)
      expect(fetch).not.toHaveBeenCalled()
    } finally { controller.dispose(); fetch.mockRestore() }
  })
  it('freezes the sender identity before entry and reuses it after controller recovery', async () => {
    const store = new MemoryOutbox()
    const controller = createSessionController({ store, csrfToken: 'csrf', tabId: 'f4', autoSchedule: false })
    const intent = await controller.freezeEntry(7, 14, 'navigation_click')
    expect(intent.clientInstanceId).toBe(await assessmentClientInstanceId(store))
    const next = assessmentApi.nextEntryRequest(intent)
    expect(next.client_instance_id).toBe(intent.clientInstanceId)
    controller.dispose()
    const recovered = createSessionController({ store, csrfToken: 'csrf', tabId: 'f4', autoSchedule: false })
    try { expect(await recovered.freezeEntry(7, 14, 'navigation_click')).toEqual(intent) }
    finally { recovered.dispose() }
  })
  it('persists resume locally and starts no upload or clock request before the UI becomes operable', async () => {
    const store = new MemoryOutbox()
    const controller = createSessionController({ store, csrfToken: 'csrf' })
    const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(() => new Promise(() => undefined))
    try {
      const pending = controller.activate({ ...process, checkpoint_url: '/checkpoint/', time_sync_create_url: '/clock/', time_sync_finalize_url: '/clock/finalize/' }, 'visited')
      const result = await Promise.race([pending, new Promise(resolve => setTimeout(() => resolve('blocked'), 30))])
      expect(result).not.toBe('blocked')
      expect(fetch).not.toHaveBeenCalled()
      expect((await store.pendingCheckpoints()).some(row => row.trigger === 'resume')).toBe(true)
    } finally { controller.dispose(); fetch.mockRestore() }
  })

  it('refuses activation when the local resume checkpoint cannot be persisted', async () => {
    const store = new MemoryOutbox()
    vi.spyOn(store, 'enqueueCheckpoint').mockRejectedValue(new Error('local checkpoint failed'))
    const controller = createSessionController({ store, csrfToken: 'csrf', autoSchedule: false })
    try { await expect(controller.activate({ ...process, checkpoint_url: '/checkpoint/' }, 'visited')).rejects.toThrow('local checkpoint failed') }
    finally { controller.dispose() }
  })

  it('refuses activation when the local lifecycle event fails even without a checkpoint endpoint', async () => {
    const store = new MemoryOutbox()
    vi.spyOn(store, 'enqueue').mockRejectedValue(new Error('local lifecycle failed'))
    const controller = createSessionController({ store, csrfToken: 'csrf', autoSchedule: false })
    try { await expect(controller.activate(process, 'visited')).rejects.toThrow('local lifecycle failed') }
    finally { controller.dispose() }
  })

  it('starts automatic capture only after readiness and keeps forced flush available beforehand', async () => {
    vi.useFakeTimers()
    const store = new MemoryOutbox()
    const controller = createSessionController({ store, csrfToken: 'csrf' })
    try {
      const runtime = await controller.activate(process, 'visited')
      const flush = vi.spyOn(runtime.sender, 'flush').mockResolvedValue(true)
      const sync = vi.spyOn(runtime.sender, 'synchronizeClock').mockResolvedValue(null)
      await vi.advanceTimersByTimeAsync(5000)
      expect(flush).not.toHaveBeenCalled()
      runtime.startBackgroundCapture!()
      runtime.startBackgroundCapture!()
      await vi.advanceTimersByTimeAsync(1000)
      expect(flush).toHaveBeenCalledOnce()
      expect(sync).toHaveBeenCalledOnce()
      runtime.dispose()
      await runtime.flushConfirmed({ timeoutMs: 1000 })
      expect(flush).toHaveBeenCalledTimes(2)
    } finally { controller.dispose(); vi.useRealTimers() }
  })

  const capability = { enabled: true, version: 1 as const, max_events: 100, max_checkpoints: 100, max_bytes: 8388608, mutation_receipts: true }
  const denied: NextEntryOutcome = { committed: false, error: { code: 'ENTRY_DENIED', message: 'target denied' } }

  it('consumes an activation receipt without a second entry and primes the same durable sender lease', async () => {
    const store = new MemoryOutbox()
    const controller = createSessionController({ store, csrfToken: 'csrf', autoSchedule: false })
    const intent = { assignmentId: 14, reason: 'navigation_click' as const, navigationId: crypto.randomUUID(), clientOccurredAt: new Date().toISOString(), clientMonotonicMs: performance.now(), clientTimeOrigin: new Date(performance.timeOrigin).toISOString() }
    const dto = enteredQuestion(intent)
    const clientId = await assessmentClientInstanceId(store)
    dto.initial_writer_lease = { writer_lease_token: 'primed', writer_client_instance_id: clientId, expires_at: new Date(Date.now() + 60000).toISOString() }
    await controller.primeStart(dto)
    expect([...store.metadata.keys()]).toEqual([])
    expect(controller.takeStartedQuestion(7, 99)).toBeUndefined()
    expect(controller.takeStartedQuestion(7, 14)).toEqual(dto)
    expect(controller.takeStartedQuestion(7, 14)).toBeUndefined()
    const post = vi.fn()
    const api = vi.spyOn(assessmentApi, 'getStudentAssessmentApi').mockReturnValue({ post } as never)
    try {
      const runtime = await controller.activate(dto.process, 'visited', { writer_lease_token: 'primed', writer_client_instance_id: clientId, expires_at: new Date(Date.now() + 60000).toISOString() })
      expect(runtime.sender.clientInstanceId).toBe(clientId)
      expect(await runtime.writerLease!.beforePointerWrite()).toEqual({ writer_lease_token: 'primed', writer_client_instance_id: clientId })
      expect(post).not.toHaveBeenCalled()
    } finally { controller.dispose(); api.mockRestore() }
  })

  it('freezes a real first resume instead of replaying synthetic start clocks', async () => {
    const store = new MemoryOutbox()
    const controller = createSessionController({ store, csrfToken: 'csrf', tabId: 'tab', autoSchedule: false })
    const dto = enteredQuestion({ assignmentId: 14, navigationId: crypto.randomUUID() } as EntryIntent)
    dto.initial_writer_lease = { writer_lease_token: 'primed', writer_client_instance_id: await assessmentClientInstanceId(store), expires_at: new Date(Date.now() + 60000).toISOString() }
    await controller.primeStart(dto)
    controller.takeStartedQuestion(7, 14)
    await controller.confirmEntry(7, 14)
    controller.dispose()
    const reloaded = createSessionController({ store, csrfToken: 'csrf', tabId: 'tab', autoSchedule: false })
    try {
      const resume = await reloaded.freezeEntry(7, 14, 'resume')
      expect(resume.reason).toBe('resume')
      expect(resume.navigationId).not.toBe(dto.entry_receipt.navigation_id)
      await reloaded.confirmEntry(7, 14)
      expect(await reloaded.freezeEntry(7, 14, 'resume')).toEqual(resume)
    } finally { reloaded.dispose() }
  })

  it('rejects activation receipts bound to another sender or inconsistent assignment', async () => {
    const controller = createSessionController({ store: new MemoryOutbox(), csrfToken: 'csrf', autoSchedule: false })
    const dto = enteredQuestion({ assignmentId: 14, navigationId: crypto.randomUUID() } as EntryIntent)
    dto.initial_writer_lease = { writer_lease_token: 'wrong', writer_client_instance_id: crypto.randomUUID(), expires_at: new Date(Date.now() + 60000).toISOString() }
    try {
      await expect(controller.primeStart(dto)).rejects.toThrow()
      dto.initial_writer_lease.writer_client_instance_id = await assessmentClientInstanceId()
      await expect(controller.primeStart({ ...dto, session_id: 8 })).rejects.toThrow()
      await expect(controller.primeStart({ ...dto, assignment: { ...dto.assignment, assignment_id: 99 } })).rejects.toThrow()
    } finally { controller.dispose() }
  })

  it('retains a matched target DTO only once in the live controller and never persists it', async () => {
    const store = new MemoryOutbox()
    const controller = createSessionController({ store, csrfToken: 'csrf', tabId: 'tab', autoSchedule: false })
    const intent = await controller.freezeEntry(7, 14, 'navigation_click')
    const dto = enteredQuestion(intent)
    expect(await controller.rememberEnteredQuestion(7, assessmentApi.nextEntryRequest(intent), { committed: true, runtime: dto })).toBe(true)
    expect(controller.takeEnteredQuestion(7, { ...intent, clientMonotonicMs: intent.clientMonotonicMs + 1 })).toBeUndefined()
    expect(createSessionController({ store, csrfToken: 'csrf', tabId: 'tab', autoSchedule: false }).takeEnteredQuestion(7, intent)).toBeUndefined()
    expect(controller.takeEnteredQuestion(7, intent)).toEqual(dto)
    expect(controller.takeEnteredQuestion(7, intent)).toBeUndefined()
    expect([...store.metadata.keys()]).toEqual(['entry-intent:7:tab'])
    controller.dispose()
  })

  it('rejects missing, malformed, mismatched or denied target authorization without trusting source ACK', async () => {
    const controller = createSessionController({ store: new MemoryOutbox(), csrfToken: 'csrf', tabId: 'tab', autoSchedule: false })
    const intent = await controller.freezeEntry(7, 14, 'navigation_click')
    const request = assessmentApi.nextEntryRequest(intent)
    expect(await controller.rememberEnteredQuestion(7, request, denied)).toBe(false)
    for (const invalid of [undefined, { committed: false }, { committed: false, error: { code: '', message: '' } },
      { committed: true },
      { committed: true, runtime: { ...enteredQuestion(intent), assignment: { assignment_id: 99, attempt_id: 14 } } }]) {
      await expect(controller.rememberEnteredQuestion(7, request, invalid as NextEntryOutcome)).rejects.toThrow()
    }
    for (const field of Object.keys(request) as Array<keyof typeof request>) {
      await expect(controller.rememberEnteredQuestion(7, { ...request, [field]: 'mismatch' } as never,
        { committed: true, runtime: enteredQuestion(intent) })).rejects.toThrow()
    }
    for (const field of ['session_id', 'attempt_id'] as const) {
      const dto = enteredQuestion(intent)
      await expect(controller.rememberEnteredQuestion(7, request,
        { committed: true, runtime: { ...dto, process: { ...dto.process, [field]: 99 } } })).rejects.toThrow()
    }
    expect(controller.takeEnteredQuestion(7, intent)).toBeUndefined()
    expect(await controller.pendingEntry(7)).toEqual(intent)
    controller.dispose()
  })

  it('validates a frozen terminal or denied entry receipt while refusing to cache it as ready', async () => {
    const controller = createSessionController({ store: new MemoryOutbox(), csrfToken: 'csrf', tabId: 'tab', autoSchedule: false })
    const intent = await controller.freezeEntry(7, 14, 'navigation_click')
    const dto = { ...enteredQuestion(intent), entry_allowed: false }
    expect(() => controller.validateEntry(7, intent, dto)).not.toThrow()
    expect(await controller.rememberEnteredQuestion(7, assessmentApi.nextEntryRequest(intent), { committed: true, runtime: dto })).toBe(false)
    expect(controller.takeEnteredQuestion(7, intent)).toBeUndefined()
    expect(await controller.pendingEntry(7)).toEqual(intent)
    controller.dispose()
  })

  it.each(['cancel', 'confirmed', 'dispose'] as const)('clears matching ready material on %s', async action => {
    const controller = createSessionController({ store: new MemoryOutbox(), csrfToken: 'csrf', tabId: 'tab', autoSchedule: false })
    const intent = await controller.freezeEntry(7, 14, 'navigation_click')
    await controller.rememberEnteredQuestion(7, assessmentApi.nextEntryRequest(intent), { committed: true, runtime: enteredQuestion(intent) })
    if (action === 'cancel') await controller.cancelEntry(7, intent.navigationId)
    if (action === 'confirmed') await controller.confirmEntry(7, 14)
    if (action === 'dispose') controller.dispose()
    expect(controller.takeEnteredQuestion(7, intent)).toBeUndefined()
    controller.dispose()
  })

  it.each(['entered', 'denied', 'missing'] as const)('keeps source ACK and invalidated lease when target is %s', async outcome => {
    const store = new MemoryOutbox()
    const controller = createSessionController({ store, csrfToken: 'csrf', tabId: 'tab', autoSchedule: false })
    const intent = await controller.freezeEntry(7, 14, 'navigation_click')
    const nextEntry = assessmentApi.nextEntryRequest(intent)
    const bodies: Array<Record<string, unknown>> = []
    const post = vi.fn(async (_url, body) => {
      if (!body.capture_bundle) return { success: true, lease_token: 'lease', client_instance_id: body.client_instance_id, expires_at: new Date(Date.now()+60000).toISOString() }
      bodies.push(body)
      return { committed: true, writer_lease_released: true, capture_operation_id: body.capture_operation_id,
        acknowledgements: body.capture_bundle.events.map((row: { event_id?: string; client_sequence_no: number }) => ({ event_id: row.event_id || crypto.randomUUID(), client_sequence_no: row.client_sequence_no, server_ingest_sequence_no: row.client_sequence_no })),
        checkpoint_ids: body.capture_bundle.checkpoints.map((row: { checkpoint_id: string }) => row.checkpoint_id),
        ...(outcome === 'missing' ? {} : { next_entry: outcome === 'entered' ? { committed: true, runtime: enteredQuestion(intent) } : denied }) }
    })
    const spy = vi.spyOn(assessmentApi, 'getStudentAssessmentApi').mockReturnValue({ post } as never)
    try {
      const runtime = await controller.activate({ ...process, capture_bundle: capability, close_bundle: capability } as never, 'visited')
      await runtime.writerLease!.beforePointerWrite()
      const close = runtime.closeAttempt('navigation', 14, nextEntry)
      if (outcome === 'missing') await expect(close).rejects.toThrow()
      else expect(await close).toBe(true)
      expect(bodies[0]!.next_entry).toEqual(nextEntry)
      expect(await store.readMetadata('capture-mutation:7:13')).toBeNull()
      expect(await store.readMetadata('confirmed-close:7:13')).toMatchObject({ reason: 'navigation' })
      expect(runtime.writerLease!.cachedCredentials()).toBeUndefined()
      expect(await controller.pendingEntry(7)).toMatchObject({ leaveConfirmed: true })
      expect(controller.takeEnteredQuestion(7, intent)).toEqual(outcome === 'entered' ? enteredQuestion(intent) : undefined)
      expect(await runtime.closeAttempt('navigation', 14, nextEntry)).toBe(true)
      expect(bodies).toHaveLength(1)
    } finally { controller.dispose(); spy.mockRestore() }
  })

  it('replays the same next-entry close envelope after response loss and reload with one leave fact', async () => {
    const store = new MemoryOutbox()
    let controller = createSessionController({ store, csrfToken: 'csrf', tabId: 'tab', autoSchedule: false })
    const intent = await controller.freezeEntry(7, 14, 'navigation_click')
    const request = assessmentApi.nextEntryRequest(intent)
    const bodies: Array<Record<string, unknown>> = []
    let lost = true
    const post = vi.fn(async (_url, body) => {
      if (!body.capture_bundle) return { success: true, lease_token: 'lease', client_instance_id: body.client_instance_id, expires_at: new Date(Date.now()+60000).toISOString() }
      bodies.push(body)
      if (lost) throw new Error('response lost')
      return { committed: true, writer_lease_released: true, capture_operation_id: body.capture_operation_id,
        acknowledgements: body.capture_bundle.events.map((row: { event_id?: string; client_sequence_no: number }) => ({ event_id: row.event_id || crypto.randomUUID(), client_sequence_no: row.client_sequence_no, server_ingest_sequence_no: row.client_sequence_no })),
        checkpoint_ids: body.capture_bundle.checkpoints.map((row: { checkpoint_id: string }) => row.checkpoint_id), next_entry: denied }
    })
    const spy = vi.spyOn(assessmentApi, 'getStudentAssessmentApi').mockReturnValue({ post } as never)
    try {
      const runtime = await controller.activate({ ...process, capture_bundle: capability, close_bundle: capability } as never, 'visited')
      await expect(runtime.closeAttempt('navigation', 14, request)).rejects.toThrow('response lost')
      const leaves = store.records.filter(row => row.event.event_type === 'item_left')
      expect(leaves).toHaveLength(1)
      controller.dispose()
      lost = false
      controller = createSessionController({ store, csrfToken: 'csrf', tabId: 'tab', autoSchedule: false })
      const restored = await controller.activate({ ...process, capture_bundle: capability, close_bundle: capability } as never, 'visited')
      expect(await restored.closeAttempt('navigation', 14, request)).toBe(true)
      expect(bodies[1]).toEqual(bodies[0])
      expect(bodies[1]!.next_entry).toEqual(request)
      expect(new Set(bodies.flatMap(body => (body.capture_bundle as { events: Array<{ event_type: string; event_id: string }> }).events.filter(row => row.event_type === 'item_left').map(row => row.event_id))).size).toBe(1)
      expect(await controller.pendingEntry(7)).toMatchObject({ leaveConfirmed: true })
      expect(controller.takeEnteredQuestion(7, intent)).toBeUndefined()
    } finally { controller.dispose(); spy.mockRestore() }
  })

  it('blocks the close request when frozen entry persistence is unavailable', async () => {
    const store = new MemoryOutbox()
    const controller = createSessionController({ store, csrfToken: 'csrf', tabId: 'tab', autoSchedule: false })
    const write = vi.spyOn(store, 'writeMetadata').mockRejectedValue(new Error('disk full'))
    await expect(controller.freezeEntry(7, 14, 'navigation_click')).rejects.toThrow('disk full')
    write.mockRestore()
    const runtime = await controller.activate({ ...process, capture_bundle: capability, close_bundle: capability } as never, 'visited')
    const post = vi.fn()
    const spy = vi.spyOn(assessmentApi, 'getStudentAssessmentApi').mockReturnValue({ post } as never)
    try {
      await expect(runtime.closeAttempt('navigation', 14, assessmentApi.nextEntryRequest({ assignmentId: 14, reason: 'navigation_click', navigationId: crypto.randomUUID(), clientOccurredAt: new Date().toISOString(), clientMonotonicMs: 1, clientTimeOrigin: new Date().toISOString() }))).rejects.toThrow()
      expect(post).not.toHaveBeenCalled()
      expect(store.records.some(row => row.event.event_type === 'item_left')).toBe(false)
    } finally { controller.dispose(); spy.mockRestore() }
  })

  it.each(['denied', 'missing'] as const)('recovers a submitted source close while preserving %s target recovery', async outcome => {
    const store = new MemoryOutbox()
    const controller = createSessionController({ store, csrfToken: 'csrf', tabId: 'tab', autoSchedule: false })
    const intent = await controller.freezeEntry(7, 14, 'navigation_click')
    const closure = { next_url: '/student/sessions/7/questions/14/', status: 'submitted' }
    await store.writeMetadata('submission-close:7:13', closure)
    const sender = new ProcessEventCollector({ sessionId: 7, attemptId: 13, endpoint: '/events/', outbox: store, autoSchedule: false })
    const left = await sender.record('item_left', { leave_reason: 'submit', to_assignment_id: 14 }, { eventId: crypto.randomUUID() })
    const frozen = await sender.freezeCaptureMutation('/release/', { capture_operation_id: crypto.randomUUID(),
      leave_event_id: left.event_id, lease_token: 'expired', client_instance_id: sender.clientInstanceId,
      next_entry: assessmentApi.nextEntryRequest(intent) })
    const post = vi.fn(async () => ({ committed: true, writer_lease_released: true, capture_operation_id: frozen.body.capture_operation_id,
      acknowledgements: frozen.processSnapshot.events.map(row => ({ event_id: row.event_id || crypto.randomUUID(), client_sequence_no: row.client_sequence_no, server_ingest_sequence_no: row.client_sequence_no })),
      checkpoint_ids: frozen.processSnapshot.checkpoints.map(row => row.checkpoint_id), ...(outcome === 'denied' ? { next_entry: denied } : {}) }))
    const spy = vi.spyOn(assessmentApi, 'getStudentAssessmentApi').mockReturnValue({ post } as never)
    try {
      const recovery = controller.recoverClosedSubmission(process as never)
      if (outcome === 'missing') await expect(recovery).rejects.toThrow('目标题')
      else expect(await recovery).toEqual(closure)
      expect(await store.readMetadata('capture-mutation:7:13')).toBeNull()
      expect(await store.readMetadata('confirmed-close:7:13')).toMatchObject({ reason: 'submit' })
      expect(await controller.pendingEntry(7)).toMatchObject({ ...intent, leaveConfirmed: true })
      expect(controller.takeEnteredQuestion(7, intent)).toBeUndefined()
      if (outcome === 'missing') expect(await controller.recoverClosedSubmission(process as never)).toEqual(closure)
      expect(post).toHaveBeenCalledExactlyOnceWith('/release/', { ...frozen.body, capture_bundle: frozen.processSnapshot }, { timeoutMs: 30000 })
    } finally { controller.dispose(); sender.dispose(); spy.mockRestore() }
  })
})

it('replays a frozen bundled close after response loss and releases only with the matching receipt', async () => {
  const store = new MemoryOutbox()
  const capability = { enabled: true, version: 1 as const, max_events: 100, max_checkpoints: 100, max_bytes: 8388608, mutation_receipts: true }
  const requests: Array<Record<string, unknown>> = []
  let loseResponse = true
  const post = vi.fn(async (_url, body) => {
    if (!body.capture_bundle) return { success: true, lease_token: 'lease-token', client_instance_id: body.client_instance_id, expires_at: new Date(Date.now() + 60000).toISOString() }
    requests.push(body)
    if (loseResponse) throw new Error('response lost')
    const bundle = body.capture_bundle
    return { success: true, committed: true, writer_lease_released: true, capture_operation_id: body.capture_operation_id,
      acknowledgements: bundle.events.map((row: { event_id: string; client_sequence_no: number }) => ({ event_id: row.event_id || crypto.randomUUID(), client_sequence_no: row.client_sequence_no, server_ingest_sequence_no: row.client_sequence_no })),
      checkpoint_ids: bundle.checkpoints.map((row: { checkpoint_id: string }) => row.checkpoint_id) }
  })
  const spy = vi.spyOn(assessmentApi, 'getStudentAssessmentApi').mockReturnValue({ post } as never)
  let controller = createSessionController({ store, csrfToken: 'csrf', autoSchedule: false })
  try {
    const runtime = await controller.activate({ ...process, capture_bundle: capability, close_bundle: capability } as never, 'visited')
    await runtime.writerLease!.beforePointerWrite()
    await expect(runtime.closeAttempt('submit')).rejects.toThrow('response lost')
    expect(await store.readMetadata('capture-mutation:7:13')).not.toBeNull()
    const original = requests[0]
    controller.dispose()
    loseResponse = false
    controller = createSessionController({ store, csrfToken: 'csrf', autoSchedule: false })
    const reloaded = await controller.activate({ ...process, capture_bundle: capability, close_bundle: capability } as never, 'submitted')
    expect(await reloaded.closeAttempt('submit')).toBe(true)
    expect(requests[1]).toEqual(original)
    expect(requests[1]!.capture_bundle).toEqual(original!.capture_bundle)
    expect(await store.readMetadata('capture-mutation:7:13')).toBeNull()
    expect(await reloaded.closeAttempt('submit')).toBe(true)
    expect(requests).toHaveLength(2)
  } finally { controller.dispose(); spy.mockRestore() }
})

it('keeps a bundled close single flight for six seconds until the exact receipt confirms', async () => {
  vi.useFakeTimers()
  const store = new MemoryOutbox()
  const capability = { enabled: true, version: 1 as const, max_events: 100, max_checkpoints: 100, max_bytes: 8388608, mutation_receipts: true }
  let confirm!: () => void
  const post = vi.fn(async (_url, body) => {
    if (!body.capture_bundle) return { success: true, lease_token: 'lease', client_instance_id: body.client_instance_id, expires_at: new Date(Date.now() + 60000).toISOString() }
    await new Promise<void>(resolve => { confirm = resolve })
    return { success: true, committed: true, writer_lease_released: true, capture_operation_id: body.capture_operation_id,
      acknowledgements: body.capture_bundle.events.map((row: { event_id: string; client_sequence_no: number }) => ({ event_id: row.event_id || crypto.randomUUID(), client_sequence_no: row.client_sequence_no, server_ingest_sequence_no: row.client_sequence_no })),
      checkpoint_ids: body.capture_bundle.checkpoints.map((row: { checkpoint_id: string }) => row.checkpoint_id) }
  })
  const spy = vi.spyOn(assessmentApi, 'getStudentAssessmentApi').mockReturnValue({ post } as never)
  const controller = createSessionController({ store, csrfToken: 'csrf', autoSchedule: false })
  try {
    const runtime = await controller.activate({ ...process, capture_bundle: capability, close_bundle: capability } as never, 'visited')
    await runtime.writerLease!.beforePointerWrite()
    let settled = false
    const first = runtime.closeAttempt('submit').then(result => { settled = true; return result })
    await vi.advanceTimersByTimeAsync(6000)
    expect(settled).toBe(false)
    expect(await store.readMetadata('capture-mutation:7:13')).not.toBeNull()
    const second = runtime.closeAttempt('submit')
    confirm()
    expect(await first).toBe(true)
    expect(await second).toBe(true)
    expect(post.mock.calls.filter(([, body]) => body.capture_bundle)).toHaveLength(1)
    expect(await store.readMetadata('capture-mutation:7:13')).toBeNull()
  } finally { controller.dispose(); spy.mockRestore(); vi.useRealTimers() }
})

it('replays the exact durably frozen close after a committed response is lost at the transport deadline', async () => {
  vi.useFakeTimers()
  const store = new MemoryOutbox()
  const capability = { enabled: true, version: 1 as const, max_events: 100, max_checkpoints: 100, max_bytes: 8388608, mutation_receipts: true }
  const committed: Array<Record<string, unknown>> = []
  let loseResponse = true
  const fetchImpl = vi.fn(async (_url, options: RequestInit) => {
    const body = JSON.parse(String(options.body))
    if (!body.capture_bundle) return new Response(JSON.stringify({ meta: { request_id: 'lease-request' }, data: {
      success: true, lease_token: 'lease', client_instance_id: body.client_instance_id, expires_at: new Date(Date.now() + 60000).toISOString(),
    } }))
    committed.push(body)
    if (loseResponse) await new Promise((_resolve, reject) => options.signal!.addEventListener('abort', () => reject(options.signal!.reason)))
    return new Response(JSON.stringify({ meta: { request_id: 'close-request' }, data: { success: true, committed: true, writer_lease_released: true,
      capture_operation_id: body.capture_operation_id,
      acknowledgements: body.capture_bundle.events.map((row: { event_id?: string; client_sequence_no: number }) => ({ event_id: row.event_id || crypto.randomUUID(), client_sequence_no: row.client_sequence_no, server_ingest_sequence_no: row.client_sequence_no })),
      checkpoint_ids: body.capture_bundle.checkpoints.map((row: { checkpoint_id: string }) => row.checkpoint_id) } }))
  })
  const api = assessmentApi.createStudentAssessmentApi(createApiClient({ fetchImpl: fetchImpl as never }))
  const spy = vi.spyOn(assessmentApi, 'getStudentAssessmentApi').mockReturnValue(api)
  let controller = createSessionController({ store, csrfToken: 'csrf', autoSchedule: false })
  try {
    const runtime = await controller.activate({ ...process, capture_bundle: capability, close_bundle: capability } as never, 'visited')
    await runtime.writerLease!.beforePointerWrite()
    const close = runtime.closeAttempt('submit')
    let failure: unknown
    const failed = close.catch(error => { failure = error })
    await vi.advanceTimersByTimeAsync(29999)
    expect(failure).toBeUndefined()
    expect(committed).toHaveLength(1)
    expect(await store.readMetadata('capture-mutation:7:13')).not.toBeNull()
    await vi.advanceTimersByTimeAsync(1)
    await failed
    expect(failure).toMatchObject({ name: 'MutationOutcomeUnknownError' })
    expect(await store.readMetadata('capture-mutation:7:13')).not.toBeNull()
    expect(await store.readMetadata('confirmed-close:7:13')).toBeNull()
    controller.dispose()
    loseResponse = false
    controller = createSessionController({ store, csrfToken: 'csrf', autoSchedule: false })
    const recovered = await controller.activate({ ...process, capture_bundle: capability, close_bundle: capability } as never, 'submitted')
    expect(await recovered.closeAttempt('submit')).toBe(true)
    expect(committed[1]).toEqual(committed[0])
    expect(await store.readMetadata('capture-mutation:7:13')).toBeNull()
    expect(await recovered.closeAttempt('submit')).toBe(true)
    expect(committed).toHaveLength(2)
  } finally { controller.dispose(); spy.mockRestore(); vi.useRealTimers() }
})

it('recovers a held close before following a denied terminal entry', async () => {
  const store = new MemoryOutbox()
  const closure = { next_url: '/student/sessions/7/questions/14/', status: 'submitted' }
  await store.writeMetadata('submission-close:7:13', closure)
  const sender = new ProcessEventCollector({ sessionId: 7, attemptId: 13, endpoint: '/events/', outbox: store, autoSchedule: false })
  const left = await sender.record('item_left', { leave_reason: 'submit', to_assignment_id: null }, { eventId: crypto.randomUUID() })
  const frozen = await sender.freezeCaptureMutation('/release/', { capture_operation_id: crypto.randomUUID(),
    leave_event_id: left.event_id, lease_token: 'expired-token', client_instance_id: sender.clientInstanceId })
  const post = vi.fn(async () => ({ committed: true, writer_lease_released: true, capture_operation_id: frozen.body.capture_operation_id,
    acknowledgements: frozen.processSnapshot.events.map(row => ({ event_id: row.event_id, client_sequence_no: row.client_sequence_no, server_ingest_sequence_no: row.client_sequence_no })), checkpoint_ids: [] }))
  const spy = vi.spyOn(assessmentApi, 'getStudentAssessmentApi').mockReturnValue({ post } as never)
  const controller = createSessionController({ store, csrfToken: 'csrf', autoSchedule: false })
  try {
    expect(await controller.recoverClosedSubmission(process as never)).toEqual(closure)
    expect(post).toHaveBeenCalledExactlyOnceWith('/release/', { ...frozen.body, capture_bundle: frozen.processSnapshot }, { timeoutMs: 30000 })
    expect(await store.readMetadata('capture-mutation:7:13')).toBeNull()
    expect(await store.readMetadata('submission-close:7:13')).toBeNull()
  } finally { controller.dispose(); spy.mockRestore() }
})

it('a close receipt for one visit cannot suppress a later return to the same attempt', async () => {
  const store = new MemoryOutbox()
  const capability = { enabled: true, version: 1 as const, max_events: 100, max_checkpoints: 100, max_bytes: 8388608, mutation_receipts: true }
  let closes = 0
  const post = vi.fn(async (_url, body) => {
    if (!body.capture_bundle) return { success: true, lease_token: 'lease', client_instance_id: body.client_instance_id, expires_at: new Date(Date.now() + 60000).toISOString() }
    closes++
    return { committed: true, writer_lease_released: true, capture_operation_id: body.capture_operation_id,
      acknowledgements: body.capture_bundle.events.map((row: { event_id?: string; client_sequence_no: number }) => ({ event_id: row.event_id || crypto.randomUUID(), client_sequence_no: row.client_sequence_no, server_ingest_sequence_no: row.client_sequence_no })),
      checkpoint_ids: body.capture_bundle.checkpoints.map((row: { checkpoint_id: string }) => row.checkpoint_id) }
  })
  const spy = vi.spyOn(assessmentApi, 'getStudentAssessmentApi').mockReturnValue({ post } as never)
  const controller = createSessionController({ store, csrfToken: 'csrf', autoSchedule: false })
  try {
    for (const navigation_id of [crypto.randomUUID(), crypto.randomUUID()]) {
      const runtime = await controller.activate({ ...process, navigation_id, capture_bundle: capability, close_bundle: capability } as never, 'visited')
      await runtime.writerLease!.beforePointerWrite()
      expect(await runtime.closeAttempt('navigation', 14)).toBe(true)
    }
    expect(closes).toBe(2)
  } finally { controller.dispose(); spy.mockRestore() }
})

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
    expect(post).toHaveBeenLastCalledWith('/draft/', { ...saved?.body, capture_bundle: saved?.processSnapshot }, undefined)
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

it('freezes source resume independently when navigation from the activation visit lost its response', async () => {
  const store = new MemoryOutbox()
  const controller = createSessionController({ store, csrfToken: 'csrf', tabId: 'tab', autoSchedule: false })
  const target = await controller.freezeEntry(7, 14, 'navigation_click', undefined, { sourceAssignmentId: 13, targetUrl: '/student/sessions/7/questions/14/' })
  const navigation = { token: target.navigationId, toAssignmentId: 14, leaveEventId: 'original-leave', head: 'original-head', expectedResponseVersion: 2 }
  await store.saveNavigationIntent(7, 13, navigation)
  const reloaded = createSessionController({ store, csrfToken: 'csrf', tabId: 'tab', autoSchedule: false })
  const source = await reloaded.freezeEntry(7, 13, 'resume')
  expect(source.reason).toBe('resume')
  expect(source.assignmentId).toBe(13)
  expect(source.navigationId).not.toBe(target.navigationId)
  expect(await reloaded.pendingEntry(7)).toEqual(target)
  expect(await reloaded.freezeEntry(7, 13, 'resume')).toEqual(source)
  await reloaded.confirmEntry(7, 13)
  expect(await reloaded.pendingEntry(7)).toEqual(target)
  expect(await store.restoreNavigationIntent(7, 13)).toEqual(navigation)
  expect(await reloaded.freezeEntry(7, 13, 'resume')).toEqual(source)
  expect(await reloaded.freezeEntry(7, 14, 'navigation_click')).toEqual(target)
  controller.dispose()
  reloaded.dispose()
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

it('records a real leave from a flow-one read-only view while legacy closed attempts stay closed', async () => {
  const capability = { enabled: true, version: 1 as const, max_events: 100, max_checkpoints: 100, max_bytes: 8388608, mutation_receipts: true }
  const store = new MemoryOutbox()
  const controller = createSessionController({ store, csrfToken: 'csrf', autoSchedule: false })
  const post = vi.fn(async (_url, body) => body.capture_bundle ? { committed: true, writer_lease_released: true, capture_operation_id: body.capture_operation_id,
    acknowledgements: body.capture_bundle.events.map((event: { event_id: string; client_sequence_no: number }) => ({ event_id: event.event_id || crypto.randomUUID(), client_sequence_no: event.client_sequence_no, server_ingest_sequence_no: event.client_sequence_no })), checkpoint_ids: body.capture_bundle.checkpoints.map((row: { checkpoint_id: string }) => row.checkpoint_id) }
    : { success: true, lease_token: '00000000-0000-4000-8000-000000000002', client_instance_id: body.client_instance_id, expires_at: new Date(Date.now() + 60000).toISOString() })
  const api = vi.spyOn(assessmentApi, 'getStudentAssessmentApi').mockReturnValue({ post } as never)
  try {
    const runtime = await controller.activate({ ...process, paper_flow_version: 1, capture_bundle: capability, close_bundle: capability } as never, 'timed_out')
    await runtime.closeAttempt('navigation', 14)
    const close = post.mock.calls.find(call => call[1].capture_bundle)
    expect(close?.[1].capture_bundle.events.filter((event: { event_type: string }) => event.event_type === 'item_left')).toHaveLength(1)
  } finally { controller.dispose(); api.mockRestore() }
  const legacyStore = new MemoryOutbox()
  const legacy = createSessionController({ store: legacyStore, csrfToken: 'csrf', autoSchedule: false })
  try {
    const runtime = await legacy.activate(process as never, 'timed_out')
    await runtime.closeAttempt('navigation', 14)
    expect(legacyStore.records.filter(row => row.event.event_type === 'item_left')).toHaveLength(0)
  } finally { legacy.dispose() }
})
