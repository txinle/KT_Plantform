import { ApiError, createApiClient } from '@/api/client'
import type { components } from '@/api/generated/schema'
import { getBootstrapContext } from '@/app/bootstrap'
import { readAdmissionCredentials, writeAdmissionCredentials, type AdmissionCredentials } from '@/assessment-runtime/admission'

export type StudentCourse = components['schemas']['StudentCourse']
export type StudentCourseCollection = components['schemas']['StudentCourseCollection']
export type StudentTask = components['schemas']['StudentTask']
export type StudentTaskCollection = components['schemas']['StudentTaskCollection']
export type StudentSessionEntry = components['schemas']['StudentSessionEntry']
export type StudentResult = components['schemas']['StudentResult']
export type StudentLearningExperiment = components['schemas']['StudentLearningExperiment'] & { version?: string }
export type StudentLearningQuestion = components['schemas']['StudentLearningQuestion']

export interface MetricDisplay {
  percent_display?: string
  value_display?: string
}

export interface StudentLearningComparison {
  student_value?: MetricDisplay
  class_average?: MetricDisplay
}

export interface StudentLearningCoverage {
  graded?: number
  submitted?: number
  is_partial?: boolean
}

export interface StudentLearningKCRow {
  name: string
  student_value: MetricDisplay
  class_average?: MetricDisplay
}

export interface StudentLearningTrendRow {
  experiment_id: number
  name: string
  student_value: MetricDisplay
  class_average?: MetricDisplay
}

export interface StudentLearningSummary {
  version?: string
  comparison_status?: 'pending' | 'updating' | 'ready' | 'failed'
  comparison_generated_at?: string
  generated_at: string
  course_id: number
  course_name: string
  course_code: string
  course_term: string
  class_group_name: string
  completion: {
    completed?: number
    eligible?: number
    rate?: MetricDisplay
  }
  correct_rate?: MetricDisplay
  score_rate?: MetricDisplay
  score_grading_coverage?: StudentLearningCoverage
  comparisons?: {
    correct_rate?: StudentLearningComparison
    score_rate?: StudentLearningComparison
  }
  experiments: Array<{
    experiment_id: number
    name: string
    status?: string
    status_label: string
    completed?: boolean
    participated?: boolean
    results_available: boolean
    correct_rate?: MetricDisplay
    score_rate?: MetricDisplay
    grading_coverage?: StudentLearningCoverage
  }>
  kc_rows?: StudentLearningKCRow[]
  trend_rows?: StudentLearningTrendRow[]
}

export interface StudentProjectionStatus {
  version?: string
  comparison_status?: 'pending' | 'updating' | 'ready' | 'failed'
  comparison_generated_at?: string
  completed_at?: string | null
}

interface StudentApiClient {
  get<T>(path: string, options?: { retry?: boolean }): Promise<T>
  mutate<T>(path: string, mutation: { method: 'POST'; body?: unknown }): Promise<T>
}

interface AdmissionResponse {
  status: 'queued' | 'admitted'
  admission_token: string
  poll_url: string
  retry_after_seconds: number
}

type StudentEntryResponse = StudentSessionEntry & Partial<AdmissionResponse> & {
  admission_action?: 'start' | 'resume'
}

