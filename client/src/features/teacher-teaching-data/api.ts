import { createApiClient } from '@/api/client'
import { getBootstrapContext } from '@/app/bootstrap'

type ApiClient = Pick<ReturnType<typeof createApiClient>, 'get'>
type QueryValue = string | number | null | undefined | (string | null)[]
export type TeachingDataQuery = Record<string, QueryValue>

export type TeachingDataResponse = {
  course: { id: number; name: string; code: string; term: string; status: string }
  scope: {
    values: Record<string, number>
    experiment_id: number | null
    class_id: number | null
    is_complete: boolean
    display: string
  }
  blocked_message: string
  filters: { q: string; student_id: number | null; session_id: number | null }
  total_roster_count: number
  student_summaries: Array<{
    student_id: number
    student_number: string
    display_name: string
    data_status: string
    data_status_label: string
    attempt_count: number | null
    submitted_count: number | null
    graded_count: number | null
    correct_rate: number | null
    score: string | null
    max_score: string | null
  }>
  sessions: Array<{
    id: number
    status: string
    status_label: string
    attempt_count: number
    submitted_count: number
    started_at: string
  }>
  attempts: Array<{
    id: number
    question_code: string
    status: string
    status_label: string
    grade_score: string | null
    timing: { display_duration_ms: number | null; display_label: string }
    timeline: Array<{
      event_id: string
      event_type: string
      occurred_at: string
      interval_quality: string
    }>
    raw_events_url: string
  }>
}

export function routeQuery(query: Record<string, unknown>): TeachingDataQuery {
  const result: TeachingDataQuery = {}
  for (const [key, value] of Object.entries(query)) {
    if (Array.isArray(value)) result[key] = value[0] as string | null | undefined
    else if (typeof value === 'string' || typeof value === 'number' || value == null) result[key] = value
  }
  return result
}

export function queryString(input: TeachingDataQuery) {
  const params = new URLSearchParams()
  for (const [key, rawValue] of Object.entries(input)) {
    const value = Array.isArray(rawValue) ? rawValue[0] : rawValue
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value))
  }
  const query = params.toString()
  return query ? `?${query}` : ''
}

export function createTeacherTeachingDataApi(client: ApiClient) {
  return {
    teaching(courseId: number, query: TeachingDataQuery = {}) {
      return client.get<TeachingDataResponse>(
        `/api/v1/teacher/courses/${courseId}/data/teaching/${queryString(query)}`,
      )
    },
  }
}

export function getTeacherTeachingDataApi() {
  const bootstrap = getBootstrapContext()
  return createTeacherTeachingDataApi(createApiClient({
    getCsrfToken: () => bootstrap.csrf_token,
  }))
}
