import { createApiClient } from '@/api/client'
import type { components } from '@/api/generated/schema'
import { getBootstrapContext } from '@/app/bootstrap'

export type TeacherExperiment = components['schemas']['TeacherExperiment']
export type TeacherExperimentCollection = components['schemas']['TeacherExperimentCollection']
export type TeacherExperimentReadiness = components['schemas']['TeacherExperimentReadiness']
export type ExperimentQuestionType = 'single_choice' | 'multiple_choice' | 'numeric' | 'short_answer' | 'fill_blank' | 'true_false'
export type SamplingInventoryInput = {
  question_bank_ids: number[]
  kc_codes: string[]
  match_scope: 'primary' | 'any_level'
}
export type SamplingInventory = {
  candidate_count: number
  kc_counts: Array<{
    kc_code: string
    primary_available: number
    any_level_available: number
  }>
  question_types: Array<{
    question_type: ExperimentQuestionType
    label: string
    available: number
  }>
  bank_counts?: Array<{ question_bank_id: number; name: string; candidate_count: number }>
}
export type ExperimentDefinitionInput = Omit<components['schemas']['PatchedTeacherExperiment'], 'sampling'> & {
  name: string
  question_bank_ids: number[]
  sampling?: {
    mode: 'total_count' | 'kc_quota'
    total_count?: number | null
    match_scope: 'primary' | 'any_level'
    nodes: Array<{
      kc_code: string
      quota?: number | null
      match_scope: 'primary' | 'any_level'
    }>
    type_quotas?: Array<{
      question_type: ExperimentQuestionType
      amount: number
    }>
  }
}
export type PublishPlan = components['schemas']['PublishPlan']
export type PublishPlanSetupInput = components['schemas']['PublishPlanSetupInput']
export type PublishPreviewSummary = components['schemas']['PublishPreviewSummary']
export type PublishPlanReview = components['schemas']['PublishPlanReview']
export type PublishOperationJob = components['schemas']['PublishOperationJob']
export type PublishParticipantCollection = components['schemas']['PublishParticipantCollection']
export type PublishParticipantDetail = components['schemas']['PublishParticipantDetail']
export type CollectionParticipantCollection = components['schemas']['CollectionParticipantCollection']
export type CollectionParticipantFilters = {
  classId?: number
  status?: string
  query?: string
  page?: number
}
export type ClassTaskCollection = components['schemas']['ClassTaskCollection']
export type ClassTask = components['schemas']['ClassTask']
export type ClassTaskProgress = components['schemas']['ClassTaskProgress']
export type ScheduleRevisionInput = components['schemas']['ScheduleRevisionInput']
export type CourseConceptCollection = components['schemas']['CourseConceptCollection']
export type GradingStatusFilter = 'pending' | 'completed' | 'all'
export type ManualGradingTotals = {
  enabled: boolean
  short_answer_questions: number
  submitted: number
  pending: number
  graded: number
}
export type ManualGradingQuestionSummary = {
  question_snapshot_id: number
  code: string
  text_summary: string
  submitted_count: number
  pending_count: number
  graded_count: number
}
export type ManualGradingSubmission = {
  submission_id: number
  student_id: number
  student_number: string
  display_name: string
  final_answer: unknown
  submitted_at: string
  grading_status: string
  latest_grade_version: number
  current_is_correct: boolean | null
  current_comment: string
  graded_at: string | null
  grader_name: string
}
export type ManualGradingOverview = {
  task: { id: number; experiment_id: number; experiment_name: string; class_id: number; class_name: string }
  totals: ManualGradingTotals
  questions: ManualGradingQuestionSummary[]
}
export type ManualGradingQuestionDetail = {
  question: {
    question_snapshot_id: number
    code: string
    text: string
    correct_answer: unknown
    accepted_answers: unknown[]
    explanation: string
  }
  submissions: {
    items: ManualGradingSubmission[]
    page: { number: number; size: number; total_items: number; total_pages: number; start: number; end: number }
  }
}
export type ManualGradeInput = { expected_grade_version: number; is_correct: boolean; comment: string }
export type ManualGradeResponse = {
  submission: ManualGradingSubmission
  question_progress: ManualGradingQuestionSummary
  task_progress: ManualGradingTotals
}

