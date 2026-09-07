import { createApiClient } from '@/api/client'
import { getBootstrapContext } from '@/app/bootstrap'

type ApiClient = ReturnType<typeof createApiClient>

export interface CourseReference {
  id: number
  name: string
  code: string
  term: string
  status: string
}

export interface PageMeta {
  number: number
  size: number
  total_items: number
  total_pages: number
  start: number
  end: number
}

export interface ArchivedBank {
  id: number
  name: string
  description: string
  question_count: number
  created_by_name: string
  updated_at: string
  detail_url: string
  can_edit_metadata: boolean
}

export interface ArchivedBankCollection {
  course: CourseReference
  page: PageMeta
  items: ArchivedBank[]
}

export interface BankEditor {
  id: number
  name: string
  description: string
  visibility: string
  visibility_choices: Array<{ value: string; label: string }>
  course: CourseReference
  detail_url: string
}

export interface QuestionEditor {
  course: CourseReference
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
    kc_mapping: {
      schema_version: string
      root_kc_code: string | null
      nodes: Array<{ kc_code: string; parent_kc_code: string | null; order: number }>
    }
  }
  question_types: Array<{ value: string; label: string }>
  statuses: Array<{ value: string; label: string }>
  concepts: Array<{ id: number; kc_code: string; name: string; description: string }>
  detail_url: string
}

export interface QuestionImportPreview {
  preview_id: string
  preview: {
    rows: Array<{
      sequence: number
      text: string
      question_type: string
      question_type_label: string
      option_count: number
      teacher_difficulty: string | null
    }>
  }
}

export interface KCImportPreview {
  batch_id: string
  preview: {
    mode: 'tree_v2' | 'workbook' | 'legacy'
    confirmable: boolean
    trees?: Array<{ sequence: number; root_kc_code: string; nodes: Array<{ kc_code: string }> }>
    rows?: Array<{ sequence: number; primary: string; secondary: string[] }>
    nodes?: Array<{ code: string; name: string }>
    questions?: Array<{ stable_question_code: string; root_kc_code: string }>
  }
}

function pageQuery(page?: number) {
  return page && page > 1 ? `?page=${page}` : ''
}