export function createStudentCourseApi(client: StudentApiClient) {
  const requests = new Map<string, Promise<unknown>>()
  const entryRequests = new Map<string, Promise<StudentSessionEntry>>()
  function get<T>(path: string, options?: { retry?: boolean }): Promise<T> {
    const existing = requests.get(path)
    if (existing) return existing as Promise<T>
    const request = options ? client.get<T>(path, options) : client.get<T>(path)
    requests.set(path, request)
    const clear = () => {
      if (requests.get(path) === request) requests.delete(path)
    }
    request.then(clear, clear)
    return request
  }
  function learningExperiment(courseId: number, experimentId: number) {
    return get<StudentLearningExperiment>(`/api/v1/student/courses/${courseId}/learning/experiments/${experimentId}/`)
  }

  async function enterTask(taskId: number, action: 'start' | 'resume'): Promise<StudentSessionEntry> {
    const requestKey = `${taskId}:${action}`
    const existingRequest = entryRequests.get(requestKey)
    if (existingRequest) return existingRequest
    const request = (async () => {
      const path = `/api/v1/student/tasks/${taskId}/${action}/`
      const body = (): AdmissionCredentials | undefined => {
        const saved = readAdmissionCredentials(taskId)
        return saved?.admission_action === action ? saved : undefined
      }
      try {
        const savedCredentials = body()
        let response = await client.mutate<StudentEntryResponse>(path, savedCredentials ? { method: 'POST', body: savedCredentials } : { method: 'POST' })
        while (response.status === 'queued') {
          const credentials: AdmissionCredentials = { admission_token: response.admission_token!, admission_action: action }
          writeAdmissionCredentials(taskId, credentials)
          const delay = Math.max(1, response.retry_after_seconds || 3) * 1000
          await new Promise(resolve => setTimeout(resolve, delay + Math.floor(Math.random() * 500)))
          const status = await client.mutate<AdmissionResponse>(response.poll_url!, {
            method: 'POST', body: { admission_token: credentials.admission_token, action },
          })
          if (status.status === 'queued') { response = { ...response, ...status }; continue }
          response = await client.mutate<StudentEntryResponse>(path, { method: 'POST', body: credentials })
        }
        if (response.admission_token) {
          writeAdmissionCredentials(taskId, {
            admission_token: response.admission_token,
            admission_action: response.admission_action || action,
            session_id: response.session_id,
          })
        }
        return response
      } catch (error) {
        if (error instanceof ApiError && (error.status < 500 || error.code.startsWith('ADMISSION_'))) throw error
        const recovered = await client.get<StudentSessionEntry & { can_continue: boolean; code: string }>(`/api/v1/student/tasks/${taskId}/recovery/`)
        if (recovered.can_continue && recovered.question_url) return recovered
        if (recovered.code === 'SESSION_NOT_STARTED') throw new Error('服务器确认任务尚未开始，请重新点击开始作业。')
        if (recovered.code === 'DEADLINE_EXPIRED') throw new Error('作答时间已截止，无法继续作答。')
        throw new Error('任务已结束或关闭，请刷新任务列表查看结果。')
      }
    })()
    entryRequests.set(requestKey, request)
    request.then(() => entryRequests.delete(requestKey), () => entryRequests.delete(requestKey))
    return request
  }
  return {
    courses: () => client.get<StudentCourseCollection>('/api/v1/student/courses/'),
    tasks: (courseId: number) => client.get<StudentTaskCollection>(`/api/v1/student/courses/${courseId}/tasks/`),
    learning: (courseId: number) => get<StudentLearningSummary>(`/api/v1/student/courses/${courseId}/learning/`),
    learningStatus: (courseId: number) => get<StudentProjectionStatus>(`/api/v1/student/courses/${courseId}/learning/?status_only=1`, { retry: false }),
    learningExperiment,
    learningExperimentStatus: (courseId: number, experimentId: number) => get<StudentProjectionStatus>(`/api/v1/student/courses/${courseId}/learning/experiments/${experimentId}/?status_only=1`, { retry: false }),
    learningQuestion: (courseId: number, experimentId: number, questionId: number) => get<StudentLearningQuestion>(`/api/v1/student/courses/${courseId}/learning/experiments/${experimentId}/questions/${questionId}/`),
    result: (taskId: number) => get<StudentResult>(`/api/v1/student/tasks/${taskId}/results/`),
    start: (taskId: number) => enterTask(taskId, 'start'),
    resume: (taskId: number) => enterTask(taskId, 'resume'),
  }
}

let api: ReturnType<typeof createStudentCourseApi> | undefined

export function getStudentCourseApi() {
  if (api) return api
  const bootstrap = getBootstrapContext()
  api = createStudentCourseApi(createApiClient({ getCsrfToken: () => bootstrap.csrf_token }))
  return api
}
