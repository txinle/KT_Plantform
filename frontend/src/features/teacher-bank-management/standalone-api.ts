import { createApiClient } from '@/api/client'
import { getBootstrapContext } from '@/app/bootstrap'
import type { QuestionRecheckResult } from './api'

type ApiClient = ReturnType<typeof createApiClient>

export interface StandaloneBank {
  id: number
  name: string
  description: string
  visibility: string
  visibility_label: string
  question_count: number
  detail_url: string
  question_import_url: string
  kc_import_url: string
}

export interface StandaloneQuestion {
  id: number
  sequence: number
  code: string
  text: string
  question_type: string
  question_type_label: string
  status: string
  status_label: string
  draft_reasons: string[]
  teacher_difficulty: string | null
  kc_mapping: {
    root_kc_code: string | null
    nodes: Array<{ kc_code: string; name: string; parent_kc_code: string | null; order: number }>
  }
  edit_url: string
}

export type StandaloneCanonicalRedirect = { redirect_url: string }

export type StandaloneBankDestination = 'detail' | 'question_import' | 'kc_import' | 'question_editor'

export interface StandaloneBankCollection {
  banks: StandaloneBank[]
  shared_banks: Array<{
    id: number
    name: string
    description: string
    question_count: number
    created_by_name: string
  }>
  visibility_choices: Array<{ value: string; label: string }>
}

export interface StandaloneConceptCollection {
  items: StandaloneConcept[]
}

export interface StandaloneQuestionEditor {
  bank: { id: number; name: string }
  question: {
    id: number
    sequence: number
    code: string
    text: string
    question_type: string
    status: string
    teacher_difficulty: string
    absolute_tolerance: string
    relative_tolerance: string
    explanation: string
    correct_answer_text: string
    accepted_answers_text: string
    options: Record<string, string>
    draft_reasons: string[]
  }
  question_types: Array<{ value: string; label: string }>
  statuses: Array<{ value: string; label: string }>
  concepts: []
  detail_url: string
}

export interface StandaloneQuestionImportPreview {
  preview_id: string
  preview: {
    rows: Array<{
      sequence: number
      text: string
      question_type: string
      question_type_label: string
      option_count: number
      teacher_difficulty: string | null
      classification?: 'ready' | 'draft'
      draft_reasons?: string[]
    }>
  }
}

export interface StandaloneKCImportPreview {
  preview_id: string
  preview: { rows: Array<{ sequence: number; primary: string; secondary: string[] }> }
}

export interface StandaloneConcept {
  id: number
  code: string
  name: string
  description: string
  scope: string
  scope_label: string
  status: string
  status_label: string
  path: string
}

export type StandaloneBankDetailResponse = {
  bank: StandaloneBank
  can_edit_metadata: boolean
  can_edit_content: boolean
  questions: StandaloneQuestion[]
} | StandaloneCanonicalRedirect

export type StandaloneBankCollectionResponse = StandaloneBankCollection | StandaloneCanonicalRedirect

export type StandaloneConceptCollectionResponse = StandaloneConceptCollection | StandaloneCanonicalRedirect

export function isStandaloneCanonicalRedirect(
  response: object,
): response is StandaloneCanonicalRedirect {
  return 'redirect_url' in response
}

export function isStandaloneBankRedirect(
  response: StandaloneBankDetailResponse,
): response is StandaloneCanonicalRedirect {
  return isStandaloneCanonicalRedirect(response)
}

export function createTeacherStandaloneQuestionBankApi(client: ApiClient) {
  return {
    banks() {
      return client.get<StandaloneBankCollectionResponse>('/api/v1/teacher/banks/')
    },
    createBank(body: { name: string; description: string; visibility: string }) {
      return client.mutate<StandaloneBank>('/api/v1/teacher/banks/', { method: 'POST', body })
    },
    detail(
      bankId: number,
      options: { destination?: StandaloneBankDestination; questionId?: number } = {},
    ) {
      const params = new URLSearchParams()
      if (options.destination && options.destination !== 'detail') params.set('destination', options.destination)
      if (options.questionId) params.set('question_id', String(options.questionId))
      const query = params.size ? `?${params.toString()}` : ''
      return client.get<StandaloneBankDetailResponse>(
        `/api/v1/teacher/banks/${bankId}/${query}`,
      )
    },
    updateVisibility(bankId: number, visibility: string) {
      return client.mutate<StandaloneBank>(`/api/v1/teacher/banks/${bankId}/visibility/`, {
        method: 'PUT', body: { visibility },
      })
    },
    questionEditor(bankId: number, questionId: number) {
      return client.get<StandaloneQuestionEditor>(
        `/api/v1/teacher/banks/${bankId}/questions/${questionId}/editor/`,
      )
    },
    updateQuestion(bankId: number, questionId: number, body: Record<string, unknown>) {
      return client.mutate<StandaloneQuestionEditor>(
        `/api/v1/teacher/banks/${bankId}/questions/${questionId}/editor/`,
        { method: 'PUT', body },
      )
    },
    recheckQuestions(bankId: number) {
      return client.mutate<QuestionRecheckResult>(
        `/api/v1/teacher/banks/${bankId}/questions/recheck/`,
        { method: 'POST' },
      )
    },
    previewQuestionImport(bankId: number, file: File) {
      const body = new FormData()
      body.append('file', file)
      return client.mutateForm<StandaloneQuestionImportPreview>(
        `/api/v1/teacher/banks/${bankId}/imports/questions/preview/`,
        { method: 'POST', body },
      )
    },
    confirmQuestionImport(bankId: number, previewId: string) {
      return client.mutate<{ created: number; active?: number; draft?: number; detail_url: string }>(
        `/api/v1/teacher/banks/${bankId}/imports/questions/confirm/`,
        { method: 'POST', body: { preview_id: previewId } },
      )
    },
    previewKCImport(bankId: number, file: File) {
      const body = new FormData()
      body.append('file', file)
      return client.mutateForm<StandaloneKCImportPreview>(
        `/api/v1/teacher/banks/${bankId}/imports/kc/preview/`,
        { method: 'POST', body },
      )
    },
    confirmKCImport(bankId: number, previewId: string) {
      return client.mutate<{ questions_updated: number }>(
        `/api/v1/teacher/banks/${bankId}/imports/kc/confirm/`,
        { method: 'POST', body: { preview_id: previewId } },
      )
    },
    copySharedBank(sourceBankId: number) {
      return client.mutate<StandaloneBank>(
        `/api/v1/teacher/banks/shared/${sourceBankId}/copy/`,
        { method: 'POST' },
      )
    },
    concepts() {
      return client.get<StandaloneConceptCollectionResponse>('/api/v1/teacher/concepts/')
    },
  }
}

export function getTeacherStandaloneQuestionBankApi() {
  const bootstrap = getBootstrapContext()
  return createTeacherStandaloneQuestionBankApi(
    createApiClient({ getCsrfToken: () => bootstrap.csrf_token }),
  )
}
