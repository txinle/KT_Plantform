import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import QuestionGroupContent from './QuestionGroupContent.vue'

const member = (key: string, order: number) => ({ item_key: key, group_order: order, blank_id: `b0${order}`, question_type: 'fill_blank', text: '', options: [], max_score: '1.00' })
const cloze = {
  group_id: 'g1', kind: 'cloze' as const, title: 'A Saturday', instructions: '每空一个单词。', content_language: 'en', material: '',
  material_segments: [{ kind: 'text' as const, text: 'Ella ' }, { kind: 'blank' as const, blank_id: 'b01', item_key: 'a1' }, { kind: 'text' as const, text: ' home.\nShe ' }, { kind: 'blank' as const, blank_id: 'b02', item_key: 'a2' }],
  members: [member('a1', 1), member('a2', 2)],
}

describe('QuestionGroupContent', () => {
  it('clears one reading response without touching siblings and previews never emit input', async () => {
    const group = { ...cloze, kind: 'reading' as const, members: [member('a1', 1), member('a2', 2)] }
    const wrapper = mount(QuestionGroupContent, { props: { group, responsesByItemKey: { a1: 'A', a2: 'B' }, activeItemKey: 'a1' } })
    await wrapper.get('[data-clear-group-item="a2"]').trigger('click')
    expect(wrapper.emitted('request-activate')?.at(-1)).toEqual([{ itemKey: 'a2', answer: null, hasAnswer: true }])
    await wrapper.setProps({ readonly: true })
    expect(wrapper.find('[data-clear-group-item]').exists()).toBe(false)
  })
  it('records selected word values through the existing per-item answer event', async () => {
    const wrapper = mount(QuestionGroupContent, { props: { group: { ...cloze, kind: 'words', word_bank: ['went', 'came'] }, responsesByItemKey: {}, activeItemKey: 'a1' } })
    await wrapper.get('select[data-group-item="a1"]').setValue('went')
    expect(wrapper.emitted('answer-change')?.at(-1)).toEqual([{ itemKey: 'a1', answer: 'went' }])
    await wrapper.setProps({ readonly: true })
    expect(wrapper.get('select').attributes('disabled')).toBeDefined()
  })
  it('confirms IME text after composition ends', async () => {
    const wrapper = mount(QuestionGroupContent, { props: { group: cloze, responsesByItemKey: {}, activeItemKey: 'a1' } })
    const input = wrapper.get('[data-group-item="a1"]')
    await input.trigger('compositionstart')
    await input.setValue('中')
    expect(wrapper.emitted('answer-change')).toBeUndefined()
    await input.trigger('compositionend')
    expect(wrapper.emitted('answer-change')?.at(-1)).toEqual([{ itemKey: 'a1', answer: '中' }])
  })
  it('renders independent blanks in the server-provided positions and keeps raw text safe', () => {
    const wrapper = mount(QuestionGroupContent, { props: { group: cloze, responsesByItemKey: { a1: 'went' }, activeItemKey: 'a1' } })
    expect(wrapper.findAll('input')).toHaveLength(2)
    expect((wrapper.get('[data-group-item="a1"]').element as HTMLInputElement).value).toBe('went')
    expect(wrapper.get('article').attributes('lang')).toBe('en')
    expect(wrapper.text()).not.toContain('{{b01}}')
  })
  it('preserves the first input intent when another attempt must be activated', async () => {
    const wrapper = mount(QuestionGroupContent, { props: { group: cloze, responsesByItemKey: {}, activeItemKey: 'a1' } })
    await wrapper.get('[data-group-item="a2"]').setValue('first')
    expect(wrapper.emitted('request-activate')?.at(-1)).toEqual([{ itemKey: 'a2', answer: 'first', hasAnswer: true }])
    expect(wrapper.emitted('answer-change')).toBeUndefined()
  })
  it('emits empty states and prevents edits after submission', async () => {
    const wrapper = mount(QuestionGroupContent, { props: { group: cloze, responsesByItemKey: { a1: 'went' }, activeItemKey: 'a1' } })
    await wrapper.get('[data-group-item="a1"]').setValue('')
    expect(wrapper.emitted('answer-change')?.at(-1)).toEqual([{ itemKey: 'a1', answer: '' }])
    await wrapper.setProps({ readonly: true })
    expect(wrapper.get('input').attributes('disabled')).toBeDefined()
  })
  it('renders reading options without exposing teacher answers or explanations', async () => {
    const group = { ...cloze, kind: 'reading' as const, material: '<script>bad()</script>\nA garden.', material_segments: [], members: [{ ...member('a1', 1), question_type: 'single_choice', text: 'Why?', options: [{ key: 'A', text: 'A reason' }, { key: 'B', text: 'Another reason' }], answer: 'A', explanation: 'secret feedback' }] }
    const wrapper = mount(QuestionGroupContent, { props: { group, responsesByItemKey: {}, activeItemKey: 'a1' } })
    expect(wrapper.find('script').exists()).toBe(false)
    expect(wrapper.text()).toContain('<script>bad()</script>')
    expect(wrapper.text()).not.toContain('secret feedback')
    await wrapper.get('input[value="B"]').setValue()
    expect(wrapper.emitted('answer-change')?.at(-1)).toEqual([{ itemKey: 'a1', answer: 'B' }])
  })
})
