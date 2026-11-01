import { flushPromises, mount } from '@vue/test-utils'
import { expect, it, vi } from 'vitest'
const api = vi.hoisted(() => ({ get: vi.fn(), save: vi.fn(), remove: vi.fn() }))
vi.mock('./group-api', () => ({ getQuestionGroupApi: () => api }))
import QuestionGroupEditor from './QuestionGroupEditor.vue'

it('renders unsaved material immediately in the student preview', async () => {
  const wrapper = mount(QuestionGroupEditor, { props: { bankId: 2 } })
  await flushPromises()
  await wrapper.get('.kt-group-editor__material').setValue('Unsaved reading material')
  expect(wrapper.get('.kt-group-editor__preview').text()).toContain('Unsaved reading material')
})

it('filters empty options and keeps preview trial answers local until an explicit save', async () => {
  api.save.mockClear()
  const wrapper = mount(QuestionGroupEditor, { props: { bankId: 2, initialDocument: {
    id: 0, revision: 0, kind: 'reading', title: '', instructions: '', material: 'Trial material', material_segments: [], content_language: 'en',
    members: [{ item_key: 'trial', group_order: 1, blank_id: '', question_type: 'single_choice', text: 'Choose', options: [{ key: 'A', text: 'Visible option' }, { key: 'B', text: '' }], answer: 'A', accepted_answers: [], explanation: '', max_score: '1.00', text_match_case_sensitive: false }],
  } } })
  await flushPromises()
  const radios = wrapper.findAll('.kt-group-editor__preview input[type=radio]')
  expect(radios).toHaveLength(1)
  await radios[0]!.setValue(true)
  expect((radios[0]!.element as HTMLInputElement).checked).toBe(true)
  expect(api.save).not.toHaveBeenCalled()
})

it('keeps the original group and strips member identities when a confirmed type conversion is saved as new', async () => {
  api.save.mockClear()
  api.get.mockResolvedValue({ id: 3, kind: 'cloze', title: 'Saturday', instructions: '', material: 'Ella {{b01}} home.', content_language: 'en', revision: 4,
    members: [{ id: 11, item_key: '11', group_order: 1, blank_id: 'b01', question_type: 'fill_blank', text: '', options: [], answer: 'went', accepted_answers: [], explanation: '', max_score: '2.00', text_match_case_sensitive: false }] })
  api.save.mockRejectedValue(new Error('Word bank must be supplied'))
  const wrapper = mount(QuestionGroupEditor, { props: { bankId: 2, groupId: 3 } })
  await flushPromises()
  await wrapper.get('select').setValue('words')
  await wrapper.get('form').trigger('submit')
  expect(api.save).not.toHaveBeenCalled()
  await wrapper.get('label.kt-alert input[type=checkbox]').setValue(true)
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(api.save).toHaveBeenCalledWith(undefined, expect.objectContaining({ kind: 'words', members: [expect.objectContaining({ id: null, max_score: '2.00' })] }))
})

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
