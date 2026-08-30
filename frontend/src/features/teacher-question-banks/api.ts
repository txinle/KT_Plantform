import { createApiClient } from '@/api/client'
import type { components } from '@/api/generated/schema'
import { getBootstrapContext } from '@/app/bootstrap'

export type QuestionBankCollection = components['schemas']['QuestionBankCollection']
export type CourseConceptCollection = components['schemas']['CourseConceptCollection']
export type QuestionBankDetail = components['schemas']['QuestionBankDetail']
export type QuestionCollection = components['schemas']['QuestionCollection']
export type BankConceptCollection = components['schemas']['BankConceptCollection']

type ApiClient = ReturnType<typeof createApiClient>
type PageQuery = { page?: number; query?: string }
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
    courseConcepts(courseId: number, input: PageQuery = {}) {
      return client.get<CourseConceptCollection>(
        `/api/v1/teacher/courses/${courseId}/concepts/${queryString({ page: input.page, q: input.query })}`,
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
