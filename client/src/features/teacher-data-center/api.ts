import { createApiClient } from '@/api/client'
import type { components } from '@/api/generated/schema'
import { getBootstrapContext } from '@/app/bootstrap'

type GeneratedDataCenterScope = components['schemas']['DataCenterScope']
type GeneratedDataCenterAdvanced = components['schemas']['DataCenterAdvanced']
type GeneratedDataCenterExportCatalog = components['schemas']['DataCenterExportCatalog']
type GeneratedDataCenterQualityCollection = components['schemas']['DataCenterQualityCollection']

export type DataCenterScope = Omit<GeneratedDataCenterScope, 'current'> & {
  current: GeneratedDataCenterScope['current'] & { experiment_ids: number[] }
}
export type DataCenterOverview = components['schemas']['DataCenterOverview']
export type DataCenterQualityCollection = GeneratedDataCenterQualityCollection & {
  preparation?: ResearchPreparation
}
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

export interface ResearchPreparation {
  retry_after_seconds?: number
  state: 'ready' | 'preparing' | 'updating' | 'failed'
  message: string
  updated_at: string | null
  result_version: string | null
  projection_version: string
  stale: boolean
  ready_count: number
  pending_count: number
  failed_count: number
  updating_count: number
  oldest_pending_seconds: number | null
}

export interface ResearchBehaviorCursor {
  previous: string | null
  next: string | null
  result_version: string
}

export interface ResearchStudentSearch {
  items: ResearchStudent[]
  limit: number
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
  preparation?: ResearchPreparation
  preview: (GeneratedDataCenterAdvanced['preview'] & { cursor?: ResearchBehaviorCursor }) | null
}

export type DataCenterExportTable = GeneratedDataCenterAdvanced['tables'][number] & {
  level: 'course' | 'experiment_binding' | 'experiment' | 'dictionary'
  format: 'csv' | 'json'
}

export type DataCenterExportCatalog = Omit<GeneratedDataCenterExportCatalog, 'tables'> & {
  tables: DataCenterExportTable[]
  experiments: { id: number; name: string }[]
  class_choices: { id: number; name: string; academic_year: string }[]
  preparation: { state: string; ready_count: number; pending_count: number }
}

export interface DataCenterExportJobMutation {
  retry_after_seconds?: number
  reused?: boolean
  source_version?: string
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
    overview(courseId: number, query: DataQuery = {}, signal?: AbortSignal) {
      return client.get<DataCenterOverview>(`/api/v1/teacher/courses/${courseId}/data/overview/${queryString(query)}`, { signal })
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
    advanced(courseId: number, query: DataQuery = {}, signal?: AbortSignal) {
      return client.get<DataCenterAdvanced>(`/api/v1/teacher/courses/${courseId}/data/advanced/${queryString(query)}`, { signal })
    },
    advancedStatus(courseId: number, query: DataQuery = {}, signal?: AbortSignal) {
      return client.get<{ preparation: ResearchPreparation }>(`/api/v1/teacher/courses/${courseId}/data/advanced/status/${queryString(query)}`, { signal, retry: false })
    },
    advancedStudents(courseId: number, query: DataQuery = {}, signal?: AbortSignal) {
      return client.get<ResearchStudentSearch>(`/api/v1/teacher/courses/${courseId}/data/advanced/students/${queryString(query)}`, { signal })
    },
    createExport(courseId: number, input: { package: string; confirm_identified: true; scope: Record<string, unknown> }) {
      return client.mutate<DataCenterExportJobMutation>(`/api/v1/teacher/courses/${courseId}/data/export-jobs/`, {
        method: 'POST',
        body: input,
      })
    },
    exportJob(courseId: number, jobId: string, signal?: AbortSignal) {
      return client.get<DataCenterExportJobMutation>(`/api/v1/teacher/courses/${courseId}/data/export-jobs/${jobId}/`, { signal, retry: false })
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
