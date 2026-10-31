import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'
import QuestionGroupImportPreview from './QuestionGroupImportPreview.vue'

it('selects a whole group and hides answer fields in the student view', async () => {
  const wrapper = mount(QuestionGroupImportPreview, { props: {
    groups: [{ group_id: 'g1', source_group_code: 'R001', kind: 'reading', title: 'Garden', instructions: '', material: 'A garden.', material_segments: [], content_language: 'en', member_row_ids: ['r1'], classification: 'ready', errors: [], item_count: 1, total_max_score: '2.00' }],
    rows: [{ row_id: 'r1', sequence: 1, text: 'Why?', question_type: 'single_choice', options: [{ key: 'A', text: 'Because' }], answer: 'PRIVATE ANSWER', explanation: 'PRIVATE EXPLANATION', max_score: '2.00', group_order: 1 }],
    modelValue: [],
  } })
  await wrapper.get('input[type="checkbox"]').setValue(true)
  expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual([['g1']])
  expect(wrapper.text()).toContain('PRIVATE ANSWER')
  await wrapper.get('[data-student-group-preview]').trigger('click')
  expect(wrapper.text()).not.toContain('PRIVATE ANSWER')
  expect(wrapper.text()).not.toContain('PRIVATE EXPLANATION')
  expect(wrapper.text()).toContain('A garden.')
})
