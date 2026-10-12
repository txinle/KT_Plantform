import type { StudentRuntimeQuestionEntry, SubmissionConfidence, NavigationClickContext, EntryReceipt } from './api'
import { defaultOutbox } from './fact-store'
import { ProcessEventCollector } from './process-sender'
import { bindLifecycle } from './session-lifecycle'
import { DEFAULT_SYNC_INTERVAL_MS } from './process-types'
import type { CaptureStore, CaptureEvent, CaptureCheckpoint, RecordOptions } from './capture-contract'
import { createWriterLease, type WriterLease } from './writer-lease'
import { getStudentAssessmentApi } from './api'
import { currentAssessmentOperationId } from './operation-timing'
type RuntimeProcess = StudentRuntimeQuestionEntry['process']
type VisitFields = 'visit_id' | 'visit_index' | 'entry_event_id' | 'navigation_id'
type ProcessState = Omit<RuntimeProcess, VisitFields> & Partial<Pick<RuntimeProcess, VisitFields>>
export interface StudentProcessRuntime {
  serializePointerWrite<T>(operation: () => Promise<T>): Promise<T>
  pointerWritesSettled(): Promise<unknown>
  restoredNavigation?: NavigationIntent | null
  saveNavigationIntent?(intent: NavigationIntent | null): Promise<void | undefined>
  prepareNavigation?(token: string, toAssignmentId: number): Promise<{ event_id: string }>
  cancelNavigation?(token: string): void
  freezeNavigationSnapshot?(intent: NavigationIntent): Promise<NavigationIntent & { processSnapshot: SubmissionProcessSnapshot }>
  acknowledgeNavigation?(snapshot: SubmissionProcessSnapshot, response: SavedNavigationConfirmation): Promise<void>
  restoredSubmission?: SubmissionIntent | null
  saveSubmissionIntent?(intent: SubmissionIntent | null): Promise<void>
  freezeSubmissionSnapshot?(intent: SubmissionIntent): Promise<SubmissionIntent & { processSnapshot: SubmissionProcessSnapshot }>
  acknowledgeSubmission?(snapshot: SubmissionProcessSnapshot, response: SubmissionProcessConfirmation): Promise<void>
  setOperationId?(operationId?: string): void
  clientInstanceId?: string
  clearResponseRecovery?(): Promise<void | undefined>
  restoredResponse?: RestoredResponse | null
  record(eventType: string, payload: Record<string, unknown>, options?: RecordOptions): Promise<CaptureEvent>
  checkpoint(trigger: string): Promise<unknown>
  flushBounded(timeoutMs: number): Promise<unknown>
  flushConfirmed(): Promise<unknown>
  closeAttempt(reason: string, toAssignmentId?: number | null): Promise<unknown>
  responseVersion: number
  sender: ProcessEventCollector
  writerLease?: WriterLease
  dispose(): void
}

export interface SubmissionIntent {
  token: string
  clickedAt: string
  answer: string
  head?: string | null
  expectedResponseVersion?: number
  confidence?: SubmissionConfidence
  processSnapshot?: SubmissionProcessSnapshot
  [key: string]: unknown
}

export interface NavigationIntent {
  token: string
  clickedAt: string
  toAssignmentId: number
  leaveEventId: string
  head: string | null
  expectedResponseVersion: number
  writerLeaseToken?: string
  writerClientInstanceId?: string
  processSnapshot?: SubmissionProcessSnapshot
  [key: string]: unknown
}

export interface SavedNavigationConfirmation extends SubmissionProcessConfirmation {
  navigation_token: string
  to_assignment_id: number
  writer_lease_released: boolean
  active_response_event_id: string | null
}

export interface SubmissionProcessSnapshot {
  client_instance_id: string
  event_boundary: number
  events: CaptureEvent[]
  checkpoints: CaptureCheckpoint[]
}

export interface SubmissionProcessConfirmation {
  committed?: boolean
  acknowledgements?: Array<{ client_sequence_no: number; event_id?: string }>
  checkpoint_ids?: string[]
}

export interface RestoredResponse {
  event_id: string
  payload: { after_state: { value: unknown }; branch_id: string }
}


