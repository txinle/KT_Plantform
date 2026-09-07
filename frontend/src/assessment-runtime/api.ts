import { createApiClient } from '@/api/client'
import type { components } from '@/api/generated/schema'
import { getBootstrapContext } from '@/app/bootstrap'
import type { WriterLeaseCredentials } from './writer-lease'

export type StudentRuntimeQuestionEntry = components['schemas']['StudentRuntimeQuestionEntry'] & { offline_recovery?: boolean }

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

export interface NavigationResponse {
  success: boolean
  next_url: string
}

interface StudentAssessmentClient {
  get<T>(path: string): Promise<T>
  mutate<T>(path: string, mutation: { method: 'POST'; body?: unknown }): Promise<T>
}

export function createStudentAssessmentApi(client: StudentAssessmentClient) {
  return {
    question: (sessionId: number, assignmentId: number, reason = 'navigation_click') => (
      client.get<StudentRuntimeQuestionEntry>(
        `/api/v1/student/sessions/${sessionId}/questions/${assignmentId}/?reason=${encodeURIComponent(reason)}`,
      )
    ),
    saveDraft: (url: string, answer: unknown, clientOccurredAt: string, target?: { target_event_id: string | null; expected_version: number } & Partial<WriterLeaseCredentials>) => (
      client.mutate<DraftMutationResponse>(url, {
        method: 'POST',
        body: { answer, client_occurred_at: clientOccurredAt, ...target },
      })
    ),
    submit: (url: string, submissionToken: string, clientClickedAt: string, targetEventId?: string | null, lease?: WriterLeaseCredentials) => (
      client.mutate<SubmissionResponse>(url, {
        method: 'POST',
        body: {
          submission_token: submissionToken,
          client_clicked_at: clientClickedAt,
          ...lease,
          ...(targetEventId ? { target_event_id: targetEventId } : {}),
        },
      })
    ),
    skip: (url: string) => client.mutate<NavigationResponse>(url, { method: 'POST' }),
    post: <T = SubmissionResponse>(url: string, body: Record<string, unknown>) => (
      client.mutate<T>(url, { method: 'POST', body })
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
