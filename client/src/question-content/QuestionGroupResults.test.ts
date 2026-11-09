import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import QuestionGroupResults from './QuestionGroupResults.vue'

const key = `${'a'.repeat(64)}.png`
const marker = `{{image:${key}}}`
const images = { [key]: 'data:image/png;base64,aGVsbG8=' }
const group = { group_id: 'g1', kind: 'reading' as const, title: 'Reading', instructions: '', material: marker, images,
  material_segments: [], content_language: 'en', members: [{ item_key: 'a1', group_order: 1, blank_id: '', question_type: 'single_choice', text: 'Why?', options: [{ key: 'A', text: marker }], submitted_answer: `A ${marker}`, correct_answer: `A ${marker}`, explanation: marker, images }] }
describe('group result pictures', () => {
  it('renders authorized response and feedback images in existing cells', () => {
    const wrapper = mount(QuestionGroupResults, { props: { groups: [group] } })
    expect(wrapper.findAll('tbody img')).toHaveLength(3)
    expect(wrapper.findAll('article img')).toHaveLength(5)
    expect(wrapper.get('input').attributes('disabled')).toBeDefined()
    expect(wrapper.get('label button').attributes('disabled')).toBeUndefined()
  })
  it('does not create answer feedback when the API omits it', () => {
    const { correct_answer, explanation, ...member } = group.members[0]!
    void correct_answer; void explanation
    const wrapper = mount(QuestionGroupResults, { props: { groups: [{ ...group, members: [member] }] } })
    expect(wrapper.text()).not.toContain('标准答案')
    expect(wrapper.findAll('tbody img')).toHaveLength(1)
  })
})
