import { createApiClient } from '@/api/client'
import type { components } from '@/api/generated/schema'
import { getBootstrapContext } from '@/app/bootstrap'

export type TeacherExperiment = components['schemas']['TeacherExperiment']
export type TeacherExperimentCollection = components['schemas']['TeacherExperimentCollection']
export type TeacherExperimentReadiness = components['schemas']['TeacherExperimentReadiness']
export type ExperimentQuestionType = 'single_choice' | 'multiple_choice' | 'numeric' | 'short_answer' | 'fill_blank' | 'true_false'
export type SamplingInventoryInput = {
  question_bank_id: number
  kc_codes: string[]
  match_scope: 'primary' | 'any_level'
}
export type SamplingInventory = {
  candidate_count: number
  question_types: Array<{
    question_type: ExperimentQuestionType
    label: string
    available: number
  }>
}
export type ExperimentDefinitionInput = Omit<components['schemas']['PatchedTeacherExperiment'], 'sampling'> & {
  name: string
  question_bank_id: number
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
export type PublishCommit = components['schemas']['PublishCommit']
export type PublishParticipantCollection = components['schemas']['PublishParticipantCollection']
export type PublishParticipantDetail = components['schemas']['PublishParticipantDetail']
export type ClassTaskCollection = components['schemas']['ClassTaskCollection']
export type ClassTask = components['schemas']['ClassTask']
export type ClassTaskProgress = components['schemas']['ClassTaskProgress']
export type ScheduleRevisionInput = components['schemas']['ScheduleRevisionInput']
export type CourseConceptCollection = components['schemas']['CourseConceptCollection']

type ApiClient = ReturnType<typeof createApiClient>

export function createTeacherExperimentApi(client: ApiClient) {
  return {
    list(courseId: number, page = 1) {
      return client.get<TeacherExperimentCollection>(
        `/api/v1/teacher/courses/${courseId}/experiments/?page=${page}`,
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
    preview(planId: number, expectedRevision: number) {
      return client.mutate<PublishPreviewSummary>(
        `/api/v1/teacher/publish-plans/${planId}/preview/`,
        {
          method: 'POST',
          body: { expected_revision: expectedRevision },
          refreshAuthoritativeState: () => this.plan(planId),
        },
      )
    },
    review(planId: number) {
      return client.get<PublishPlanReview>(`/api/v1/teacher/publish-plans/${planId}/review/`)
    },
    commit(planId: number, expectedRevision: number) {
      return client.mutate<PublishCommit>(`/api/v1/teacher/publish-plans/${planId}/commit/`, {
        method: 'POST',
        body: { expected_revision: expectedRevision },
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
  }
}

export function getTeacherExperimentApi() {
  const bootstrap = getBootstrapContext()
  return createTeacherExperimentApi(
    createApiClient({ getCsrfToken: () => bootstrap.csrf_token }),
  )
}
