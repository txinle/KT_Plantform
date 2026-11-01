export interface GroupMemberView {
  item_key: string
  question_type: string
  text: string
  options: Array<{ key: string; text: string }>
  group_order: number
  blank_id: string
  max_score?: string
  assignment_id?: number
  attempt_id?: number
}

export type MaterialSegment = { kind: 'text'; text: string } | { kind: 'blank'; blank_id: string; item_key: string }
export interface QuestionGroupView {
  group_id: string
  kind: 'reading' | 'cloze' | 'words'
  word_bank?: string[]
  title: string
  instructions: string
  material: string
  material_segments: MaterialSegment[]
  content_language: string
  members: GroupMemberView[]
}
export interface GroupActivationIntent { itemKey: string; answer?: unknown; hasAnswer?: boolean; confidence?: string }
export interface GroupAnswerChange { itemKey: string; answer: unknown }
