import { createApiClient } from '@/api/client'
import type { components } from '@/api/generated/schema'
import { getBootstrapContext } from '@/app/bootstrap'
import type { WriterLeaseCredentials } from './writer-lease'

export type StudentRuntimeQuestionEntry = components['schemas']['StudentRuntimeQuestionEntry'] & {
  offline_recovery?: boolean
  process: components['schemas']['StudentRuntimeProcess'] & {
    visit_id: number
    visit_index: number
    entry_event_id: string
    navigation_id: string
  }
}

export interface NavigationClickContext {
  navigationId: string
  clientOccurredAt: string
  clientMonotonicMs: number
  clientTimeOrigin: string
}

export interface DraftMutationResponse {
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
    recoverQuestion: (sessionId: number, assignmentId: number) => client.get<RecoveryState>(`/api/v1/student/sessions/${sessionId}/questions/${assignmentId}/recovery/`),
    recover: (attemptId: number, token: string) => client.get<RecoveryState>(`/api/v1/student/attempts/${attemptId}/recovery/?submission_token=${encodeURIComponent(token)}`),
    question: (sessionId: number, assignmentId: number, reason = 'direct_open', navigation?: NavigationClickContext) => {
      const query = new URLSearchParams({ reason })
      if (navigation) {
        query.set('navigation_id', navigation.navigationId)
        query.set('client_occurred_at', navigation.clientOccurredAt)
        query.set('client_monotonic_ms', navigation.clientMonotonicMs.toFixed(3))
        query.set('client_time_origin', navigation.clientTimeOrigin)
      }
      return client.get<StudentRuntimeQuestionEntry>(
        `/api/v1/student/sessions/${sessionId}/questions/${assignmentId}/?${query.toString()}`,
      )
    },
    saveDraft: (url: string, answer: unknown, clientOccurredAt: string, target?: { target_event_id: string | null; expected_version: number } & Partial<WriterLeaseCredentials>) => (
      client.mutate<DraftMutationResponse>(url, {
        method: 'POST',
        body: { answer, client_occurred_at: clientOccurredAt, ...target },
      })
    ),
    submit: (url: string, submissionToken: string, clientClickedAt: string, targetEventId?: string | null, lease?: WriterLeaseCredentials, confidence?: SubmissionConfidence) => (
      client.mutate<SubmissionResponse>(url, {
        method: 'POST',
        body: {
          submission_token: submissionToken,
          client_clicked_at: clientClickedAt,
          ...lease,
          ...(confidence ? { confidence } : {}),
          ...(targetEventId ? { target_event_id: targetEventId } : {}),
        },
      })
    ),
    skip: (url: string) => client.mutate<NavigationResponse>(url, { method: 'POST' }),
    post: <T = SubmissionResponse>(url: string, body: Record<string, unknown>, options: { keepalive?: boolean } = {}) => (
      client.mutate<T>(url, { method: 'POST', body, ...options })
    ),
  }
}

let api: ReturnType<typeof createStudentAssessmentApi> | undefined

export function getStudentAssessmentApi() {
  if (api) return api
  const bootstrap = getBootstrapContext()
  api = createStudentAssessmentApi(
    createApiClient({ getCsrfToken: () => bootstrap.csrf_token }),
  )
  return api
}
