import { createApiClient } from '@/api/client'
import type { components } from '@/api/generated/schema'
import { getBootstrapContext } from '@/app/bootstrap'

type GeneratedDataCenterScope = components['schemas']['DataCenterScope']
type GeneratedDataCenterAdvanced = components['schemas']['DataCenterAdvanced']
type GeneratedDataCenterExportCatalog = components['schemas']['DataCenterExportCatalog']

export type DataCenterScope = Omit<GeneratedDataCenterScope, 'current'> & {
  current: GeneratedDataCenterScope['current'] & { experiment_ids: number[] }
}
export type DataCenterOverview = components['schemas']['DataCenterOverview']
export type DataCenterQualityCollection = components['schemas']['DataCenterQualityCollection']
export type DataCenterExportHistoryCollection = components['schemas']['DataCenterExportHistoryCollection']

export interface ResearchStudent {
  id: number
  student_number: string
  name: string
}

export interface ResearchStudentSummary extends ResearchStudent {
  class_name: string
  course_name: string
  experiment_count: number
}

export interface ResearchSession {
  session_ref: string
  student_id: number
  student_number: string
  name: string
  course_name: string
  experiment_name: string
  class_name: string
  status_label: string
  started_at: string
  ended_at: string
  submitted_at: string
  duration_minutes: number | null
  question_count: number
  behavior_count: number
}

export interface ResearchResponse {
  question_ref: string
  question_type: string
  assigned_order: number
  first_visit_order: number | null
  visit_count: number | null
  clicked_times: string[]
  entered_times: string[]
  left_times: string[]
  submitted_at: string
  final_answer: string
  status_label: string
  is_correct: boolean | null
}

export interface ResearchVisit {
  key: string
  question_ref: string
  visit_index: number
  sequence: number
  clicked_at: string
  entered_at: string
  left_at: string
  duration_seconds: number | null
  problem: string
}

export interface ResearchBehavior {
  question_ref: string
  visit_index: number | null
  sequence: number
  category: string
  action: string
  occurred_at: string
  before: string
  after: string
  details: string
}

export interface ResearchWorkspace {
  students: ResearchStudent[]
  student: ResearchStudentSummary | null
  sessions: ResearchSession[]
  session: ResearchSession | null
  responses: ResearchResponse[]
  visits: ResearchVisit[]
  behavior: ResearchBehavior[]
  question: ResearchResponse | null
  selected_visit: string | null
}

export type DataCenterAdvanced = GeneratedDataCenterAdvanced & {
  view: 'overview' | 'student' | 'session' | 'question' | 'timeline'
  workspace: ResearchWorkspace
}

export type DataCenterExportTable = GeneratedDataCenterAdvanced['tables'][number] & {
  level: 'course' | 'experiment' | 'dictionary'
  format: 'csv' | 'json'
}

export type DataCenterExportCatalog = Omit<GeneratedDataCenterExportCatalog, 'tables'> & {
  tables: DataCenterExportTable[]
}

export interface DataCenterExportJobMutation {
  job_id: string
  package: string
  status: string
  status_label: string
  download_url: string | null
  history_url: string
}

type ApiClient = ReturnType<typeof createApiClient>
type QueryValue = string | number | null | undefined | (string | null)[]
export type DataQuery = Record<string, QueryValue>

export function routeQuery(query: Record<string, unknown>): DataQuery {
  const result: DataQuery = {}
  for (const [key, value] of Object.entries(query)) {
    if (Array.isArray(value)) result[key] = value[0] as string | null | undefined
    else if (typeof value === 'string' || typeof value === 'number' || value == null) result[key] = value
  }
  return result
}

export function queryString(input: DataQuery) {
  const params = new URLSearchParams()
  for (const [key, rawValue] of Object.entries(input)) {
    const value = Array.isArray(rawValue) ? rawValue[0] : rawValue
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value))
  }
  const query = params.toString()
  return query ? `?${query}` : ''
}

export function createTeacherDataCenterApi(client: ApiClient) {
  return {
    scope(courseId: number, query: DataQuery = {}) {
      return client.get<DataCenterScope>(`/api/v1/teacher/courses/${courseId}/data/scope/${queryString(query)}`)
    },
    updateScope(courseId: number, input: { experiment_ids: number[]; class_id: number | null }) {
      return client.mutate<DataCenterScope>(`/api/v1/teacher/courses/${courseId}/data/scope/`, {
        method: 'POST',
        body: input,
      })
    },
    overview(courseId: number, query: DataQuery = {}) {
      return client.get<DataCenterOverview>(`/api/v1/teacher/courses/${courseId}/data/overview/${queryString(query)}`)
    },
    exports(courseId: number, query: DataQuery = {}) {
      return client.get<DataCenterExportCatalog>(`/api/v1/teacher/courses/${courseId}/data/exports/${queryString(query)}`)
    },
    quality(courseId: number, query: DataQuery = {}) {
      return client.get<DataCenterQualityCollection>(`/api/v1/teacher/courses/${courseId}/data/quality/${queryString(query)}`)
    },
    history(courseId: number, query: DataQuery = {}) {
      return client.get<DataCenterExportHistoryCollection>(`/api/v1/teacher/courses/${courseId}/data/exports/history/${queryString(query)}`)
    },
    advanced(courseId: number, query: DataQuery = {}) {
      return client.get<DataCenterAdvanced>(`/api/v1/teacher/courses/${courseId}/data/advanced/${queryString(query)}`)
    },
    createExport(courseId: number, input: { package: string; confirm_identified: true; scope: Record<string, unknown> }) {
      return client.mutate<DataCenterExportJobMutation>(`/api/v1/teacher/courses/${courseId}/data/export-jobs/`, {
        method: 'POST',
        body: input,
      })
    },
    removeExport(url: string) {
      return client.mutate<DataCenterExportJobMutation>(url, { method: 'DELETE' })
    },
  }
}

export function getTeacherDataCenterApi() {
  const bootstrap = getBootstrapContext()
  return createTeacherDataCenterApi(createApiClient({
    getCsrfToken: () => bootstrap.csrf_token,
  }))
}
