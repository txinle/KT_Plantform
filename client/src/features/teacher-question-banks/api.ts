import { createApiClient } from '@/api/client'
import type { components } from '@/api/generated/schema'
import { getBootstrapContext } from '@/app/bootstrap'

export type QuestionBankCollection = components['schemas']['QuestionBankCollection']
export type CourseConceptCollection = components['schemas']['CourseConceptCollection'] & {
  summary: { total: number; active: number; used: number; archived: number }
}
export type QuestionBankDetail = components['schemas']['QuestionBankDetail']
type GeneratedQuestionCollection = components['schemas']['QuestionCollection']
export type QuestionCollection = Omit<GeneratedQuestionCollection, 'items'> & {
  items: Array<GeneratedQuestionCollection['items'][number] & { draft_reasons: string[] }>
}
export type BankConceptCollection = components['schemas']['BankConceptCollection']
export interface CourseConceptImportRow {
  sequence: number
  code: string
  name: string
  classification: 'new' | 'unchanged' | 'archived_unchanged' | 'conflict'
  error?: string
}
export interface CourseConceptImportPreview {
  batch_id: string
  preview: { confirmable: boolean; rows: CourseConceptImportRow[] }
}

type ApiClient = ReturnType<typeof createApiClient>
type PageQuery = { page?: number; query?: string; all?: boolean }
type QuestionQuery = PageQuery & { questionType?: string; status?: string }

function queryString(input: Record<string, string | number | undefined>) {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(input)) {
    if (value !== undefined && value !== '') params.set(key, String(value))
  }
  const query = params.toString()
  return query ? `?${query}` : ''
}

export function createTeacherQuestionBankApi(client: ApiClient) {
  return {
    list(courseId: number, input: PageQuery = {}) {
      return client.get<QuestionBankCollection>(
        `/api/v1/teacher/courses/${courseId}/banks/${queryString({ page: input.page, q: input.query })}`,
      )
    },
    createBank(courseId: number, input: { name: string; description: string; visibility: string }) {
      return client.mutate<{ id: number; name: string; detail_url: string }>(
        `/api/v1/teacher/courses/${courseId}/banks/`,
        { method: 'POST', body: input },
      )
    },
    courseConcepts(courseId: number, input: PageQuery = {}) {
      return client.get<CourseConceptCollection>(
        `/api/v1/teacher/courses/${courseId}/concepts/${queryString({ page: input.page, q: input.query, all: input.all ? 1 : undefined })}`,
      )
    },
    createCourseConcept(courseId: number, input: { sequence: number; name: string; description: string }) {
      return client.mutate<{ id: number; kc_code: string; name: string }>(
        `/api/v1/teacher/courses/${courseId}/concepts/`,
        { method: 'POST', body: input },
      )
    },
    previewCourseConceptImport(courseId: number, file: File) {
      const body = new FormData()
      body.append('file', file)
      return client.mutateForm<CourseConceptImportPreview>(
        `/api/v1/teacher/courses/${courseId}/concepts/import/preview/`,
        { method: 'POST', body },
      )
    },
    confirmCourseConceptImport(courseId: number, batchId: string) {
      return client.mutate<{ created: number; unchanged: number }>(
        `/api/v1/teacher/courses/${courseId}/concepts/import/confirm/`,
        { method: 'POST', body: { batch_id: batchId } },
      )
    },
    detail(courseId: number, bankId: number) {
      return client.get<QuestionBankDetail>(
        `/api/v1/teacher/courses/${courseId}/banks/${bankId}/`,
      )
    },
    questions(courseId: number, bankId: number, input: QuestionQuery = {}) {
      return client.get<QuestionCollection>(
        `/api/v1/teacher/courses/${courseId}/banks/${bankId}/questions/${queryString({
          page: input.page,
          q: input.query,
          question_type: input.questionType,
          status: input.status,
        })}`,
      )
    },
    bankConcepts(courseId: number, bankId: number, input: PageQuery = {}) {
      return client.get<BankConceptCollection>(
        `/api/v1/teacher/courses/${courseId}/banks/${bankId}/concepts/${queryString({ page: input.page, q: input.query })}`,
      )
    },
  }
}

export function getTeacherQuestionBankApi() {
  const bootstrap = getBootstrapContext()
  return createTeacherQuestionBankApi(
    createApiClient({ getCsrfToken: () => bootstrap.csrf_token }),
  )
}
