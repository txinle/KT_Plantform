import { createApiClient } from '@/api/client'
import { getBootstrapContext } from '@/app/bootstrap'

export interface TeacherCourseCard {
  id: number
  name: string
  code: string
  term: string
  description: string
  owner_name: string
  status: string
  status_label: string
  class_count: number
  bank_count: number
  experiment_count: number
  detail_url: string
}

export interface TeacherCourse {
  id: number
  name: string
  code: string
  term: string
  description: string
  owner_name: string
  status: string
  status_label: string
  updated_at: string
  banks_url: string
  classes_url: string
  experiments_url: string
  data_url: string
  settings_url: string
  detail_url: string
}

export interface TeacherCourseSettingsCourse {
  id: number
  name: string
  code: string
  term: string
  description: string
  status: string
  status_label: string
}

export interface TeacherCourseInput {
  name: string
  code: string
  term: string
  description: string
}

export interface TeacherCourseSettingsInput extends TeacherCourseInput {
  state_token: string
  teachers: number[]
}

export interface TeacherCourseListResponse {
  items: TeacherCourseCard[]
  create_url: string
}

export interface TeacherCourseDetailResponse {
  course: TeacherCourse
  metrics: {
    class_count: number
    bank_count: number
    experiment_count: number
    student_count: number
  }
  recent_experiments: Array<{
    id: number
    name: string
    question_bank_name: string
    status: string
    status_label: string
    detail_url: string
  }>
  recent_banks: Array<{
    id: number
    name: string
    description: string
    question_count: number
    visibility: string
    visibility_label: string
    updated_at: string
    detail_url: string
  }>
  class_center_enabled: boolean
  is_course_owner: boolean
}

export interface TeacherCourseSettingsResponse {
  course: TeacherCourseSettingsCourse
  form: TeacherCourseSettingsInput & {
    teacher_options: Array<{ id: number; display_name: string; username: string }>
  }
  capabilities: {
    can_update: boolean
    can_archive: boolean
  }
  archive_url: string
  purge_request_create_url: string
  purge_requests: Array<{
    purge_request_id: string
    preview_session_count: number
    preview_event_count: number
    status: string
    status_label: string
    execute_url: string | null
  }>
}

interface TeacherCourseApiClient {
  get<T>(path: string): Promise<T>
  mutate<T>(path: string, mutation: { method: 'POST'; body: unknown }): Promise<T>
}

export function createTeacherCourseApi(client: TeacherCourseApiClient) {
  return {
    list: () => client.get<TeacherCourseListResponse>('/api/v1/teacher/courses/'),
    create: (input: TeacherCourseInput) => client.mutate<{ course: TeacherCourseCard }>('/api/v1/teacher/courses/', { method: 'POST', body: input }),
    detail: (courseId: number) => client.get<TeacherCourseDetailResponse>(`/api/v1/teacher/courses/${courseId}/`),
    settings: (courseId: number) => client.get<TeacherCourseSettingsResponse>(`/api/v1/teacher/courses/${courseId}/settings/`),
    saveSettings: (courseId: number, input: TeacherCourseSettingsInput) => client.mutate<TeacherCourseSettingsResponse>(`/api/v1/teacher/courses/${courseId}/settings/`, { method: 'POST', body: input }),
    archive: (courseId: number) => client.mutate<TeacherCourseSettingsResponse>(`/api/v1/teacher/courses/${courseId}/archive/`, { method: 'POST', body: { confirmation: 'archive' } }),
    createPurgeRequest: (courseId: number, reasonCode: string) => client.mutate<TeacherCourseSettingsResponse>(`/api/v1/teacher/courses/${courseId}/purge-requests/`, { method: 'POST', body: { confirmation: 'PURGE', reason_code: reasonCode } }),
    executePurgeRequest: (courseId: number, purgeRequestId: string) => client.mutate<TeacherCourseSettingsResponse>(`/api/v1/teacher/courses/${courseId}/purge-requests/${purgeRequestId}/execute/`, { method: 'POST', body: { confirmation: 'execute' } }),
  }
}

let api: ReturnType<typeof createTeacherCourseApi> | undefined

export function getTeacherCourseApi() {
  if (api) return api
  const bootstrap = getBootstrapContext()
  api = createTeacherCourseApi(createApiClient({ getCsrfToken: () => bootstrap.csrf_token }))
  return api
}
