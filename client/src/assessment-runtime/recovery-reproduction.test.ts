import { afterEach, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/client'
import * as assessmentApi from './api'
import type { CaptureBundle, CaptureMutation } from './capture-contract'
import { MemoryOutbox } from './fact-store'
import { mutationMaterialFingerprint } from './mutation-material'
import { ProcessEventCollector } from './process-sender'
import { ResponseTrajectory } from './response-trajectory'
import { createSessionController } from './session-controller'

vi.mock('@/app/bootstrap', () => ({ getBootstrapContext: () => ({ csrf_token: 'csrf' }) }))

const capability = { enabled: true, version: 1 as const, recovery_supported: true,
  max_events: 100, max_checkpoints: 100, max_bytes: 8388608, mutation_receipts: true }
const process = { session_id: 7, attempt_id: 13, event_batch_url: '/capture/', checkpoint_url: '',
  time_sync_create_url: '', time_sync_finalize_url: '', app_version: 'reproduction', heartbeat_interval_seconds: 30,
  capture_version: 'capture-v2', capture_bundle: capability, response_version: 0 }

afterEach(() => vi.restoreAllMocks())

it('does not upload a new document descendant while its old parent is owned by an unresolved draft', async () => {
  const store = new MemoryOutbox()
  const old = new ProcessEventCollector({ sessionId: 7, attemptId: 13, endpoint: '/capture/',
    outbox: store, captureBundle: capability, autoSchedule: false, uuid: () => 'old-client' })
  const current = new ProcessEventCollector({ sessionId: 7, attemptId: 13, endpoint: '/capture/',
    outbox: store, captureBundle: capability, autoSchedule: false, uuid: () => 'new-client' })
  try {
    const parent = await old.record('response_transition', { parent_event_id: null, answer: 'A' }, { eventId: crypto.randomUUID() })
    await old.freezeCaptureMutation('/draft/', { target_event_id: parent.event_id })
    const child = await current.record('response_transition', { parent_event_id: parent.event_id, answer: 'B' }, { eventId: crypto.randomUUID() })
    expect(await store.peekBatch()).toBeNull()
    await store.writeMetadata('capture-mutation:7:13', null)
    expect((await store.peekBatch())?.events).toEqual([parent])
    await store.deleteAcknowledged(old.clientInstanceId, [parent.client_sequence_no])
    expect((await store.peekBatch())?.events).toEqual([child])
  } finally { old.dispose(); current.dispose() }
})

// Transport double: accepts a draft only if its referenced transition exists
// already or arrives in the same transaction. The real controller, trajectory,
// sender, outbox, fingerprinting and rejection recovery run without mocks.
async function resumedDraft() {
  const store = new MemoryOutbox()
  const old = new ProcessEventCollector({ sessionId: 7, attemptId: 13, endpoint: '/capture/',
    outbox: store, captureBundle: capability, autoSchedule: false,
    uuid: () => '00000000-0000-4000-8000-000000000101' })
  const trajectory = new ResponseTrajectory('single_choice', null, null,
    (type, payload, options) => old.record(type, payload, options))
  trajectory.change('A', 'change')
  await trajectory.settled()
  const target = trajectory.head!
  await old.freezeCaptureMutation('/draft/', { answer: 'A', target_event_id: target, expected_version: 0,
    writer_lease_token: 'expired-lease', writer_client_instance_id: old.clientInstanceId,
    capture_operation_id: crypto.randomUUID() })

  const persisted = new Set<string>()
  const history: string[] = []
  let version = 0
  const acknowledge = (bundle: CaptureBundle) => ({ success: true, committed: true,
    acknowledgements: bundle.events.map(row => ({ event_id: row.event_id || crypto.randomUUID(),
      client_sequence_no: row.client_sequence_no, server_ingest_sequence_no: row.client_sequence_no })),
    checkpoint_ids: bundle.checkpoints.map(row => row.checkpoint_id) })
  const reject = async (body: Record<string, unknown>, code: string) => {
    history.push(code)
    throw new ApiError(409, { error: { code, message: code, fields: {
      mutation_rejected: true, receipt_version: 2, mutation_kind: 'draft', attempt_id: 13,
      capture_operation_id: body.capture_operation_id,
      material_fingerprint: await mutationMaterialFingerprint(body),
    } } })
  }
  const save = async (body: Record<string, unknown>) => {
    const bundle = body.capture_bundle as CaptureBundle
    if (body.writer_lease_token === 'expired-lease') return reject(body, 'writer_lease_conflict')
    const staged = new Set([...persisted, ...bundle.events.map(row => row.event_id!)])
    if (!staged.has(String(body.target_event_id))) return reject(body, 'response_target_not_persisted')
    bundle.events.forEach(row => persisted.add(row.event_id!))
    history.push('draft_saved')
    return { ...acknowledge(bundle), receipt_version: 2, attempt_id: 13,
      material_fingerprint: await mutationMaterialFingerprint(body), capture_operation_id: body.capture_operation_id as string,
      active_response_event_id: target, response_version: ++version }
  }
  vi.spyOn(assessmentApi, 'getStudentAssessmentApi').mockReturnValue({ post: (_url: string, body: Record<string, unknown>) => save(body) } as never)
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, init) => {
    const bundle = JSON.parse(String(init?.body)).capture_bundle as CaptureBundle
    bundle.events.forEach(row => persisted.add(row.event_id!))
    history.push(bundle.events.some(row => row.event_id === target) ? 'old_target_uploaded' : 'facts_uploaded')
    return Response.json({ data: acknowledge(bundle), meta: { request_id: 'reproduction' } })
  })
  const controller = createSessionController({ store, csrfToken: 'csrf', autoSchedule: false })
  const runtime = await controller.activate(process, 'visited')
  expect(runtime.clientInstanceId).not.toBe(old.clientInstanceId)
  expect(runtime.restoredResponse?.event_id).toBe(target)
  await expect(runtime.pointerWritesSettled()).rejects.toMatchObject({ code: 'writer_lease_conflict' })
  expect(await store.readMetadata<CaptureMutation>('capture-mutation:7:13')).toBeNull()
  expect(persisted.has(target)).toBe(false)
  expect((await store.pendingEvents(old.clientInstanceId, 99)).some(row => row.event_id === target)).toBe(true)
  const retry = () => runtime.captureMutation!('/draft/', { answer: 'A', target_event_id: target,
    expected_version: runtime.responseVersion, writer_lease_token: 'fresh-lease',
    writer_client_instance_id: runtime.clientInstanceId }, save)
  return { retry, history, persisted, target, store, old, controller }
}