export function createTeacherBankManagementApi(client: ApiClient) {
  return {
    archivedBanks(courseId: number, page?: number) {
      return client.get<ArchivedBankCollection>(
        `/api/v1/teacher/courses/${courseId}/banks/archived/${pageQuery(page)}`,
      )
    },
    archiveBank(courseId: number, bankId: number) {
      return client.mutate<{ id: number; visibility: string }>(
        `/api/v1/teacher/courses/${courseId}/banks/${bankId}/archive/`,
        { method: 'POST' },
      )
    },
    restoreBank(courseId: number, bankId: number) {
      return client.mutate<{ id: number; visibility: string }>(
        `/api/v1/teacher/courses/${courseId}/banks/${bankId}/restore/`,
        { method: 'POST' },
      )
    },
    deleteBank(courseId: number, bankId: number) {
      return client.mutate<void>(
        `/api/v1/teacher/courses/${courseId}/banks/${bankId}/delete/`,
        { method: 'DELETE', body: { confirmation: 'delete' } },
      )
    },
    deleteQuestion(courseId: number, bankId: number, questionId: number) {
      return client.mutate<void>(
        `/api/v1/teacher/courses/${courseId}/banks/${bankId}/questions/${questionId}/delete/`,
        { method: 'DELETE', body: { confirmation: 'delete' } },
      )
    },
    sharedSources(courseId: number) {
      return client.get<{ course: CourseReference; items: Array<{
        id: number; name: string; code: string; term: string; shared_bank_count: number; shared_creator_count: number
      }> }>(`/api/v1/teacher/courses/${courseId}/banks/shared/`)
    },
    sharedCreators(courseId: number, sourceCourseId: number) {
      return client.get<{ course: CourseReference; source_course: CourseReference; items: Array<{
        id: number; name: string; username: string; shared_bank_count: number
      }> }>(`/api/v1/teacher/courses/${courseId}/banks/shared/${sourceCourseId}/`)
    },
    sharedBanks(courseId: number, sourceCourseId: number, creatorId: number) {
      return client.get<{ course: CourseReference; source_course: CourseReference; creator: {
        id: number; name: string; username: string
      }; items: Array<{ id: number; name: string; description: string; question_count: number }> }>(
        `/api/v1/teacher/courses/${courseId}/banks/shared/${sourceCourseId}/creators/${creatorId}/`,
      )
    },
    copySharedBank(courseId: number, sourceCourseId: number, creatorId: number, bankId: number) {
      return client.mutate<{ id: number; name: string; detail_url: string }>(
        `/api/v1/teacher/courses/${courseId}/banks/shared/${sourceCourseId}/creators/${creatorId}/banks/${bankId}/copy/`,
        { method: 'POST' },
      )
    },
    bankEditor(courseId: number, bankId: number) {
      return client.get<BankEditor>(
        `/api/v1/teacher/courses/${courseId}/banks/${bankId}/editor/`,
      )
    },
    updateBank(courseId: number, bankId: number, body: Pick<BankEditor, 'name' | 'description' | 'visibility'>) {
      return client.mutate<BankEditor>(
        `/api/v1/teacher/courses/${courseId}/banks/${bankId}/editor/`,
        { method: 'PUT', body },
      )
    },
    questionEditor(courseId: number, bankId: number, questionId: number) {
      return client.get<QuestionEditor>(
        `/api/v1/teacher/courses/${courseId}/banks/${bankId}/questions/${questionId}/editor/`,
      )
    },
    updateQuestion(courseId: number, bankId: number, questionId: number, body: Record<string, unknown>) {
      return client.mutate<QuestionEditor>(
        `/api/v1/teacher/courses/${courseId}/banks/${bankId}/questions/${questionId}/editor/`,
        { method: 'PUT', body },
      )
    },
    previewQuestionImport(courseId: number, bankId: number, file: File) {
      const body = new FormData()
      body.append('file', file)
      return client.mutateForm<QuestionImportPreview>(
        `/api/v1/teacher/courses/${courseId}/banks/${bankId}/imports/questions/preview/`,
        { method: 'POST', body },
      )
    },
    confirmQuestionImport(courseId: number, bankId: number, previewId: string) {
      return client.mutate<{ created: number; detail_url: string }>(
        `/api/v1/teacher/courses/${courseId}/banks/${bankId}/imports/questions/confirm/`,
        { method: 'POST', body: { preview_id: previewId } },
      )
    },
    previewKCImport(courseId: number, bankId: number, file: File) {
      const body = new FormData()
      body.append('file', file)
      return client.mutateForm<KCImportPreview>(
        `/api/v1/teacher/courses/${courseId}/banks/${bankId}/imports/kc/preview/`,
        { method: 'POST', body },
      )
    },
    confirmKCImport(courseId: number, bankId: number, batchId: string) {
      return client.mutate<{ questions_updated: number }>(
        `/api/v1/teacher/courses/${courseId}/banks/${bankId}/imports/kc/confirm/`,
        { method: 'POST', body: { batch_id: batchId } },
      )
    },
    updateCourseConcept(courseId: number, conceptId: number, body: { name: string; description: string }) {
      return client.mutate<{ id: number; kc_code: string; name: string; description: string; status: string }>(
        `/api/v1/teacher/courses/${courseId}/concepts/${conceptId}/`,
        { method: 'PUT', body },
      )
    },
    deleteCourseConcept(courseId: number, conceptId: number) {
      return client.mutate<{ id: number; status: string } | void>(
        `/api/v1/teacher/courses/${courseId}/concepts/${conceptId}/`,
        { method: 'DELETE' },
      )
    },
  }
}

export function getTeacherBankManagementApi() {
  const bootstrap = getBootstrapContext()
  return createTeacherBankManagementApi(
    createApiClient({ getCsrfToken: () => bootstrap.csrf_token }),
  )
}
