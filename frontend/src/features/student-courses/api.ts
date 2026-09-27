import { ApiError, createApiClient } from '@/api/client'
import type { components } from '@/api/generated/schema'
import { getBootstrapContext } from '@/app/bootstrap'

export type StudentCourse = components['schemas']['StudentCourse']
export type StudentCourseCollection = components['schemas']['StudentCourseCollection']
export type StudentTask = components['schemas']['StudentTask']
export type StudentTaskCollection = components['schemas']['StudentTaskCollection']
export type StudentSessionEntry = components['schemas']['StudentSessionEntry']
export type StudentResult = components['schemas']['StudentResult']
export type StudentLearningExperiment = components['schemas']['StudentLearningExperiment']
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

interface StudentApiClient {
  get<T>(path: string): Promise<T>
  mutate<T>(path: string, mutation: { method: 'POST' }): Promise<T>
}

export function createStudentCourseApi(client: StudentApiClient) {
  const learningExperimentRequests = new Map<string, Promise<StudentLearningExperiment>>()

  function learningExperiment(courseId: number, experimentId: number) {
    const key = `${courseId}:${experimentId}`
    const existing = learningExperimentRequests.get(key)
    if (existing) return existing
    const request = client.get<StudentLearningExperiment>(`/api/v1/student/courses/${courseId}/learning/experiments/${experimentId}/`)
    learningExperimentRequests.set(key, request)
    const clear = () => {
      if (learningExperimentRequests.get(key) === request) learningExperimentRequests.delete(key)
    }
    request.then(clear, clear)
    return request
  }

  async function enterTask(taskId: number, action: 'start' | 'resume'): Promise<StudentSessionEntry> {
    try { return await client.mutate<StudentSessionEntry>(`/api/v1/student/tasks/${taskId}/${action}/`, { method: 'POST' }) }
    catch (error) {
      if (error instanceof ApiError && error.status < 500) throw error
      const recovered = await client.get<StudentSessionEntry & { can_continue: boolean; code: string }>(`/api/v1/student/tasks/${taskId}/recovery/`)
      if (recovered.can_continue && recovered.question_url) return recovered
      if (recovered.code === 'SESSION_NOT_STARTED') throw new Error('服务器确认任务尚未开始，请重新点击开始作业。')
      if (recovered.code === 'DEADLINE_EXPIRED') throw new Error('作答时间已截止，无法继续作答。')
      throw new Error('任务已结束或关闭，请刷新任务列表查看结果。')
    }
  }
  return {
    courses: () => client.get<StudentCourseCollection>('/api/v1/student/courses/'),
    tasks: (courseId: number) => client.get<StudentTaskCollection>(`/api/v1/student/courses/${courseId}/tasks/`),
    learning: (courseId: number) => client.get<StudentLearningSummary>(`/api/v1/student/courses/${courseId}/learning/`),
    learningExperiment,
    learningQuestion: (courseId: number, experimentId: number, questionId: number) => client.get<StudentLearningQuestion>(`/api/v1/student/courses/${courseId}/learning/experiments/${experimentId}/questions/${questionId}/`),
    result: (taskId: number) => client.get<StudentResult>(`/api/v1/student/tasks/${taskId}/results/`),
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