it('REPRO: saves a restored old-client answer after lease rejection without a second missing-target rejection', async () => {
  const scenario = await resumedDraft()
  try {
    await expect(scenario.retry()).resolves.toMatchObject({ active_response_event_id: scenario.target, response_version: 1 })
    expect(scenario.history).not.toContain('response_target_not_persisted')
  } finally { scenario.controller.dispose(); scenario.old.dispose() }
})

it('uploads the old target before saving and retains the recoverable answer', async () => {
  const scenario = await resumedDraft()
  try {
    expect((await scenario.store.pendingEvents(scenario.old.clientInstanceId, 99))
      .some(row => row.event_id === scenario.target)).toBe(true)
    await expect(scenario.retry()).resolves.toMatchObject({ active_response_event_id: scenario.target, response_version: 1 })
    expect(scenario.history).toEqual(['writer_lease_conflict',
      'old_target_uploaded', 'facts_uploaded', 'draft_saved'])
    expect(scenario.persisted.has(scenario.target)).toBe(true)
    expect((await scenario.store.restoreResponse(7, 13))?.event_id).toBe(scenario.target)
  } finally { scenario.controller.dispose(); scenario.old.dispose() }
})

it('keeps the old answer and does not send a new draft when its prerequisite upload fails', async () => {
  const scenario = await resumedDraft()
  try {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(Response.json({ error: { code: 'unavailable' } }, { status: 503 }))
    await expect(scenario.retry()).rejects.toThrow('作答轨迹尚未确认保存')
    expect(scenario.history).toEqual(['writer_lease_conflict'])
    expect(await scenario.store.readMetadata('capture-mutation:7:13')).toBeNull()
    expect((await scenario.store.pendingEvents(scenario.old.clientInstanceId, 99))
      .some(row => row.event_id === scenario.target)).toBe(true)
    await expect(scenario.retry()).resolves.toMatchObject({ active_response_event_id: scenario.target, response_version: 1 })
  } finally { scenario.controller.dispose(); scenario.old.dispose() }
})
