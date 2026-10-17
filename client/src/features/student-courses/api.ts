import { ApiError, createApiClient } from '@/api/client'
import type { components } from '@/api/generated/schema'
import { clearBootstrapContext, getAuthenticationGeneration, getCsrfToken } from '@/app/bootstrap'
import { currentAssessmentOperationId } from '@/assessment-runtime/operation-timing'
import { markPreparedStartupPhase } from '@/assessment-runtime/prepared-paper'
import { assessmentClientInstanceId } from '@/assessment-runtime/session-controller'
import type { EnteredQuestion } from '@/assessment-runtime/api'
import { clearAdmissionCredentials, readAdmissionCredentials, writeAdmissionCredentials, clearStartIntent, readStartIntent, writeStartIntent, type AdmissionCredentials } from '@/assessment-runtime/admission'

export type StudentCourse = components['schemas']['StudentCourse']
export type StudentCourseCollection = components['schemas']['StudentCourseCollection']
export type StudentTask = components['schemas']['StudentTask']
export type StudentTaskCollection = components['schemas']['StudentTaskCollection']
export type StudentSessionEntry = components['schemas']['StudentSessionEntry'] & { runtime?: EnteredQuestion }
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
  status: 'queued' | 'admitted' | 'executing' | 'succeeded' | 'completed' | 'failed'
  admission_token: string
  poll_url: string
  retry_after_seconds: number
}

type StudentEntryResponse = StudentSessionEntry & Partial<AdmissionResponse> & {
  admission_action?: 'start' | 'resume'
}

