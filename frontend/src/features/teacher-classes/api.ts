import { createApiClient } from '@/api/client'
import type { components } from '@/api/generated/schema'
import { getBootstrapContext } from '@/app/bootstrap'

export type TeacherClassCollection = components['schemas']['TeacherClassCollection'] & {
  capabilities: { can_create: boolean }
}
export type TeacherClassCenter = components['schemas']['TeacherClassCenter']
export interface TeacherClassCreateInput {
  name: string
  academic_year: string
}

export interface TeacherClassCreateResponse {
  class_group: TeacherClassCollection['items'][number]
}

type ApiClient = ReturnType<typeof createApiClient>

interface ClassListQuery {
  page?: number
  query?: string
}

interface ClassCenterQuery {
  section?: 'overview' | 'students' | 'experiments'
  page?: number
  query?: string
  sort?: string
  status?: string
  experimentId?: number
}

function queryString(values: Record<string, string | number | undefined>) {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== '') params.set(key, String(value))
  }
  const encoded = params.toString()
  return encoded ? `?${encoded}` : ''
}

export function createTeacherClassApi(client: ApiClient) {
  return {
    list(courseId: number, query: ClassListQuery = {}) {
      const suffix = queryString({ page: query.page ?? 1, q: query.query })
      return client.get<TeacherClassCollection>(
        `/api/v1/teacher/courses/${courseId}/classes/${suffix}`,
      )
    },
    create(courseId: number, input: TeacherClassCreateInput) {
      return client.mutate<TeacherClassCreateResponse>(
        `/api/v1/teacher/courses/${courseId}/classes/`,
        { method: 'POST', body: input },
      )
    },
    center(courseId: number, classId: number, query: ClassCenterQuery = {}) {
      const suffix = queryString({
        page: query.page ?? 1,
        q: query.query,
        sort: query.sort,
        status: query.status,
        experiment_id: query.experimentId,
      })
      return client.get<TeacherClassCenter>(
        `/api/v1/teacher/courses/${courseId}/classes/${classId}/center/${suffix}`,
      )
    },
  }
}

export function getTeacherClassApi() {
  const bootstrap = getBootstrapContext()
  return createTeacherClassApi(
    createApiClient({ getCsrfToken: () => bootstrap.csrf_token }),
  )
}
