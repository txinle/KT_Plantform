import type { StudentRuntimeQuestionEntry } from './api'

type ProcessState = StudentRuntimeQuestionEntry['process']

interface ProcessCollector {
  record(eventType: string, payload: Record<string, unknown>, options?: Record<string, unknown>): Promise<unknown>
  checkpoint(trigger: string): Promise<unknown>
  flush(): Promise<unknown>
  flushBounded(timeoutMs: number, options?: Record<string, unknown>): Promise<unknown>
  synchronizeClock(urls: SyncUrls): Promise<unknown>
  startSynchronization(urls: SyncUrls, intervalMs: number, immediate: boolean): void
}

interface ProcessLifecycle {
  ready: Promise<unknown>
  closeAttempt(reason: string, toAssignmentId?: number | null): Promise<unknown>
  dispose(): void
}

interface SyncUrls {
  createUrl: string
  finalizeUrl: string
}

interface ProcessDataApi {
  defaultOutbox(): Promise<unknown>
  ProcessEventCollector: new (options: Record<string, unknown>) => ProcessCollector
  bindLifecycle(collector: ProcessCollector, options: Record<string, unknown>): ProcessLifecycle
  DEFAULT_SYNC_INTERVAL_MS: number
}

export interface StudentProcessRuntime {
  record(eventType: string, payload: Record<string, unknown>, options?: Record<string, unknown>): Promise<unknown>
  checkpoint(trigger: string): Promise<unknown>
  flushBounded(timeoutMs: number): Promise<unknown>
  closeAttempt(reason: string, toAssignmentId?: number | null): Promise<unknown>
  dispose(): void
}

export async function createProcessRuntime(
  processState: ProcessState,
  attemptStatus: string,
  csrfToken: string,
): Promise<StudentProcessRuntime> {
  const processApi = (globalThis as typeof globalThis & { KTProcessData?: ProcessDataApi }).KTProcessData
  if (!processApi) throw new Error('作答过程记录器未加载，请刷新页面后重试。')

  const outbox = await processApi.defaultOutbox()
  const collector = new processApi.ProcessEventCollector({
    sessionId: processState.session_id,
    attemptId: processState.attempt_id,
    endpoint: processState.event_batch_url,
    checkpointEndpoint: processState.checkpoint_url,
    csrfToken,
    appVersion: processState.app_version,
    outbox,
  })
  const syncUrls = {
    createUrl: processState.time_sync_create_url,
    finalizeUrl: processState.time_sync_finalize_url,
  }
  const lifecycle = processApi.bindLifecycle(collector, {
    heartbeatIntervalSeconds: processState.heartbeat_interval_seconds,
    attemptOpen: !['submitted', 'timed_out', 'unanswered'].includes(attemptStatus),
    onlineOptions: { syncUrls },
  })
  await lifecycle.ready
  void collector.synchronizeClock(syncUrls).then(() => collector.flush()).catch(() => undefined)
  collector.startSynchronization(syncUrls, processApi.DEFAULT_SYNC_INTERVAL_MS, false)

  return {
    record: (eventType, payload, options = {}) => collector.record(eventType, payload, options),
    checkpoint: (trigger) => collector.checkpoint(trigger),
    flushBounded: (timeoutMs) => collector.flushBounded(timeoutMs, {
      ignoreVisibility: true,
      keepalive: true,
    }),
    closeAttempt: (reason, toAssignmentId = null) => lifecycle.closeAttempt(
      reason,
      toAssignmentId,
    ),
    dispose: () => lifecycle.dispose(),
  }
}
