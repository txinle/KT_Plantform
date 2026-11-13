import { createApiClient } from '@/api/client'
import { getBootstrapContext } from '@/app/bootstrap'
import type { GroupMemberView, QuestionGroupView } from '@/question-content/question-group-content'

export interface GroupEditorMember extends GroupMemberView {
  id?: number
  absolute_tolerance?: string | null
  relative_tolerance?: string | null
  answer: unknown
  accepted_answers: string[]
  explanation: string
  teacher_difficulty?: string | number | null
  text_match_case_sensitive: boolean
  text_match_policy_version?: string
  primary_concept_id?: number | null
  kc_mapping?: { root_kc_code: string | null; nodes: Array<{ kc_code: string; parent_kc_code: string | null; order: number }> }
}
export interface GroupEditorDocument extends Omit<QuestionGroupView, 'members' | 'group_id'> {
  id: number
  status?: string
  total_max_score?: string
  revision: number
  members: GroupEditorMember[]
  available_concepts?: Array<{ id: number; name: string; code?: string }>
}
export function getQuestionGroupApi(bankId: number, courseId?: number) {
  const client = createApiClient({ getCsrfToken: () => getBootstrapContext().csrf_token })
  const prefix = courseId ? `/api/v1/teacher/courses/${courseId}/banks/${bankId}/groups/` : `/api/v1/teacher/banks/${bankId}/groups/`
  return {
    get: (id: number) => client.get<GroupEditorDocument>(`${prefix}${id}/`),
    save: (id: number | undefined, body: Record<string, unknown>) => client.mutate<GroupEditorDocument>(id ? `${prefix}${id}/` : prefix, { method: id ? 'PUT' : 'POST', body }),
    remove: (id: number) => client.mutate<void>(`${prefix}${id}/`, { method: 'DELETE', body: { confirmation: 'delete' } }),
  }
}
