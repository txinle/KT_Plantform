export interface FixedHint {
  id: string
  question_code: string
  level: number
  text: string
  contains_answer: boolean
}
export interface ToolRule {
  id: string
  tool: 'confidence' | 'check' | 'hint'
  trigger: 'item_enter' | 'elapsed_seconds'
  question_code: string
  after_seconds: number
  max_exposures: number
  required: boolean
  hint_id: string
}
export interface ResearchTools {
  doubt_enabled: boolean
  check_enabled: boolean
  kc_visible: boolean
  hints: FixedHint[]
  rules: ToolRule[]
}
export function emptyResearchTools(): ResearchTools {
  return { doubt_enabled: false, check_enabled: false, kc_visible: true, hints: [], rules: [] }
}
