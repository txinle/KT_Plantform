import { beforeEach, describe, expect, it, vi } from 'vitest'

import { createProcessRuntime } from './process-bridge'

const processState = {
  visit_id: 41,
  visit_index: 2,
  entry_event_id: '00000000-0000-4000-8000-000000000040',
  navigation_id: '00000000-0000-4000-8000-000000000041',
  session_id: 7,
  attempt_id: 13,
  event_batch_url: '/assessment/process/events/batch/',
  checkpoint_url: '/assessment/process/capture-checkpoints/',
  time_sync_create_url: '/assessment/process/time-sync/create/',
  time_sync_finalize_url: '/assessment/process/time-sync/finalize/',
  app_version: '2026.08.29.2',
  heartbeat_interval_seconds: 30,
}

describe('assessment process bridge', () => {
  const outbox = { name: 'existing-indexed-db-outbox' }
  const collector = {
    record: vi.fn(),
    dispose: vi.fn(),
    checkpoint: vi.fn(),
    flushBounded: vi.fn(),
    flush: vi.fn(),
    synchronizeClock: vi.fn().mockResolvedValue(undefined),
    startSynchronization: vi.fn(),
  }
  const lifecycle = {
    ready: Promise.resolve(),
    closeAttempt: vi.fn(),
    dispose: vi.fn(),
  }
  const ProcessEventCollector = vi.fn(function processCollector() {
    return collector
  })
  const defaultOutbox = vi.fn().mockResolvedValue(outbox)
  const bindLifecycle = vi.fn().mockReturnValue(lifecycle)

  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('KTProcessData', {
      defaultOutbox,
      ProcessEventCollector,
      bindLifecycle,
      DEFAULT_SYNC_INTERVAL_MS: 900_000,
    })
  })

  it('reuses the version-2 legacy outbox and one collector without dual writes', async () => {
    const runtime = await createProcessRuntime(processState, 'visited', 'csrf-token')

    expect(defaultOutbox).toHaveBeenCalledOnce()
    expect(ProcessEventCollector).toHaveBeenCalledWith({
      sessionId: 7,
      attemptId: 13,
      endpoint: '/assessment/process/events/batch/',
      checkpointEndpoint: '/assessment/process/capture-checkpoints/',
      csrfToken: 'csrf-token',
      appVersion: '2026.08.29.2',
      correlationId: '00000000-0000-4000-8000-000000000041',
      outbox,
    })
    expect(bindLifecycle).toHaveBeenCalledWith(collector, {
      deferRendered: true,
      heartbeatIntervalSeconds: 30,
      attemptOpen: true,
      onlineOptions: {
        syncUrls: {
          createUrl: '/assessment/process/time-sync/create/',
          finalizeUrl: '/assessment/process/time-sync/finalize/',
        },
      },
    })
    expect(collector.startSynchronization).toHaveBeenCalledWith(
      {
        createUrl: '/assessment/process/time-sync/create/',
        finalizeUrl: '/assessment/process/time-sync/finalize/',
      },
      900_000,
      false,
    )

    await runtime.record('response_input_started', { question_type: 'single_choice' })
    await runtime.closeAttempt('navigation', 14)
    runtime.dispose()

    expect(collector.record).toHaveBeenCalledWith(
      'response_input_started',
      { question_type: 'single_choice' },
      {},
    )
    expect(lifecycle.closeAttempt).toHaveBeenCalledWith('navigation', 14)
    expect(lifecycle.dispose).toHaveBeenCalledOnce()
    expect(collector.dispose).toHaveBeenCalledOnce()
  })
})