export interface EntryIntent extends NavigationClickContext { assignmentId: number; reason: 'navigation_click' | 'resume'; sourceAssignmentId?: number; targetUrl?: string; leaveConfirmed?: boolean }
interface ControllerOptions { store: CaptureStore; csrfToken: string; autoSchedule?: boolean; tabId?: string }

export class SessionController {
  readonly store: CaptureStore
  sender?: ProcessEventCollector
  private lifecycle?: ReturnType<typeof bindLifecycle>
  private generation = 0
  private active?: StudentProcessRuntime
  private disposed = false
  private activationQueue: Promise<unknown> = Promise.resolve()
  private pointerQueue: Promise<unknown> = Promise.resolve()
  private sessionId?: number
  private readonly tabId: string
  constructor(private readonly options: ControllerOptions) {
    this.store = options.store
    const key = 'kt-assessment-tab-id'
    this.tabId = options.tabId || sessionStorage.getItem(key) || crypto.randomUUID()
    sessionStorage.setItem(key, this.tabId)
  }
  async freezeEntry(sessionId: number, assignmentId: number, reason: EntryIntent['reason'], click?: NavigationClickContext, source?: { sourceAssignmentId: number; targetUrl: string }): Promise<EntryIntent> {
    if (this.disposed) throw new Error('会话控制器已关闭。')
    if (this.store.isFallback) throw new Error('本地持久化不可用，不能可靠记录答案轨迹。请释放存储空间后重试。')
    const key = `entry-intent:${sessionId}:${this.tabId}`
    const saved = await this.store.readMetadata<EntryIntent>(key)
    if (saved) {
      if (saved.assignmentId !== assignmentId) {
        const prior = await this.store.readMetadata<EntryIntent>(`confirmed-entry:${sessionId}:${this.tabId}`)
        if (saved.sourceAssignmentId === assignmentId && prior?.assignmentId === assignmentId && (reason === 'resume' || click?.navigationId === prior.navigationId)) return prior
        throw new Error('上一次进入结果尚未确认，请先核验原题目。')
      }
      return saved
    }
    if (reason === 'resume') {
      const prior = await this.store.readMetadata<EntryIntent>(`confirmed-entry:${sessionId}:${this.tabId}`)
      if (prior?.assignmentId === assignmentId) {
        await this.store.writeMetadata(key, prior)
        return prior
      }
    }
    const intent: EntryIntent = { ...(click || { navigationId: crypto.randomUUID(), clientOccurredAt: new Date().toISOString(),
      clientMonotonicMs: Math.round(performance.now() * 1000) / 1000, clientTimeOrigin: new Date(performance.timeOrigin).toISOString() }), assignmentId, reason, ...source }
    await this.store.writeMetadata(key, intent)
    return intent
  }
  validateEntry(sessionId: number, intent: EntryIntent, dto: StudentRuntimeQuestionEntry & { entry_receipt: EntryReceipt }) {
    const receipt = dto.entry_receipt
    if (!receipt || receipt.navigation_id !== intent.navigationId || receipt.navigation_id !== dto.process.navigation_id
      || dto.session_id !== sessionId || dto.assignment.assignment_id !== intent.assignmentId || receipt.session_id !== sessionId
      || receipt.assignment_id !== intent.assignmentId || receipt.attempt_id !== dto.assignment.attempt_id
      || receipt.visit_id !== dto.process.visit_id || receipt.visit_index !== dto.process.visit_index
      || receipt.entry_event_id !== dto.process.entry_event_id || !receipt.entered_at) throw new Error('进入回执与本次操作不一致，请核验原题目。')
  }
  pendingEntry(sessionId: number) { return this.store.readMetadata<EntryIntent>(`entry-intent:${sessionId}:${this.tabId}`) }
  async confirmLeave(sessionId: number, navigationId: string) {
    const key = `entry-intent:${sessionId}:${this.tabId}`
    const intent = await this.store.readMetadata<EntryIntent>(key)
    if (intent?.navigationId === navigationId) await this.store.writeMetadata(key, { ...intent, leaveConfirmed: true })
  }
  async confirmEntry(sessionId: number, assignmentId?: number) {
    const key = `entry-intent:${sessionId}:${this.tabId}`
    const intent = await this.store.readMetadata<EntryIntent>(key)
    if (assignmentId !== undefined && intent?.assignmentId !== assignmentId) return
    if (intent) await this.store.writeMetadata(`confirmed-entry:${sessionId}:${this.tabId}`, intent)
    await this.store.writeMetadata(key, null)
  }
  async cancelEntry(sessionId: number, navigationId: string) {
    const key = `entry-intent:${sessionId}:${this.tabId}`
    const intent = await this.store.readMetadata<EntryIntent>(key)
    if (intent?.navigationId === navigationId) await this.store.writeMetadata(key, null)
  }
  async recoverClosedSubmission(process: RuntimeProcess) {
    const intent = await this.store.restoreSubmissionIntent(process.session_id, process.attempt_id) as SubmissionIntent | null
    if (!intent) return null
    const recovered = await getStudentAssessmentApi().recover(process.attempt_id, intent.token)
    if (!recovered.result || recovered.submission_matches !== true) return null
    if (intent.processSnapshot) {
      const sender = new ProcessEventCollector({ sessionId: process.session_id, attemptId: process.attempt_id,
        endpoint: process.event_batch_url, checkpointEndpoint: process.checkpoint_url, csrfToken: this.options.csrfToken,
        outbox: this.store, autoSchedule: false })
      await sender.acknowledgeSubmission(intent.processSnapshot, recovered.result)
    }
    await this.store.saveSubmissionIntent(process.session_id, process.attempt_id, null)
    return recovered.result
  }
  activate(process: ProcessState, attemptStatus: string): Promise<StudentProcessRuntime> {
    const pending = this.activationQueue.then(() => this.install(process, attemptStatus))
    this.activationQueue = pending.catch(() => undefined)
    return pending
  }
  private async install(process: ProcessState, attemptStatus: string): Promise<StudentProcessRuntime> {
    await this.pointerQueue
    if (this.disposed) throw new Error('会话控制器已关闭。')
    if (this.sessionId !== undefined && this.sessionId !== process.session_id) throw new Error('Session controller cannot change session identity.')
    if (process.capture_version === 'capture-v2' && this.store.isFallback) throw new Error('本地持久化不可用，不能可靠记录答案轨迹。请释放存储空间后重试。')
    this.sessionId = process.session_id
    const restoredNavigation = await this.store.restoreNavigationIntent(process.session_id, process.attempt_id) as NavigationIntent | null
    const restoredSubmission = await this.store.restoreSubmissionIntent(process.session_id, process.attempt_id) as SubmissionIntent | null
    const syncUrls = { createUrl: process.time_sync_create_url, finalizeUrl: process.time_sync_finalize_url }
    if (this.disposed) throw new Error('会话控制器已关闭。')
    const first = !this.sender
    if (!this.sender) this.sender = new ProcessEventCollector({ sessionId: process.session_id, attemptId: process.attempt_id,
      endpoint: process.event_batch_url, checkpointEndpoint: process.checkpoint_url, csrfToken: this.options.csrfToken,
      appVersion: process.app_version, correlationId: process.navigation_id, outbox: this.store, autoSchedule: this.options.autoSchedule })
    const sender = this.sender
    await sender.recordQueue
    if (this.disposed) throw new Error('会话控制器已关闭。')
    sender.attemptId = process.attempt_id
    sender.correlationId = process.navigation_id ?? null
    sender.endpoint = process.event_batch_url
    sender.checkpointEndpoint = process.checkpoint_url
    sender.setOperationId(currentAssessmentOperationId())
    const open = !['submitted', 'timed_out', 'unanswered'].includes(attemptStatus)
    if (!this.lifecycle) {
      this.lifecycle = bindLifecycle(sender, { deferRendered: true, heartbeatIntervalSeconds: process.heartbeat_interval_seconds,
        attemptOpen: open, pendingNavigation: restoredNavigation, onlineOptions: { syncUrls } })
      await this.lifecycle.ready
      if (this.disposed) throw new Error('会话控制器已关闭。')
      void sender.synchronizeClock(syncUrls).then(() => sender.flush()).catch(() => undefined)
      sender.startSynchronization(syncUrls, DEFAULT_SYNC_INTERVAL_MS, false)
    } else this.lifecycle.activate(open, restoredNavigation)
    const lifecycle = this.lifecycle
    const generation = ++this.generation
    const assertActive = () => { if (generation !== this.generation) throw new Error('题目已经切换，旧操作不能写入新题目。') }
    const lease = process.writer_lease_acquire_url && process.writer_lease_renew_url && process.writer_lease_release_url
      ? createWriterLease({ client: getStudentAssessmentApi(), clientInstanceId: sender.clientInstanceId, acquireUrl: process.writer_lease_acquire_url,
        renewUrl: process.writer_lease_renew_url, releaseUrl: process.writer_lease_release_url }) : undefined
    const restored = await this.store.restoreResponse(process.session_id, process.attempt_id)
    if (this.disposed) throw new Error('会话控制器已关闭。')
    const runtime: StudentProcessRuntime = {
      serializePointerWrite: operation => {
        const pending = this.pointerQueue.then(() => { assertActive(); return operation() })
        this.pointerQueue = pending.catch(() => undefined)
        return pending
      },
      pointerWritesSettled: () => this.pointerQueue,
      sender, responseVersion: process.response_version || 0, writerLease: lease, clientInstanceId: sender.clientInstanceId,
      restoredNavigation, restoredSubmission, restoredResponse: restored as RestoredResponse | null,
      saveNavigationIntent: intent => this.store.saveNavigationIntent(process.session_id, process.attempt_id, intent),
      saveSubmissionIntent: intent => this.store.saveSubmissionIntent(process.session_id, process.attempt_id, intent),
      clearResponseRecovery: () => this.store.clearResponseRecovery(process.session_id, process.attempt_id),
      prepareNavigation: (token, target) => { assertActive(); return lifecycle.prepareNavigation(token, target).then(event => {
        if (!event.event_id) throw new Error('离题记录缺少事件标识。')
        return { event_id: event.event_id }
      }) },
      cancelNavigation: token => { assertActive(); lifecycle.cancelNavigation(token) },
      freezeNavigationSnapshot: intent => { assertActive(); return sender.freezeNavigationSnapshot(intent) },
      acknowledgeNavigation: (snapshot, receipt) => sender.acknowledgeNavigation(snapshot, receipt),
      freezeSubmissionSnapshot: intent => { assertActive(); return sender.freezeSubmissionSnapshot(intent) },
      acknowledgeSubmission: (snapshot, receipt) => sender.acknowledgeSubmission(snapshot, receipt),
      setOperationId: operationId => { assertActive(); sender.setOperationId(operationId) },
      record: async (type, payload, options) => { assertActive(); return sender.record(type, payload, options) },
      checkpoint: trigger => { assertActive(); return sender.checkpoint(trigger) },
      flushBounded: timeout => sender.flushBounded(timeout, { ignoreVisibility: true, keepalive: true }),
      flushConfirmed: () => sender.flushConfirmed({ ignoreVisibility: true, keepalive: true }),
      closeAttempt: (reason, target = null) => { assertActive(); return lifecycle.closeAttempt(reason, target) },
      dispose: () => { if (generation === this.generation) this.generation++ },
    }
    if (!first && this.active?.writerLease) this.active.writerLease.invalidate()
    this.active = runtime
    return runtime
  }
  dispose() { this.disposed = true; this.generation++; this.lifecycle?.dispose(); this.sender?.dispose() }
}
export const createSessionController = (options: ControllerOptions) => new SessionController(options)
const sessions = new Map<number, Promise<SessionController>>()
export function getSessionController(sessionId: number, csrfToken: string): Promise<SessionController> {
  let pending = sessions.get(sessionId)
  if (!pending) {
    pending = defaultOutbox().then(store => createSessionController({ store, csrfToken }))
    sessions.set(sessionId, pending)
  }
  return pending
}
export async function createProcessRuntime(process: ProcessState, status: string, csrfToken: string): Promise<StudentProcessRuntime> {
  return (await getSessionController(process.session_id, csrfToken)).activate(process, status)
}
export function disposeSessionController(sessionId: number) {
  const pending = sessions.get(sessionId)
  sessions.delete(sessionId)
  void pending?.then(controller => controller.dispose())
}

export function disposeAllSessionControllers() {
  for (const sessionId of sessions.keys()) disposeSessionController(sessionId)
}
