import { createApiClient } from '@/api/client'
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
  }>
}

interface StudentApiClient {
  get<T>(path: string): Promise<T>
  mutate<T>(path: string, mutation: { method: 'POST' }): Promise<T>
}

export function createStudentCourseApi(client: StudentApiClient) {
  return {
    courses: () => client.get<StudentCourseCollection>('/api/v1/student/courses/'),
    tasks: (courseId: number) => client.get<StudentTaskCollection>(`/api/v1/student/courses/${courseId}/tasks/`),
    learning: (courseId: number) => client.get<StudentLearningSummary>(`/api/v1/student/courses/${courseId}/learning/`),
    learningExperiment: (courseId: number, experimentId: number) => client.get<StudentLearningExperiment>(`/api/v1/student/courses/${courseId}/learning/experiments/${experimentId}/`),
    learningQuestion: (courseId: number, experimentId: number, questionId: number) => client.get<StudentLearningQuestion>(`/api/v1/student/courses/${courseId}/learning/experiments/${experimentId}/questions/${questionId}/`),
    result: (taskId: number) => client.get<StudentResult>(`/api/v1/student/tasks/${taskId}/results/`),
    start: (taskId: number) => client.mutate<StudentSessionEntry>(`/api/v1/student/tasks/${taskId}/start/`, { method: 'POST' }),
    resume: (taskId: number) => client.mutate<StudentSessionEntry>(`/api/v1/student/tasks/${taskId}/resume/`, { method: 'POST' }),
  }
}

let api: ReturnType<typeof createStudentCourseApi> | undefined

export function getStudentCourseApi() {
  if (api) return api
  const bootstrap = getBootstrapContext()
  api = createStudentCourseApi(createApiClient({ getCsrfToken: () => bootstrap.csrf_token }))
  return api
}