export function createStudentCourseApi(client: StudentApiClient, responseRetryAfter: (path: string) => number = () => 0) {
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
    const pendingAdmission = readAdmissionCredentials(taskId)
    if (action === 'resume' && (readStartIntent(taskId) || (pendingAdmission?.admission_action === 'start' && pendingAdmission.session_id === undefined))) {
      action = 'start'
    }
    const requestKey = `${taskId}:${action}`
    const existingRequest = entryRequests.get(requestKey)
    if (existingRequest) return existingRequest
    const request = (async () => {
      const path = `/api/v1/student/tasks/${taskId}/${action}/`
      const body = (): AdmissionCredentials | undefined => {
        const saved = readAdmissionCredentials(taskId)
        return saved?.admission_action === action ? saved : undefined
      }
      const clientId = await assessmentClientInstanceId()
      const savedIntent = action === 'start' ? readStartIntent(taskId) : undefined
      const identity = savedIntent?.client_instance_id === clientId ? savedIntent : { client_instance_id: clientId, navigation_id: crypto.randomUUID(), client_occurred_at: new Date().toISOString(), client_monotonic_ms: performance.now().toFixed(3), client_time_origin: new Date(performance.timeOrigin).toISOString(), reason: 'start' }
      if (action === 'start') writeStartIntent(taskId, identity)
      const retryDeadline = Date.now() + 60000
      let retryIndex = 0
      const retryStart = async (seconds: number) => {
        const windowMs = Math.min(4000, 1000 * 2 ** Math.min(retryIndex++, 2))
        const delay = seconds * 1000 + Math.floor(Math.random() * windowMs)
        if (Date.now() + delay >= retryDeadline) throw new ApiError(429, { error: { code: 'START_BUSY', message: '启动入口仍繁忙，请稍后重试。' } })
        await new Promise(resolve => setTimeout(resolve, delay))
      }
      let admissionRefreshed = false
      let terminalFailure = false
      for (;;) {
        try {
          const savedCredentials = body()
          markPreparedStartupPhase('request')
          let response = await client.mutate<StudentEntryResponse>(path, { method: 'POST', body: { ...identity, ...savedCredentials } })
          if (response.status === 'executing' && !response.poll_url) {
            await retryStart(Math.max(responseRetryAfter(path), response.retry_after_seconds || 1))
            continue
          }
          while (response.status === 'queued' || response.status === 'executing' || (response.status === 'admitted' && !response.question_url)) {
            const credentials: AdmissionCredentials = { admission_token: response.admission_token!, admission_action: action }
            writeAdmissionCredentials(taskId, credentials)
            if (response.status === 'admitted') {
              markPreparedStartupPhase('grant')
              markPreparedStartupPhase('activation_start')
              response = await client.mutate<StudentEntryResponse>(path, { method: 'POST', body: { ...identity, ...credentials } })
              continue
            }
            await retryStart(Math.max(responseRetryAfter(response.poll_url || path), response.retry_after_seconds || 0))
            const status = await client.mutate<AdmissionResponse>(response.poll_url!, {
              method: 'POST', body: { admission_token: credentials.admission_token, action },
            })
            if (status.status === 'queued' || status.status === 'executing') { response = { ...response, ...status }; continue }
            if (status.status === 'failed') { terminalFailure = true; throw new Error('作业准备失败，请重新核验任务状态。') }
            if (status.status === 'admitted') { response = { ...response, ...status }; continue }
            response = await client.mutate<StudentEntryResponse>(path, { method: 'POST', body: { ...identity, ...credentials } })
          }
          if (response.status === 'failed') { terminalFailure = true; throw new Error('作业准备失败，请重新核验任务状态。') }
          if (response.admission_token) {
            writeAdmissionCredentials(taskId, {
              admission_token: response.admission_token,
              admission_action: response.admission_action || action,
              session_id: response.session_id,
            })
          }
          markPreparedStartupPhase('activation_response')
          if (action === 'start') clearStartIntent(taskId)
          return response
        } catch (error) {
          if (terminalFailure) throw error
          if (error instanceof ApiError && error.status === 429 && error.code === 'START_BUSY') {
            await retryStart(error.retryAfterSeconds ?? responseRetryAfter(path))
            continue
          }
          if (error instanceof ApiError && error.code === 'ADMISSION_TOKEN_INVALID' && !admissionRefreshed) {
            clearAdmissionCredentials(taskId)
            admissionRefreshed = true
            continue
          }
          if (error instanceof ApiError && (error.status < 500 || error.code.startsWith('ADMISSION_'))) throw error
          const recovered = await client.get<StudentSessionEntry & { can_continue: boolean; code: string }>(`/api/v1/student/tasks/${taskId}/recovery/`)
          if (recovered.can_continue && recovered.session_id && action === 'start' && !body()) {
            const receipt = await client.mutate<StudentSessionEntry>(path, { method: 'POST', body: identity })
            clearStartIntent(taskId)
            markPreparedStartupPhase('activation_response')
            return receipt
          }
          if (recovered.can_continue && recovered.question_url) return recovered
          if (recovered.code === 'SESSION_NOT_STARTED') throw new Error('服务器确认任务尚未开始，请重新点击开始作业。')
          if (recovered.code === 'DEADLINE_EXPIRED') throw new Error('作答时间已截止，无法继续作答。')
          throw new Error('任务已结束或关闭，请刷新任务列表查看结果。')
        }
      }
    })()
    entryRequests.set(requestKey, request)
    request.then(() => entryRequests.delete(requestKey), () => entryRequests.delete(requestKey))
    return request
  }
  return {
    courses: () => get<StudentCourseCollection>('/api/v1/student/courses/'),
    tasks: (courseId: number) => get<StudentTaskCollection>(`/api/v1/student/courses/${courseId}/tasks/`),
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
let apiGeneration: number | undefined

export function getStudentCourseApi() {
  const generation = getAuthenticationGeneration()
  if (api && apiGeneration === generation) return api
  apiGeneration = generation
  const retryAfter = new Map<string, number>()
  function assertCurrentAuthentication() {
    if (generation !== getAuthenticationGeneration()) throw new ApiError(409, { error: {
      code: 'AUTHENTICATION_CHANGED', message: '登录状态已变化，请重新加载课程。',
    } })
  }
  const client = createApiClient({ getCsrfToken: () => { assertCurrentAuthentication(); return getCsrfToken() },
    getOperationId: currentAssessmentOperationId,
    onAuthenticationRequired: loginUrl => {
      if (generation !== getAuthenticationGeneration()) return
      clearBootstrapContext()
      window.location.assign(loginUrl)
    }, onResponse: response => {
    if (!response.url) return
    const value = response.headers.get('Retry-After')
    const delay = value === null ? 0 : /^\d+$/.test(value) ? Number(value) : Math.max(0, (Date.parse(value) - Date.now()) / 1000)
    retryAfter.set(new URL(response.url, location.origin).pathname, Number.isFinite(delay) ? delay : 0)
  } })
  api = createStudentCourseApi({
    async get<T>(path: string, options?: { retry?: boolean }) {
      assertCurrentAuthentication()
      const response = await client.get<T>(path, options)
      assertCurrentAuthentication()
      return response
    },
    async mutate<T>(path: string, mutation: { method: 'POST'; body?: unknown }) {
      assertCurrentAuthentication()
      const response = await client.mutate<T>(path, mutation)
      assertCurrentAuthentication()
      return response
    },
  }, path => retryAfter.get(new URL(path, location.origin).pathname) || 0)
  return api
}
