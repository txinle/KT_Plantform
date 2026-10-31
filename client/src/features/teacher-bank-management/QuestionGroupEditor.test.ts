import { flushPromises, mount } from '@vue/test-utils'
import { expect, it, vi } from 'vitest'
const api = vi.hoisted(() => ({ get: vi.fn(), save: vi.fn(), remove: vi.fn() }))
vi.mock('./group-api', () => ({ getQuestionGroupApi: () => api }))
import QuestionGroupEditor from './QuestionGroupEditor.vue'

it('saves all members with the fetched revision and preserves entered values on conflict', async () => {
  api.get.mockResolvedValue({ id: 3, kind: 'cloze', title: 'Saturday', instructions: '', material: 'Ella {{b01}} home.', content_language: 'en', revision: 4,
    members: [{ id: 11, item_key: '11', group_order: 1, blank_id: 'b01', question_type: 'fill_blank', text: '', options: [], answer: 'went', accepted_answers: [], explanation: '', max_score: '2.00', text_match_case_sensitive: false }] })
  api.save.mockRejectedValue(new Error('题组已被其他编辑修改'))
  const wrapper = mount(QuestionGroupEditor, { props: { bankId: 2, groupId: 3 } })
  await flushPromises()
  await wrapper.get('[data-group-max-score]').setValue('3.50')
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(api.save).toHaveBeenCalledWith(3, expect.objectContaining({ expected_revision: 4, members: [expect.objectContaining({ id: 11, max_score: '3.50' })] }))
  expect(wrapper.text()).toContain('题组已被其他编辑修改')
  expect(wrapper.get<HTMLInputElement>('[data-group-max-score]').element.value).toBe('3.50')
})
