import { afterEach, expect, it, vi } from 'vitest'
import { MemoryOutbox } from './fact-store'
import { createSessionController } from './session-controller'
import { ProcessEventCollector } from './process-sender'

vi.mock('@/app/bootstrap', () => ({ getBootstrapContext: () => ({ csrf_token: 'csrf' }) }))
const capability = { enabled: true, version: 1 as const, recovery_supported: true,
  max_events: 100, max_checkpoints: 100, max_bytes: 8388608, mutation_receipts: true }
const process = { session_id: 7, attempt_id: 13, event_batch_url: '/capture/', checkpoint_url: '',
  time_sync_create_url: '', time_sync_finalize_url: '', app_version: 'test', heartbeat_interval_seconds: 30,
  capture_version: 'capture-v2', capture_bundle: capability }
afterEach(() => vi.restoreAllMocks())

it.each([99, 100, 101, 250])('freezes navigation after bounded confirmation of %i pending facts', async count => {
  const store = new MemoryOutbox()
  const sent: Record<string, unknown>[] = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, init) => {
    const body = JSON.parse(String(init?.body))
    sent.push(body)
    return new Response(JSON.stringify({ data: { success: true, committed: true,
      acknowledgements: body.capture_bundle.events.map((row: { event_id: string; client_sequence_no: number }) => ({
        event_id: row.event_id || crypto.randomUUID(), client_sequence_no: row.client_sequence_no, server_ingest_sequence_no: row.client_sequence_no })),
      checkpoint_ids: body.capture_bundle.checkpoints.map((row: { checkpoint_id: string }) => row.checkpoint_id),
    }, meta: { request_id: 'drain' } }))
  })
  const controller = createSessionController({ store, csrfToken: 'csrf', autoSchedule: false })
  try {
    const runtime = await controller.activate(process, 'visited')
    for (let index = 0; index < count; index++) await runtime.record('response_input_started', { question_type: 'short_answer' }, { eventId: crypto.randomUUID() })
    const token = crypto.randomUUID()
    const left = await runtime.prepareNavigation!(token, 99)
    const frozen = await runtime.freezeNavigationSnapshot!({ token, clickedAt: new Date().toISOString(),
      leaveEventId: left.event_id, toAssignmentId: 99, head: null, expectedResponseVersion: 0 })
    expect(frozen.token).toBe(token)
    expect(frozen.leaveEventId).toBe(left.event_id)
    expect(frozen.processSnapshot!.events.length).toBeLessThanOrEqual(100)
    for (const body of sent) expect((body.capture_bundle as { events: unknown[] }).events.length).toBeLessThanOrEqual(100)
    expect(await runtime.freezeNavigationSnapshot!(frozen)).toEqual(frozen)
  } finally { controller.dispose() }
})

it('stops a boundary drain when every remaining fact belongs to an unresolved mutation', async () => {
  const store = new MemoryOutbox()
  const sender = new ProcessEventCollector({ sessionId: 7, attemptId: 13, endpoint: '/capture/',
    outbox: store, captureBundle: capability, autoSchedule: false })
  await sender.record('response_input_started', { question_type: 'short_answer' }, { eventId: crypto.randomUUID() })
  await sender.freezeCaptureMutation('/draft/', { target_event_id: crypto.randomUUID(), expected_version: 0 })
  const read = store.captureState.bind(store)
  let reads = 0
  vi.spyOn(store, 'captureState').mockImplementation(id => {
    if (++reads > 12) throw new Error('drain spun without progress')
    return read(id)
  })
  await expect(sender.flushConfirmed({ ignoreVisibility: true })).rejects.toMatchObject({ code: 'capture_drain_blocked' })
  expect(reads).toBeLessThan(12)
  expect(await store.count()).toBe(1)
})

it('preserves an individually oversized fact and reports its unsendable budget', async () => {
  const store = new MemoryOutbox()
  const sender = new ProcessEventCollector({ sessionId: 7, attemptId: 13, endpoint: '/capture/',
    outbox: store, captureBundle: { ...capability, max_bytes: 70000 }, autoSchedule: false })
  await sender.record('response_transition', { after_state: { question_type: 'short_answer', value: 'x'.repeat(10000) } })
  await expect(sender.flushConfirmed({ ignoreVisibility: true })).rejects.toMatchObject({ code: 'capture_item_too_large' })
  expect(await store.count()).toBe(1)
  expect(await store.readMetadata('capture-flush:7')).toBeNull()
})

it.each(['checkpoints', 'bytes'])('honors server navigation %s budgets before persisting an intent', async limit => {
  const store = new MemoryOutbox()
  const sender = new ProcessEventCollector({ sessionId: 7, attemptId: 13, endpoint: '/capture/', checkpointEndpoint: '/checkpoint/',
    outbox: store, captureBundle: { ...capability, ...(limit === 'checkpoints' ? { max_checkpoints: 1 } : { max_bytes: 70000 }) }, autoSchedule: false })
  await sender.record('response_transition', { after_state: { question_type: 'short_answer', value: limit === 'bytes' ? 'x'.repeat(10000) : 'answer' } })
  await sender.recordCheckpoint('heartbeat')
  await expect(sender.freezeNavigationSnapshot({ token: crypto.randomUUID() })).rejects.toMatchObject({ code: 'navigation_snapshot_too_large' })
  expect(await store.restoreNavigationIntent(7, 13)).toBeNull()
  expect(await store.count()).toBe(1)
})
