import type { StudentRuntimeQuestionEntry, SubmissionConfidence, SubmissionResponse, NavigationClickContext, EntryReceipt, NextEntryRequest, NextEntryOutcome, EnteredQuestion } from './api'
import { getBootstrapContext } from '@/app/bootstrap'
import { defaultOutbox } from './fact-store'
import { ProcessEventCollector } from './process-sender'
import { bindLifecycle } from './session-lifecycle'
import { DEFAULT_SYNC_INTERVAL_MS } from './process-types'
import type { CaptureStore, CaptureEvent, CaptureCheckpoint, CaptureMutation, CaptureMutationRejection, ProcessConfirmation, RecordOptions, FlushOptions } from './capture-contract'
import { createWriterLease, type WriterLease } from './writer-lease'
import { getStudentAssessmentApi, nextEntryRequest } from './api'
import { currentAssessmentOperationId, timedAssessmentStage } from './operation-timing'
import { matchesMutationRejection, mutationMaterialFingerprint, sameDraftSemantics } from './mutation-material'
type RuntimeProcess = StudentRuntimeQuestionEntry['process']
type VisitFields = 'visit_id' | 'visit_index' | 'entry_event_id' | 'navigation_id'
type ProcessState = Omit<RuntimeProcess, VisitFields> & Partial<Pick<RuntimeProcess, VisitFields>>
export interface StudentProcessRuntime {
  startBackgroundCapture?(): void
  restoredClosure?: SubmissionResponse | null
  saveClosureIntent?(intent: SubmissionResponse | null): Promise<void>
  closesWriterLease?: boolean
  captureMutation?<T extends ProcessConfirmation>(url: string, body: Record<string, unknown>, send: (body: Record<string, unknown>) => Promise<T>, operationId?: string | null, prepare?: () => Promise<Record<string, unknown> | undefined>): Promise<T>
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
  flushConfirmed(options?: Pick<FlushOptions, 'timeoutMs'>): Promise<unknown>
  closeAttempt(reason: string, toAssignmentId?: number | null, nextEntry?: NextEntryRequest): Promise<unknown>
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
  nextEntry?: NextEntryRequest
  inlineConfidence?: { probability: number | null; answer_event_id: string | null }
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
  private startedQuestion?: EnteredQuestion
  private enteredQuestion?: { sessionId: number; request: NextEntryRequest; dto: EnteredQuestion }
  private readonly tabId: string
  constructor(private readonly options: ControllerOptions) {
    this.store = options.store
    const key = 'kt-assessment-tab-id'
    this.tabId = options.tabId || sessionStorage.getItem(key) || crypto.randomUUID()
    sessionStorage.setItem(key, this.tabId)
  }
  async primeStart(dto: EnteredQuestion) {
    const receipt = dto.entry_receipt
    if (this.disposed) throw new Error('会话控制器已关闭。')
    if (this.store.isFallback) throw new Error('本地持久化不可用，不能可靠记录答案轨迹。请释放存储空间后重试。')
    if (dto.initial_writer_lease && dto.initial_writer_lease.writer_client_instance_id !== await assessmentClientInstanceId(this.store)) throw new Error('进入回执与本次客户端不一致。')
    this.validateEntry(dto.session_id, { assignmentId: dto.assignment.assignment_id, navigationId: receipt.navigation_id }, dto)
    // Activation has its own server entry and cannot be replayed as question navigation.
    this.startedQuestion = dto
  }
  takeStartedQuestion(sessionId: number, assignmentId: number): EnteredQuestion | undefined {
    const cached = this.startedQuestion
    if (!cached || cached.session_id !== sessionId || cached.assignment.assignment_id !== assignmentId) return undefined
    this.startedQuestion = undefined
    return cached
  }
  async freezeEntry(sessionId: number, assignmentId: number, reason: EntryIntent['reason'], click?: NavigationClickContext, source?: { sourceAssignmentId: number; targetUrl: string }): Promise<EntryIntent> {
    if (this.disposed) throw new Error('会话控制器已关闭。')
    if (this.store.isFallback) throw new Error('本地持久化不可用，不能可靠记录答案轨迹。请释放存储空间后重试。')
    let key = `entry-intent:${sessionId}:${this.tabId}`
    const saved = await this.store.readMetadata<EntryIntent>(key)
    if (saved) {
      if (saved.assignmentId !== assignmentId) {
        const prior = await this.store.readMetadata<EntryIntent>(`confirmed-entry:${sessionId}:${this.tabId}`)
        if (saved.sourceAssignmentId === assignmentId && prior?.assignmentId === assignmentId && (reason === 'resume' || click?.navigationId === prior.navigationId)) return prior
        if (saved.sourceAssignmentId !== assignmentId || reason !== 'resume') throw new Error('上一次进入结果尚未确认，请先核验原题目。')
        key = `source-resume:${sessionId}:${this.tabId}`
        const resume = await this.store.readMetadata<EntryIntent>(key)
        if (resume?.assignmentId === assignmentId) return resume
      } else {
        return saved
      }
    }
    if (reason === 'resume') {
      const prior = await this.store.readMetadata<EntryIntent>(`confirmed-entry:${sessionId}:${this.tabId}`)
      if (prior?.assignmentId === assignmentId) {
        await this.store.writeMetadata(key, prior)
        return prior
      }
    }
    const intent: EntryIntent = { ...(click || { navigationId: crypto.randomUUID(), clientOccurredAt: new Date().toISOString(),
      clientMonotonicMs: Math.round(performance.now() * 1000) / 1000, clientTimeOrigin: new Date(performance.timeOrigin).toISOString() }),
      clientInstanceId: await assessmentClientInstanceId(this.store), assignmentId, reason, ...source }
    await this.store.writeMetadata(key, intent)
    return intent
  }
  validateEntry(sessionId: number, intent: Pick<EntryIntent, 'assignmentId' | 'navigationId'>, dto: StudentRuntimeQuestionEntry & { entry_receipt: EntryReceipt }) {
    const receipt = dto?.entry_receipt
    if (!receipt || !dto.assignment || !dto.process
      || !Number.isSafeInteger(receipt.attempt_id) || receipt.attempt_id <= 0
      || !Number.isSafeInteger(receipt.visit_id) || receipt.visit_id <= 0
      || !Number.isSafeInteger(receipt.visit_index) || receipt.visit_index <= 0 || !receipt.entry_event_id
      || receipt.navigation_id !== intent.navigationId || receipt.navigation_id !== dto.process.navigation_id
      || dto.session_id !== sessionId || dto.assignment.assignment_id !== intent.assignmentId || receipt.session_id !== sessionId
      || dto.process.session_id !== sessionId || dto.process.attempt_id !== dto.assignment.attempt_id
      || receipt.assignment_id !== intent.assignmentId || receipt.attempt_id !== dto.assignment.attempt_id
      || receipt.visit_id !== dto.process.visit_id || receipt.visit_index !== dto.process.visit_index
      || receipt.entry_event_id !== dto.process.entry_event_id || !receipt.entered_at) throw new Error('进入回执与本次操作不一致，请核验原题目。')
  }
  private async entryForRequest(sessionId: number, request: NextEntryRequest): Promise<EntryIntent> {
    if (this.disposed) throw new Error('会话控制器已关闭。')
    if (this.store.isFallback) throw new Error('本地持久化不可用，不能可靠记录答案轨迹。请释放存储空间后重试。')
    const intent = await this.pendingEntry(sessionId)
    if (!intent || !this.matchesEntryRequest(intent, request)) throw new Error('目标题进入材料与本地冻结操作不一致，请核验原操作。')
    return intent
  }
  private matchesEntryRequest(intent: EntryIntent, request: NextEntryRequest): boolean {
    const frozen = nextEntryRequest(intent)
    return Object.keys(frozen).every(key => frozen[key as keyof NextEntryRequest] === request[key as keyof NextEntryRequest])
  }
  async rememberEnteredQuestion(sessionId: number, request: NextEntryRequest, outcome?: NextEntryOutcome): Promise<boolean> {
    const intent = await this.entryForRequest(sessionId, request)
    if (this.enteredQuestion?.sessionId === sessionId && this.enteredQuestion.request.navigation_id === request.navigation_id) this.enteredQuestion = undefined
    if (outcome?.committed === false && typeof outcome.error?.code === 'string' && outcome.error.code
      && typeof outcome.error.message === 'string' && outcome.error.message) return false
    if (outcome?.committed !== true || !outcome.runtime) throw new Error('目标题进入结果不完整，请核验原操作。')
    this.validateEntry(sessionId, intent, outcome.runtime)
    if (outcome.runtime.entry_allowed === false) return false
    if (outcome.runtime.entry_allowed !== true) throw new Error('目标题进入结果不完整，请核验原操作。')
    if (this.disposed) throw new Error('会话控制器已关闭。')
    this.enteredQuestion = { sessionId, request: { ...request }, dto: outcome.runtime }
    return true
  }
  takeEnteredQuestion(sessionId: number, intent: EntryIntent): EnteredQuestion | undefined {
    const cached = this.enteredQuestion
    if (!cached || cached.sessionId !== sessionId || !this.matchesEntryRequest(intent, cached.request)) return undefined
    this.validateEntry(sessionId, intent, cached.dto)
    this.enteredQuestion = undefined
    return cached.dto
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
    if (assignmentId !== undefined && intent?.assignmentId !== assignmentId) {
      const resumeKey = `source-resume:${sessionId}:${this.tabId}`
      const resume = await this.store.readMetadata<EntryIntent>(resumeKey)
      if (intent?.sourceAssignmentId === assignmentId && resume?.assignmentId === assignmentId) {
        await this.store.writeMetadata(`confirmed-entry:${sessionId}:${this.tabId}`, resume)
        await this.store.writeMetadata(resumeKey, null)
      }
      return
    }
    if (this.enteredQuestion?.sessionId === sessionId && (assignmentId === undefined || this.enteredQuestion.request.assignment_id === assignmentId)) this.enteredQuestion = undefined
    if (intent) await this.store.writeMetadata(`confirmed-entry:${sessionId}:${this.tabId}`, intent)
    await this.store.writeMetadata(key, null)
  }
  async cancelEntry(sessionId: number, navigationId: string) {
    const key = `entry-intent:${sessionId}:${this.tabId}`
    const intent = await this.store.readMetadata<EntryIntent>(key)
    if (intent?.navigationId === navigationId) {
      if (this.enteredQuestion?.sessionId === sessionId && this.enteredQuestion.request.navigation_id === navigationId) this.enteredQuestion = undefined
      await this.store.writeMetadata(key, null)
    }
  }
  async recoverClosedSubmission(process: RuntimeProcess) {
    const closeKey = `submission-close:${process.session_id}:${process.attempt_id}`;
    const closure = await this.store.readMetadata<SubmissionResponse>(closeKey);
    if (closure) {
      const pending = await this.store.readMetadata<CaptureMutation>(`capture-mutation:${process.session_id}:${process.attempt_id}`);
      const sender = new ProcessEventCollector({ sessionId: process.session_id, attemptId: process.attempt_id,
        endpoint: process.event_batch_url, checkpointEndpoint: process.checkpoint_url, csrfToken: this.options.csrfToken,
        captureBundle: process.capture_bundle, outbox: this.store, autoSchedule: false });
      if (pending?.body.leave_event_id) {
        const receipt = await getStudentAssessmentApi(pending.operationId ?? null).post<ProcessConfirmation>(pending.url, { ...pending.body, capture_bundle: pending.processSnapshot }, { timeoutMs: 30000 });
        if (receipt.capture_operation_id !== pending.body.capture_operation_id || receipt.writer_lease_released !== true) {
          throw new Error('收尾回执与本次操作不一致，请核验原操作。');
        }
        await sender.acknowledgeCaptureMutation(pending, receipt);
        await this.store.writeMetadata(`confirmed-close:${process.session_id}:${process.attempt_id}`, { leaveEventId: pending.body.leave_event_id, reason: 'submit' });
        if (this.sender?.attemptId === process.attempt_id) this.active?.writerLease?.invalidate();
        if (pending.body.next_entry) {
          const request = pending.body.next_entry as NextEntryRequest;
          await this.confirmLeave(process.session_id, request.navigation_id);
          await this.rememberEnteredQuestion(process.session_id, request, (receipt as ProcessConfirmation & { next_entry?: NextEntryOutcome }).next_entry);
        }
      } else if (!await this.store.readMetadata(`confirmed-close:${process.session_id}:${process.attempt_id}`)) {
        return null;
      }
      if (!/^\/student\/sessions\/\d+\/questions\/\d+\//.test(closure.next_url)
        && await sender.flushConfirmed({ ignoreVisibility: true, timeoutMs: 30000 }) === false) throw new Error('最终收尾检查点尚未确认。');
      await this.store.writeMetadata(closeKey, null);
      return closure;
    }
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
  activate(process: ProcessState, attemptStatus: string, initialLease?: StudentRuntimeQuestionEntry['initial_writer_lease']): Promise<StudentProcessRuntime> {
    const pending = this.activationQueue.then(() => this.install(process, attemptStatus, initialLease))
    this.activationQueue = pending.catch(() => undefined)
    return pending
  }
  private async install(process: ProcessState, attemptStatus: string, initialLease?: StudentRuntimeQuestionEntry['initial_writer_lease']): Promise<StudentProcessRuntime> {
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
    const clientInstanceId = await assessmentClientInstanceId(this.store)
    if (!this.sender) this.sender = new ProcessEventCollector({ sessionId: process.session_id, attemptId: process.attempt_id,
      endpoint: process.event_batch_url, checkpointEndpoint: process.checkpoint_url, csrfToken: this.options.csrfToken,
      appVersion: process.app_version, correlationId: process.navigation_id, outbox: this.store, uuid: () => clientInstanceId, autoSchedule: false })
    const sender = this.sender
    sender.autoSchedule = false
    sender.dispose()
    await sender.recordQueue
    if (this.disposed) throw new Error('会话控制器已关闭。')
    sender.attemptId = process.attempt_id
    sender.correlationId = process.navigation_id ?? null
    sender.endpoint = process.event_batch_url
    sender.checkpointEndpoint = process.checkpoint_url
    sender.captureBundle = process.capture_bundle
    sender.setOperationId(currentAssessmentOperationId())
    const open = process.paper_flow_version === 1 || !['submitted', 'timed_out', 'unanswered'].includes(attemptStatus)
    if (!this.lifecycle) {
      this.lifecycle = bindLifecycle(sender, { deferRendered: true, heartbeatIntervalSeconds: process.heartbeat_interval_seconds,
        attemptOpen: open, pendingNavigation: restoredNavigation, onlineOptions: { syncUrls } })
      await this.lifecycle.ready
      if (this.disposed) throw new Error('会话控制器已关闭。')
    } else this.lifecycle.activate(open, restoredNavigation)
    const lifecycle = this.lifecycle
    const generation = ++this.generation
    const assertActive = () => { if (generation !== this.generation) throw new Error('题目已经切换，旧操作不能写入新题目。') }
    const lease = process.writer_lease_acquire_url && process.writer_lease_renew_url && process.writer_lease_release_url
      ? createWriterLease({ client: { post: (url, body, options) => getStudentAssessmentApi().post(url, body, { ...options, timeoutMs: 30000 }) }, clientInstanceId: sender.clientInstanceId, acquireUrl: process.writer_lease_acquire_url,
        renewUrl: process.writer_lease_renew_url, releaseUrl: process.writer_lease_release_url, initialLease }) : undefined
    const restored = await this.store.restoreResponse(process.session_id, process.attempt_id)
    if (this.disposed) throw new Error('会话控制器已关闭。')
    let closeFlight: Promise<boolean> | undefined;
    const prepareMutation = async (intent: CaptureMutation, prepare?: () => Promise<Record<string, unknown> | undefined>) => {
      if (Object.hasOwn(intent.body, 'target_event_id') && !intent.body.writer_lease_token) {
        if (intent.attemptId !== process.attempt_id) throw Object.assign(new Error('先前题目的未发送草稿缺少原写入租约，请返回原题目核验。'), {
          code: 'capture_prior_lease_unavailable', fields: { attempt_id: intent.attemptId },
        });
        const credentials = prepare ? await prepare() : await lease?.beforePointerWrite();
        if (credentials) {
          intent = { ...intent, body: { ...intent.body, ...credentials } };
          await timedAssessmentStage('local_store', () => this.store.writeMetadata(`capture-mutation:${intent.sessionId}:${intent.attemptId}`, intent), intent.operationId ?? null);
        }
      }
      return intent;
    };
    const confirmMutation = async <T extends ProcessConfirmation>(intent: CaptureMutation, send: (body: Record<string, unknown>) => Promise<T>) => {
      if (intent.phase === 'prepared') intent = await prepareMutation(intent);
      const key = `capture-mutation:${intent.sessionId}:${intent.attemptId}`;
      const document = { ...intent.body, capture_bundle: intent.processSnapshot };
      const fingerprint = Object.hasOwn(intent.body, 'target_event_id') ? await mutationMaterialFingerprint(document) : undefined;
      if (intent.materialFingerprint && intent.materialFingerprint !== fingerprint) throw new Error('本地操作材料与冻结凭证不一致，请核验原操作。');
      if (intent.phase === 'prepared') {
        intent = { ...intent, phase: 'sent', materialFingerprint: fingerprint };
        await this.store.writeMetadata(key, intent);
      }
      let result: T;
      try { result = await send(document); }
      catch (error) {
        if (fingerprint && matchesMutationRejection(error, { mutationKind: 'draft', attemptId: intent.attemptId,
          materialFingerprint: fingerprint, captureOperationId: intent.body.capture_operation_id as string | undefined })) {
          if (!await this.store.rejectCaptureMutation(intent, {
            code: error.code, status: error.status, fields: error.fields, materialFingerprint: fingerprint,
            captureOperationId: intent.body.capture_operation_id as string, rejectedAt: new Date().toISOString(),
          })) throw new Error('拒绝回执与本地待确认操作不一致，请核验原操作。');
        }
        throw error;
      }
      const proof = result as ProcessConfirmation & { receipt_version?: number; attempt_id?: number; material_fingerprint?: string };
      if (proof.receipt_version !== undefined && (proof.receipt_version !== 2 || proof.attempt_id !== intent.attemptId
        || proof.material_fingerprint !== fingerprint || proof.capture_operation_id !== intent.body.capture_operation_id)) {
        throw new Error('操作回执与本次冻结材料不一致，请核验原操作。');
      }
      if (intent.body.leave_event_id && (result.capture_operation_id !== intent.body.capture_operation_id || result.writer_lease_released !== true)) {
        throw new Error('收尾回执与本次操作不一致，请核验原操作。');
      }
      let responseVersion: number | undefined;
      if (Object.hasOwn(intent.body, 'target_event_id')) {
        const draft = result as ProcessConfirmation & { response_version?: number };
        if (draft.active_response_event_id !== intent.body.target_event_id || !Number.isSafeInteger(draft.response_version)
          || Number(draft.response_version) < Number(intent.body.expected_version)) throw new Error('草稿回执与本次答案不一致，请核验原操作。');
        responseVersion = Number(draft.response_version);
      }
      await sender.acknowledgeCaptureMutation(intent, result);
      if (intent.body.leave_event_id) {
        const left = intent.processSnapshot.events.find(row => row.event_id === intent.body.leave_event_id);
        const visitId = left?.correlation_id || (intent.attemptId === process.attempt_id ? sender.correlationId : undefined);
        const local = await this.store.readMetadata<{ reason: string }>(`close-local:${intent.sessionId}:${intent.attemptId}:${visitId || 'unscoped'}`);
        await this.store.writeMetadata(`confirmed-close:${intent.sessionId}:${intent.attemptId}`, {
          leaveEventId: intent.body.leave_event_id, reason: left?.payload.leave_reason || local?.reason,
          visitId,
        });
        if (intent.attemptId === process.attempt_id) lease?.invalidate();
        if (intent.body.next_entry) {
          const request = intent.body.next_entry as NextEntryRequest;
          await this.confirmLeave(process.session_id, request.navigation_id);
          await this.rememberEnteredQuestion(process.session_id, request, (result as ProcessConfirmation & { next_entry?: NextEntryOutcome }).next_entry);
        }
      }
      if (responseVersion !== undefined && intent.attemptId === process.attempt_id) runtime.responseVersion = responseVersion;
      return result;
    };
    let priorMutationRecovery: Promise<void> | undefined;
    const recoverPriorMutations = () => {
      if (!priorMutationRecovery) priorMutationRecovery = (async () => {
        const pending = await this.store.pendingCaptureMutations(process.session_id);
        for (const intent of pending) {
          if (intent.attemptId === process.attempt_id) continue;
          await confirmMutation(intent, frozen => getStudentAssessmentApi(intent.operationId ?? null).post(intent.url, frozen, { timeoutMs: 30000 }));
        }
      })().finally(() => { priorMutationRecovery = undefined; });
      return priorMutationRecovery;
    };
    const runtime: StudentProcessRuntime = {
      captureMutation: async (url, body, send, operationId = currentAssessmentOperationId(), prepare) => {
        assertActive();
        await recoverPriorMutations();
        const pending = await this.store.readMetadata<CaptureMutation>(`capture-mutation:${process.session_id}:${process.attempt_id}`);
        if (pending) {
          const result = await confirmMutation(pending, frozen => getStudentAssessmentApi(pending.operationId ?? null).post<Awaited<ReturnType<typeof send>>>(pending.url, frozen, { timeoutMs: 30000 }));
          if (pending.url === url && (Object.hasOwn(body, 'target_event_id')
            ? sameDraftSemantics(pending.body, body) : JSON.stringify(pending.body) === JSON.stringify(body))) return result;
          if (pending.processSnapshot.client_instance_id !== sender.clientInstanceId
            && await sender.flushConfirmed({ ignoreVisibility: true, operationId: operationId || undefined }) === false) {
            throw new Error('作答轨迹尚未确认保存，请恢复网络后重试。');
          }
          if (Object.hasOwn(body, 'expected_version')) body = { ...body, expected_version: runtime.responseVersion };
        }
        const rejectionKey = `capture-mutation-rejection:${process.session_id}:${process.attempt_id}`;
        const rejection = await this.store.readMetadata<CaptureMutationRejection>(rejectionKey);
        if (rejection?.code === 'response_target_not_persisted') {
          const [events, head] = await Promise.all([
            this.store.pendingEvents(sender.clientInstanceId, Number.MAX_SAFE_INTEGER),
            this.store.restoreResponse(process.session_id, process.attempt_id),
          ]);
          const matchesTarget = (event: CaptureEvent) => event.event_id === body.target_event_id
            && event.session_id === process.session_id && event.attempt_id === process.attempt_id;
          if (!events.some(matchesTarget) && !(head && matchesTarget(head))) {
            throw Object.assign(new Error('本地缺少目标答案记录，不能重建保存请求。请保留本页并联系教师核验。'), {
              code: 'response_target_missing_local', fields: { target_event_id: body.target_event_id },
            });
          }
        }
        if (rejection?.code === 'response_target_not_persisted' && !rejection.materialSynchronized) {
          if (await sender.flushConfirmed({ ignoreVisibility: true, operationId: operationId || undefined, timeoutMs: 30000 }) === false) {
            throw new Error('目标答案轨迹尚未确认保存，请恢复网络后重试。');
          }
          await this.store.writeMetadata(rejectionKey, { ...rejection, materialSynchronized: true });
        }
        if (Object.hasOwn(body, 'target_event_id')) body = { ...body, capture_operation_id: crypto.randomUUID() };
        let intent: CaptureMutation;
        try { intent = await sender.freezeCaptureMutation(url, body, operationId); }
        catch (error) {
          if ((error as { code?: string }).code !== 'capture_bundle_too_large') throw error;
          if (await sender.flushConfirmed({ ignoreVisibility: true, operationId: operationId || undefined }) === false) throw error;
          intent = await sender.freezeCaptureMutation(url, body, operationId);
        }
        intent = { ...intent, phase: 'prepared' };
        await this.store.writeMetadata(`capture-mutation:${intent.sessionId}:${intent.attemptId}`, intent);
        intent = await prepareMutation(intent, prepare);
        return confirmMutation(intent, send);
      },
      serializePointerWrite: operation => {
        const operationId = currentAssessmentOperationId();
        const pending = timedAssessmentStage('pointer_queue', async () => {
          await this.pointerQueue;
          assertActive();
        }, operationId ?? null).then(operation);
        this.pointerQueue = pending.catch(() => undefined)
        return pending
      },
      pointerWritesSettled: async () => {
        await this.pointerQueue;
        if (closeFlight) await closeFlight;
        await recoverPriorMutations();
        const intent = await this.store.readMetadata<CaptureMutation>(`capture-mutation:${process.session_id}:${process.attempt_id}`);
        if (intent) await confirmMutation(intent, frozen => getStudentAssessmentApi(intent.operationId ?? null).post(intent.url, frozen, { timeoutMs: 30000 }));
      },
      sender, responseVersion: process.response_version || 0, writerLease: lease, clientInstanceId: sender.clientInstanceId,
      restoredNavigation, restoredSubmission, restoredResponse: restored as RestoredResponse | null,
      restoredClosure: await this.store.readMetadata<SubmissionResponse>(`submission-close:${process.session_id}:${process.attempt_id}`),
      saveClosureIntent: intent => this.store.writeMetadata(`submission-close:${process.session_id}:${process.attempt_id}`, intent),
      saveNavigationIntent: intent => this.store.saveNavigationIntent(process.session_id, process.attempt_id, intent),
      saveSubmissionIntent: intent => this.store.saveSubmissionIntent(process.session_id, process.attempt_id, intent),
      clearResponseRecovery: () => this.store.clearResponseRecovery(process.session_id, process.attempt_id),
      prepareNavigation: (token, target) => { assertActive(); return lifecycle.prepareNavigation(token, target).then(event => {
        if (!event.event_id) throw new Error('离题记录缺少事件标识。')
        return { event_id: event.event_id }
      }) },
      cancelNavigation: token => { assertActive(); lifecycle.cancelNavigation(token) },
      freezeNavigationSnapshot: async intent => {
        assertActive()
        await recoverPriorMutations()
        const operationId = sender.operationId
        try { return await sender.freezeNavigationSnapshot(intent) }
        catch (error) {
          if (!['navigation_snapshot_backlog', 'navigation_snapshot_too_large'].includes((error as { code?: string }).code || '')) throw error
          const boundary = await sender._freezeBoundary()
          if (!await sender.flushConfirmed({ ignoreVisibility: true, operationId, boundary })) throw error
          assertActive()
          return sender.freezeNavigationSnapshot(intent)
        }
      },
      acknowledgeNavigation: (snapshot, receipt) => sender.acknowledgeNavigation(snapshot, receipt),
      freezeSubmissionSnapshot: intent => { assertActive(); return sender.freezeSubmissionSnapshot(intent) },
      acknowledgeSubmission: (snapshot, receipt) => sender.acknowledgeSubmission(snapshot, receipt),
      setOperationId: operationId => { assertActive(); sender.setOperationId(operationId) },
      record: async (type, payload, options) => { assertActive(); return sender.record(type, payload, options) },
      checkpoint: trigger => { assertActive(); return sender.checkpoint(trigger) },
      flushBounded: async timeout => { await recoverPriorMutations(); return sender.flushBounded(timeout, { ignoreVisibility: true, keepalive: true }) },
      flushConfirmed: async options => { await recoverPriorMutations(); return sender.flushConfirmed({ ignoreVisibility: true, keepalive: true, ...options }) },
      closesWriterLease: process.close_bundle?.enabled === true && process.close_bundle.version === 1 && process.capture_bundle?.mutation_receipts === true,
      closeAttempt: async (reason, target = null, nextEntry) => {
        assertActive();
        if (!runtime.closesWriterLease) return lifecycle.closeAttempt(reason, target);
        const close = async () => {
          const confirmed = await this.store.readMetadata<{ reason?: string; visitId?: string }>(`confirmed-close:${process.session_id}:${process.attempt_id}`);
          if (confirmed?.reason === reason && (reason === 'submit' || confirmed.visitId === sender.correlationId)) return true;
          const pending = await this.store.readMetadata<CaptureMutation>(`capture-mutation:${process.session_id}:${process.attempt_id}`);
          if (pending?.body.leave_event_id) {
            await confirmMutation(pending, frozen => getStudentAssessmentApi(pending.operationId ?? null).post(pending.url, frozen, { timeoutMs: 30000 }));
            return true;
          }
          if (nextEntry) await this.entryForRequest(process.session_id, nextEntry);
          await lifecycle.closeAttempt(reason, target, false);
          const local = await this.store.readMetadata<{ eventId: string }>(sender.closeMetadataKey());
          if (!local) return true;
          const credentials = lease?.cachedCredentials() || await lease?.beforePointerWrite();
          if (!credentials || !process.writer_lease_release_url) throw new Error('缺少收尾租约，请核验原操作。');
          await runtime.captureMutation!(process.writer_lease_release_url, {
            lease_token: credentials.writer_lease_token, client_instance_id: credentials.writer_client_instance_id,
            capture_operation_id: crypto.randomUUID(), leave_event_id: local.eventId,
            ...(nextEntry ? { next_entry: nextEntry } : {}),
          }, frozen => getStudentAssessmentApi().post(process.writer_lease_release_url!, frozen, { timeoutMs: 30000 }));
          return true;
        };
        if (!closeFlight) closeFlight = close().finally(() => { closeFlight = undefined; });
        return closeFlight;
      },
      startBackgroundCapture: () => {
        assertActive()
        if (this.options.autoSchedule === false || sender.autoSchedule) return
        sender.autoSchedule = true
        sender.scheduleUpload(250 + Math.floor(Math.random() * 500))
        sender.startSynchronization(syncUrls, DEFAULT_SYNC_INTERVAL_MS, false)
        sender.setTimeout(() => { if (generation === this.generation) void sender.synchronizeClock(syncUrls) }, 250 + Math.floor(Math.random() * 500))
      },
      dispose: () => { if (generation === this.generation) { this.generation++; sender.autoSchedule = false; sender.dispose() } },
    }
    if (!process.capture_bundle?.enabled || process.capture_bundle.version !== 1 || process.capture_bundle.mutation_receipts !== true) runtime.captureMutation = undefined;
    if (!first && this.active?.writerLease) this.active.writerLease.invalidate()
    this.active = runtime
    return runtime
  }
  dispose() { this.disposed = true; this.startedQuestion = undefined; this.enteredQuestion = undefined; this.generation++; this.lifecycle?.dispose(); this.sender?.dispose() }
}
export const createSessionController = (options: ControllerOptions) => new SessionController(options)
const sessions = new Map<number, Promise<SessionController>>()
export function getSessionController(sessionId: number, csrfToken: string): Promise<SessionController> {
  let pending = sessions.get(sessionId)
  if (!pending) {
    pending = defaultOutbox().then(async store => {
      await assessmentClientInstanceId(store)
      return createSessionController({ store, csrfToken })
    })
    sessions.set(sessionId, pending)
  }
  return pending
}
export async function createProcessRuntime(process: ProcessState, status: string, csrfToken: string, initialLease?: StudentRuntimeQuestionEntry['initial_writer_lease']): Promise<StudentProcessRuntime> {
  return (await getSessionController(process.session_id, csrfToken)).activate(process, status, initialLease)
}
export function disposeSessionController(sessionId: number) {
  const pending = sessions.get(sessionId)
  sessions.delete(sessionId)
  void pending?.then(controller => controller.dispose())
}

export function disposeAllSessionControllers() {
  for (const sessionId of sessions.keys()) disposeSessionController(sessionId)
}

const identityFlights = new Map<number, Promise<string>>()
let documentTabFlight: Promise<string> | undefined

async function holdDocumentTabId(tabId: string): Promise<boolean> {
  return new Promise((resolve, reject) => {
    void navigator.locks.request(`kt-assessment-tab:${tabId}`, { mode: 'exclusive', ifAvailable: true }, async lock => {
      if (!lock) { resolve(false); return }
      let release!: () => void
      const lifetime = new Promise<void>(done => { release = done })
      window.addEventListener('pagehide', () => {
        release()
        documentTabFlight = undefined
        identityFlights.clear()
      }, { once: true })
      resolve(true)
      await lifetime
    }).catch(reject)
  })
}

function ownedDocumentTabId(): Promise<string> {
  if (!documentTabFlight) documentTabFlight = (async () => {
    const tabKey = 'kt-assessment-tab-id'
    let tabId = sessionStorage.getItem(tabKey) || crypto.randomUUID()
    while (!await holdDocumentTabId(tabId)) tabId = crypto.randomUUID()
    sessionStorage.setItem(tabKey, tabId)
    return tabId
  })().catch(error => { documentTabFlight = undefined; throw error })
  return documentTabFlight
}

export function assessmentClientInstanceId(store?: CaptureStore): Promise<string> {
  const userId = getBootstrapContext().user?.id || 0
  const pending = identityFlights.get(userId)
  if (pending) return pending
  const identity = (async () => {
    // Without document ownership, retain a fresh legacy identity instead of sharing a lease from copied storage.
    if (!navigator.locks) return crypto.randomUUID()
    const capture = store || await defaultOutbox()
    const tabId = await ownedDocumentTabId()
    const key = `assessment-client:${userId}:${tabId}`
    const saved = await capture.readMetadata<string>(key)
    const id = saved || crypto.randomUUID()
    if (!saved) await capture.writeMetadata(key, id)
    return id
  })().catch(error => { identityFlights.delete(userId); throw error })
  identityFlights.set(userId, identity)
  return identity
}
