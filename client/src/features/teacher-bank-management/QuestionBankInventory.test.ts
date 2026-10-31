import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import QuestionBankInventory from './QuestionBankInventory.vue'

describe('QuestionBankInventory', () => {
  it('shows one complete group and only server-provided types, forwarding the filter', async () => {
    const wrapper = mount(QuestionBankInventory, { props: { canEdit: true, summary: { complete_count: 1, response_item_count: 5, usable_count: 1, types: [{ value: 'words', label: '选词填空', count: 1 }] }, groups: [{ id: 2, revision: 1, kind: 'words', title: 'Book exchange', instructions: 'Choose words.', material: '', material_segments: [], content_language: 'en', status: 'active', total_max_score: '5.00', members: Array.from({ length: 5 }, (_, index) => ({ item_key: String(index), question_type: 'fill_blank', group_order: index + 1, blank_id: `b0${index + 1}`, text: '', options: [], answer: '', accepted_answers: [], explanation: '', text_match_case_sensitive: false, max_score: '1.00' })) }] } })
    expect(wrapper.text()).toContain('1 道完整题 · 5 作答项 · 5.00 分')
    expect(wrapper.findAll('option')).toHaveLength(2)
    await wrapper.get('select').setValue('words')
    expect(wrapper.emitted('filter')?.at(-1)).toEqual([{ query: '', type: 'words' }])
    await wrapper.get('.kt-bank-inventory__group-foot button').trigger('click')
    expect(wrapper.emitted('edit')?.at(-1)).toEqual([2])
    await wrapper.setProps({ canEdit: false })
    expect(wrapper.find('.kt-bank-inventory__group-foot button').exists()).toBe(false)
  })
})
