import { createApiClient } from '@/api/client'
import type { components } from '@/api/generated/schema'
import { getBootstrapContext } from '@/app/bootstrap'
import type { PreparedPaper, PaperSubmissionIntent, PaperSubmissionResponse } from './prepared-paper'
import type { WriterLeaseCredentials } from './writer-lease'
import { currentAssessmentOperationId } from './operation-timing'
import { currentAdmissionCredentials, readAdmissionCredentials } from './admission'
import type { ToolRule } from '@/features/teacher-experiments/research-tools'
import type { CaptureEvent, CaptureCheckpoint, CaptureBundle, CaptureBundleCapability, ProcessConfirmation } from './capture-contract'
import type { SavedNavigationConfirmation } from './session-controller'

export async function withTransportTimeout<T>(send: (signal?: AbortSignal) => Promise<T>, timeoutMs?: number): Promise<T> {
  if (timeoutMs === undefined) return send()
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(new DOMException('收尾请求等待超时，结果尚待核验。', 'TimeoutError')), timeoutMs)
  try { return await send(controller.signal) }
  finally { clearTimeout(timer) }
}

export interface StudentResearchToolConfiguration {
  content_version: string
  doubt_enabled?: boolean
  check_enabled?: boolean
  hints?: { id: string; level: number }[]
  confidence_scale?: { scale_version: string; minimum: number; maximum: number; step: number }
  rules: Array<ToolRule & { pending_request?: { event_id: string; content_version: string; text?: string }; already_presented?: boolean }>
  state?: { doubt: boolean; pending_check: boolean; checking: boolean; doubt_note: string }
}

export interface AtomicSubmissionEnvelope {
  version: 1
  client_instance_id: string
  event_boundary: number
  events: CaptureEvent[]
  checkpoints: CaptureCheckpoint[]
  expected_version: number
}

export interface SavedNavigationRequest {
  navigation_token: string
  leave_event_id: string
  to_assignment_id: number
  target_event_id: string | null
  client_clicked_at: string
  writer_lease_token?: string
  writer_client_instance_id?: string
  save_and_leave: AtomicSubmissionEnvelope
  next_entry?: NextEntryRequest
  inline_confidence?: { probability: number | null; answer_event_id: string | null }
}

export interface SavedNavigationResponse extends SavedNavigationConfirmation {
  success: boolean
  response_version: number
  saved_at: string | null
  status: string
  next_url: string
  next_entry?: NextEntryOutcome
}

export interface NextEntryRequest {
  client_instance_id?: string
  assignment_id: number
  reason: 'navigation_click' | 'resume'
  navigation_id: string
  client_occurred_at: string
  client_monotonic_ms: string
  client_time_origin: string
}

export type EnteredQuestion = StudentRuntimeQuestionEntry & { entry_receipt: EntryReceipt }
export type NextEntryOutcome = { committed: true; runtime: EnteredQuestion }
  | { committed: false; error: { code: string; message: string } }

export function nextEntryRequest(intent: NavigationClickContext & { assignmentId: number; reason: 'navigation_click' | 'resume' }): NextEntryRequest {
  return { assignment_id: intent.assignmentId, reason: intent.reason, navigation_id: intent.navigationId,
    client_occurred_at: intent.clientOccurredAt, client_monotonic_ms: intent.clientMonotonicMs.toFixed(3),
    client_time_origin: intent.clientTimeOrigin,
    ...(intent.clientInstanceId ? { client_instance_id: intent.clientInstanceId } : {}) }
}

export type StudentRuntimeQuestionEntry = components['schemas']['StudentRuntimeQuestionEntry'] & {
  assessment_flow_version?: number
  response_write_allowed?: boolean
  prepared_paper?: PreparedPaper
  initial_writer_lease?: WriterLeaseCredentials & { expires_at: string }
  inline_confidence?: { probability: number | null; answer_event_id: string | null }
  offline_recovery?: boolean
  research_tools?: StudentResearchToolConfiguration
  process: components['schemas']['StudentRuntimeProcess'] & {
    paper_flow_version?: number
    transition_entry?: { enabled: boolean; version: number }
    capture_bundle?: CaptureBundleCapability
    close_bundle?: CaptureBundleCapability
    atomic_submission?: { version: 1; max_events: number; max_checkpoints: number; max_bytes: number }
    save_and_leave?: { version: 1; max_events: number; max_checkpoints: number; max_bytes: number }
  }
}

export interface NavigationClickContext {
  clientInstanceId?: string
  sessionId?: number
  navigationId: string
  clientOccurredAt: string
  clientMonotonicMs: number
  clientTimeOrigin: string
}

export interface EntryReceipt {
  navigation_id: string
  session_id: number
  assignment_id: number
  attempt_id: number
  visit_id: number
  visit_index: number
  entry_event_id: string
  entered_at: string
}

