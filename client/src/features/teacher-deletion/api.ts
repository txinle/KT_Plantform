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
  }
  confirmation_token: string
}

export interface DeletionResult {
  deleted: boolean
  cleanup_pending: boolean
  cleanup_id: string | null
}

export interface DeletionCleanupStatus {
  cleanup_pending: boolean
  status: 'pending' | 'completed'
  error: string
}

export type DeletionTarget =
  | { kind: 'experiment'; experimentId: number }
  | { kind: 'class_experiment'; classId: number; experimentId: number }
  | { kind: 'class'; classId: number }

type ApiClient = ReturnType<typeof createApiClient>

export function createTeacherDeletionApi(client: ApiClient) {
  return {
    previewExperiment(courseId: number, experimentId: number) {
      return client.get<DeletionPreview>(`/api/v1/teacher/courses/${courseId}/experiments/${experimentId}/deletion/`)
    },
    deleteExperiment(courseId: number, experimentId: number, confirmationToken: string) {
      return client.mutate<DeletionResult>(`/api/v1/teacher/courses/${courseId}/experiments/${experimentId}/deletion/`, {
        method: 'POST', body: { confirmation: 'delete', confirmation_token: confirmationToken },
      })
    },
    previewClassExperiment(courseId: number, classId: number, experimentId: number) {
      return client.get<DeletionPreview>(`/api/v1/teacher/courses/${courseId}/classes/${classId}/experiments/${experimentId}/remove/`)
    },
    removeClassExperiment(courseId: number, classId: number, experimentId: number, confirmationToken: string) {
      return client.mutate<DeletionResult>(`/api/v1/teacher/courses/${courseId}/classes/${classId}/experiments/${experimentId}/remove/`, {
        method: 'POST', body: { confirmation: 'delete', confirmation_token: confirmationToken },
      })
    },
    previewClass(courseId: number, classId: number) {
      return client.get<DeletionPreview>(`/api/v1/teacher/accounts/courses/${courseId}/classes/${classId}/delete/`)
    },
    deleteClass(courseId: number, classId: number, confirmationToken: string) {
      return client.mutate<DeletionResult>(`/api/v1/teacher/accounts/courses/${courseId}/classes/${classId}/delete/`, {
        method: 'POST', body: { confirmation: 'delete', confirmation_token: confirmationToken },
      })
    },
    cleanup(courseId: number, cleanupId: string) {
      return client.get<DeletionCleanupStatus>(`/api/v1/teacher/courses/${courseId}/deletion-cleanups/${encodeURIComponent(cleanupId)}/`)
    },
  }
}

export function getTeacherDeletionApi() {
  const bootstrap = getBootstrapContext()
  return createTeacherDeletionApi(createApiClient({ getCsrfToken: () => bootstrap.csrf_token }))
}
