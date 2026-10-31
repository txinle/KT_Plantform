import type { GroupMemberView, QuestionGroupView } from './question-group-content'
export interface ScoreProgress { earned: string | number; paper_maximum: string | number; graded_maximum: string | number; graded_count: number; item_count: number; pending_count: number; unsubmitted_count?: number }
export interface ResultMember extends GroupMemberView {
  response_value?: unknown
  submitted_answer?: unknown
  final_answer?: unknown
  is_correct?: boolean | null
  correct_answer?: unknown
  explanation?: string
  score?: string | number | { value: string | number; maximum: string | number }
}
export interface ResultGroup extends Omit<QuestionGroupView, 'members'> { members: ResultMember[]; score_progress?: ScoreProgress }
