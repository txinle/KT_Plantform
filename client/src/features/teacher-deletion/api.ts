import { createApiClient } from '@/api/client'
import { getBootstrapContext } from '@/app/bootstrap'

export type DeletionAction = 'delete_experiment' | 'remove_class_experiment' | 'delete_class'

export interface DeletionExperimentImpact {
  id: number
  name: string
  class_count: number
  session_count: number
  class_session_count: number
  export_count: number
  classes: Array<{ id: number; name: string; session_count: number }>
  detail_url: string
}

export interface DeletionPreview {
  action: DeletionAction
  object_id: number
  object_name: string
  allowed: boolean
  blocked_reason: string
  experiments: DeletionExperimentImpact[]
  counts: {
    class_count: number
    session_count: number
    export_count: number
    member_count: number
    coverage_run_count?: number
    coverage_session_count?: number
  }
  confirmation_token: string
}

export interface DeletionResult {
  deleted: boolean
  cleanup_pending: boolean
  cleanup_id: string | null
  operation_id: string
}

export interface DeletionOperationInput {
  operation_id: string
  action: DeletionAction
  object_id: number
  class_id: number | null
  confirmation_token: string
}

export interface DeletionOperation {
  operation_id: string
  state: 'pending' | 'succeeded' | 'rejected'
  execute_before: string
  result: DeletionResult | null
  error_code: string
  message: string
}

export interface DeletionCleanupStatus {
  cleanup_pending: boolean
  status: 'pending' | 'completed'
  error: string
  state: 'pending' | 'running' | 'completed' | 'failed'
  attempts: number
  next_retry_at: string | null
  retained_count: number
}

export type DeletionTarget =
  | { kind: 'experiment'; experimentId: number }
  | { kind: 'class_experiment'; classId: number; experimentId: number }
  | { kind: 'class'; classId: number }

type ApiClient = ReturnType<typeof createApiClient>

export function createTeacherDeletionApi(client: ApiClient) {
  return {
    previewExperiment(courseId: number, experimentId: number, signal?: AbortSignal) {
      return client.get<DeletionPreview>(`/api/v1/teacher/courses/${courseId}/experiments/${experimentId}/deletion/`, { signal, retry: false })
    },
    deleteExperiment(courseId: number, experimentId: number, confirmationToken: string, operationId: string) {
      return client.mutate<DeletionResult>(`/api/v1/teacher/courses/${courseId}/experiments/${experimentId}/deletion/`, {
        method: 'POST', body: { confirmation: 'delete', confirmation_token: confirmationToken, operation_id: operationId },
      })
    },
    previewClassExperiment(courseId: number, classId: number, experimentId: number, signal?: AbortSignal) {
      return client.get<DeletionPreview>(`/api/v1/teacher/courses/${courseId}/classes/${classId}/experiments/${experimentId}/remove/`, { signal, retry: false })
    },
    removeClassExperiment(courseId: number, classId: number, experimentId: number, confirmationToken: string, operationId: string) {
      return client.mutate<DeletionResult>(`/api/v1/teacher/courses/${courseId}/classes/${classId}/experiments/${experimentId}/remove/`, {
        method: 'POST', body: { confirmation: 'delete', confirmation_token: confirmationToken, operation_id: operationId },
      })
    },
    previewClass(courseId: number, classId: number, signal?: AbortSignal) {
      return client.get<DeletionPreview>(`/api/v1/teacher/accounts/courses/${courseId}/classes/${classId}/delete/`, { signal, retry: false })
    },
    deleteClass(courseId: number, classId: number, confirmationToken: string, operationId: string) {
      return client.mutate<DeletionResult>(`/api/v1/teacher/accounts/courses/${courseId}/classes/${classId}/delete/`, {
        method: 'POST', body: { confirmation: 'delete', confirmation_token: confirmationToken, operation_id: operationId },
      })
    },
    registerOperation(courseId: number, input: DeletionOperationInput) {
      return client.mutate<DeletionOperation>(`/api/v1/teacher/courses/${courseId}/deletion-operations/`, { method: 'POST', body: input })
    },
    operation(courseId: number, operationId: string, signal?: AbortSignal) {
      return client.get<DeletionOperation>(`/api/v1/teacher/courses/${courseId}/deletion-operations/${encodeURIComponent(operationId)}/`, { signal, retry: false })
    },
    cleanup(courseId: number, cleanupId: string, signal?: AbortSignal) {
      return client.get<DeletionCleanupStatus>(`/api/v1/teacher/courses/${courseId}/deletion-cleanups/${encodeURIComponent(cleanupId)}/`, { signal, retry: false })
    },
  }
}

export function getTeacherDeletionApi() {
  const bootstrap = getBootstrapContext()
  return createTeacherDeletionApi(createApiClient({ getCsrfToken: () => bootstrap.csrf_token }))
}