export interface DraftMutationResponse extends ProcessConfirmation {
  response_version?: number
  active_response_event_id?: string | null
  success: boolean
  saved_at: string
  response_snapshot_id?: string
  content_hash?: string
  content_length?: number
  state_hash?: string
  response_state_after?: unknown
}

export interface SubmissionResponse {
  success: boolean
  status: string
  next_url: string
  confidence_required: boolean
  confidence?: Record<string, unknown>
  feedback_mode?: string
  is_correct?: boolean | null
  correct_answer?: unknown
  explanation?: string
  committed?: boolean
  acknowledgements?: Array<{ client_sequence_no: number; event_id?: string }>
  checkpoint_ids?: string[]
  final_response_event_id?: string | null
  response_version?: number
}

export interface SubmissionConfidence {
  status: 'committed' | 'skipped'
  probability?: number
  answer_event_id?: string | null
}

export interface RecoveryState {
  status: string
  code: string
  can_continue: boolean
  question_url: string | null
  submission_matches?: boolean
  result?: SubmissionResponse | null
}

export interface NavigationResponse {
  success: boolean
  next_url: string
}

interface StudentAssessmentClient {
  get<T>(path: string): Promise<T>
  mutate<T>(path: string, mutation: { method: 'POST'; body?: unknown; keepalive?: boolean }): Promise<T>
}

export function createStudentAssessmentApi(client: StudentAssessmentClient) {
  return {
    finishPaper: (url: string, body: PaperSubmissionIntent) => client.mutate<PaperSubmissionResponse>(url, { method: 'POST', body: { submission_token: body.submission_token, client_clicked_at: body.client_clicked_at, expected_versions: body.expected_versions } }),
    paperSubmissionStatus: (url: string) => client.get<PaperSubmissionResponse>(url),
    saveAndLeave: (url: string, body: SavedNavigationRequest) => client.mutate<SavedNavigationResponse>(url, { method: 'POST', body }),
    recoverQuestion: (sessionId: number, assignmentId: number) => client.get<RecoveryState>(`/api/v1/student/sessions/${sessionId}/questions/${assignmentId}/recovery/`),
    recover: (attemptId: number, token: string) => client.get<RecoveryState>(`/api/v1/student/attempts/${attemptId}/recovery/?submission_token=${encodeURIComponent(token)}`),
    question: (sessionId: number, assignmentId: number) => client.get<StudentRuntimeQuestionEntry>(`/api/v1/student/sessions/${sessionId}/questions/${assignmentId}/`),
    enterQuestion: (sessionId: number, assignmentId: number, reason: string, navigation: NavigationClickContext, taskId?: number) => {
      const saved = taskId ? readAdmissionCredentials(taskId) : currentAdmissionCredentials(sessionId)?.credentials
      const admission = saved?.session_id === sessionId
        ? { admission_token: saved.admission_token, admission_action: saved.admission_action } : {}
      return client.mutate<StudentRuntimeQuestionEntry & { entry_receipt: EntryReceipt }>(
      `/api/v1/student/sessions/${sessionId}/questions/${assignmentId}/`, { method: 'POST', body: {
        reason, navigation_id: navigation.navigationId, client_occurred_at: navigation.clientOccurredAt,
        client_monotonic_ms: navigation.clientMonotonicMs.toFixed(3), client_time_origin: navigation.clientTimeOrigin,
        ...(navigation.clientInstanceId ? { client_instance_id: navigation.clientInstanceId } : {}),
        ...admission,
      } })
    },
    saveDraft: (url: string, answer: unknown, clientOccurredAt: string, target?: { target_event_id: string | null; expected_version: number; capture_bundle?: CaptureBundle; capture_operation_id?: string; inline_confidence?: { probability: number | null; answer_event_id: string | null } } & Partial<WriterLeaseCredentials>) => (
      client.mutate<DraftMutationResponse>(url, {
        method: 'POST',
        body: { answer, client_occurred_at: clientOccurredAt, ...target },
      })
    ),
    post: <T = SubmissionResponse>(url: string, body: Record<string, unknown>, options: { keepalive?: boolean; timeoutMs?: number } = {}) => {
      const { timeoutMs, ...mutationOptions } = options
      return withTransportTimeout(signal => client.mutate<T>(url, {
        method: 'POST', body, ...mutationOptions, ...(signal ? { signal } : {}),
      }), timeoutMs)
    },
  }
}

let api: ReturnType<typeof createStudentAssessmentApi> | undefined

export function getStudentAssessmentApi(operationId?: string | null) {
  if (operationId !== undefined) return createStudentAssessmentApi(
    createApiClient({ getCsrfToken: () => getBootstrapContext().csrf_token, getOperationId: () => operationId || undefined }),
  )
  if (api) return api
  api = createStudentAssessmentApi(
    createApiClient({ getCsrfToken: () => getBootstrapContext().csrf_token, getOperationId: currentAssessmentOperationId }),
  )
  return api
}