type ApiClient = ReturnType<typeof createApiClient>

export function createTeacherExperimentApi(client: ApiClient) {
  return {
    list(courseId: number, page = 1, archived = false) {
      return client.get<TeacherExperimentCollection>(
        `/api/v1/teacher/courses/${courseId}/experiments/?page=${page}${archived ? '&archived=1' : ''}`,
      )
    },
    archive(courseId: number, experimentId: number) {
      return client.mutate<TeacherExperiment>(
        `/api/v1/teacher/courses/${courseId}/experiments/${experimentId}/archive/`,
        { method: 'POST' },
      )
    },
    detail(courseId: number, experimentId: number) {
      return client.get<TeacherExperiment>(
        `/api/v1/teacher/courses/${courseId}/experiments/${experimentId}/`,
      )
    },
    concepts(courseId: number) {
      return client.get<CourseConceptCollection>(
        `/api/v1/teacher/courses/${courseId}/concepts/?all=1`,
      )
    },
    readiness(courseId: number, experimentId: number) {
      return client.get<TeacherExperimentReadiness>(
        `/api/v1/teacher/courses/${courseId}/experiments/${experimentId}/readiness/`,
      )
    },
    samplingInventory(courseId: number, input: SamplingInventoryInput) {
      return client.mutate<SamplingInventory>(
        `/api/v1/teacher/courses/${courseId}/experiments/sampling-inventory/`,
        { method: 'POST', body: input },
      )
    },
    create(courseId: number, input: ExperimentDefinitionInput) {
      return client.mutate<TeacherExperiment>(
        `/api/v1/teacher/courses/${courseId}/experiments/`,
        { method: 'POST', body: input },
      )
    },
    update(courseId: number, experimentId: number, input: ExperimentDefinitionInput) {
      return client.mutate<TeacherExperiment>(
        `/api/v1/teacher/courses/${courseId}/experiments/${experimentId}/`,
        { method: 'PATCH', body: input },
      )
    },
    startPlan(courseId: number, experimentId: number) {
      return client.mutate<PublishPlan>(
        `/api/v1/teacher/courses/${courseId}/experiments/${experimentId}/publish-plans/`,
        { method: 'POST' },
      )
    },
    plan(planId: number) {
      return client.get<PublishPlan>(`/api/v1/teacher/publish-plans/${planId}/`)
    },
    saveSetup(planId: number, input: PublishPlanSetupInput) {
      return client.mutate<PublishPlan>(`/api/v1/teacher/publish-plans/${planId}/setup/`, {
        method: 'PUT',
        body: input,
        refreshAuthoritativeState: () => this.plan(planId),
      })
    },
    preview(planId: number, expectedRevision: number, idempotencyKey?: string) {
      return client.mutate<PublishOperationJob>(
        `/api/v1/teacher/publish-plans/${planId}/preview/`,
        {
          method: 'POST',
          body: { expected_revision: expectedRevision },
          idempotencyKey,
          refreshAuthoritativeState: () => this.plan(planId),
        },
      )
    },
    operationJob(jobId: string) {
      return client.get<PublishOperationJob>(
        `/api/v1/teacher/publish-operation-jobs/${jobId}/`,
      )
    },
    review(planId: number) {
      return client.get<PublishPlanReview>(`/api/v1/teacher/publish-plans/${planId}/review/`)
    },
    commit(planId: number, expectedRevision: number, idempotencyKey?: string) {
      return client.mutate<PublishOperationJob>(`/api/v1/teacher/publish-plans/${planId}/commit/`, {
        method: 'POST',
        body: { expected_revision: expectedRevision },
        idempotencyKey,
        refreshAuthoritativeState: () => this.plan(planId),
      })
    },
    participants(planId: number, page = 1) {
      return client.get<PublishParticipantCollection>(
        `/api/v1/teacher/publish-plans/${planId}/participants/?page=${page}`,
      )
    },
    participant(planId: number, participantId: number) {
      return client.get<PublishParticipantDetail>(
        `/api/v1/teacher/publish-plans/${planId}/participants/${participantId}/`,
      )
    },
    participantExportUrl(planId: number) {
      return `/api/v1/teacher/publish-plans/${planId}/participants/export/`
    },
    collectionParticipants(experimentId: number, filters: CollectionParticipantFilters = {}) {
      const params = new URLSearchParams()
      if (filters.classId !== undefined) params.set('class_id', String(filters.classId))
      if (filters.status) params.set('status', filters.status)
      const query = filters.query?.trim()
      if (query) params.set('q', query)
      if (filters.page !== undefined && filters.page > 1) params.set('page', String(filters.page))
      const queryString = params.toString()
      return client.get<CollectionParticipantCollection>(
        `/api/v1/teacher/experiments/${experimentId}/collection-participants/${queryString ? `?${queryString}` : ''}`,
      )
    },
    classTasks(experimentId: number) {
      return client.get<ClassTaskCollection>(
        `/api/v1/teacher/experiments/${experimentId}/class-tasks/`,
      )
    },
    task(taskId: number) {
      return client.get<ClassTask>(`/api/v1/teacher/class-tasks/${taskId}/`)
    },
    progress(taskId: number) {
      return client.get<ClassTaskProgress>(`/api/v1/teacher/class-tasks/${taskId}/progress/`)
    },
    reviseSchedule(taskId: number, input: ScheduleRevisionInput) {
      return client.mutate<ClassTask>(
        `/api/v1/teacher/class-tasks/${taskId}/schedule-revisions/`,
        { method: 'POST', body: input, refreshAuthoritativeState: () => this.task(taskId) },
      )
    },
    cancel(taskId: number, reason: string) {
      return client.mutate<ClassTask>(`/api/v1/teacher/class-tasks/${taskId}/cancel/`, {
        method: 'POST',
        body: { reason },
        refreshAuthoritativeState: () => this.task(taskId),
      })
    },
    manualClose(taskId: number, reason: string) {
      return client.mutate<ClassTask>(`/api/v1/teacher/class-tasks/${taskId}/manual-close/`, {
        method: 'POST',
        body: { reason },
        refreshAuthoritativeState: () => this.task(taskId),
      })
    },
    releaseFeedback(taskId: number, reason?: string) {
      return client.mutate<ClassTask>(`/api/v1/teacher/class-tasks/${taskId}/feedback-release/`, {
        method: 'POST',
        body: reason ? { reason } : {},
        refreshAuthoritativeState: () => this.task(taskId),
      })
    },
    grading(taskId: number) {
      return client.get<ManualGradingOverview>(`/api/v1/teacher/class-tasks/${taskId}/grading/`)
    },
    gradingQuestion(taskId: number, snapshotId: number, status: GradingStatusFilter = 'pending', page = 1) {
      return client.get<ManualGradingQuestionDetail>(
        `/api/v1/teacher/class-tasks/${taskId}/grading/questions/${snapshotId}/?status=${status}&page=${page}`,
      )
    },
    gradeSubmission(taskId: number, snapshotId: number, submissionId: number, input: ManualGradeInput) {
      return client.mutate<ManualGradeResponse>(
        `/api/v1/teacher/class-tasks/${taskId}/grading/submissions/${submissionId}/`,
        {
          method: 'POST',
          body: input,
          refreshAuthoritativeState: () => this.gradingQuestion(taskId, snapshotId, 'all', 1),
        },
      )
    },
  }
}

export function getTeacherExperimentApi() {
  const bootstrap = getBootstrapContext()
  return createTeacherExperimentApi(
    createApiClient({ getCsrfToken: () => bootstrap.csrf_token }),
  )
}
