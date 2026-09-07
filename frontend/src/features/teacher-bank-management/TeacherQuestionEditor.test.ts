import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/client'

const mocks = vi.hoisted(() => ({ load: vi.fn(), save: vi.fn(), push: vi.fn() }))
vi.mock('vue-router', () => ({ useRoute: () => ({ params: { courseId: '1', bankId: '2', questionId: '3' } }), useRouter: () => ({ push: mocks.push }) }))
vi.mock('./api', () => ({ getTeacherBankManagementApi: () => ({ questionEditor: mocks.load, updateQuestion: mocks.save }) }))
import TeacherQuestionEditor from './TeacherQuestionEditor.vue'

const mapping = { schema_version: 'question-kc-tree-v2', root_kc_code: 'KC1', nodes: [{ kc_code: 'KC1', parent_kc_code: null, order: 1 }] }
const fixture = () => ({ bank: { name: '题库' }, detail_url: '/teacher/courses/1/banks/2/', question: {
  code: 'Q1', text: '题干', question_type: 'single_choice', status: 'active', teacher_difficulty: '0.100',
  absolute_tolerance: '', relative_tolerance: '', explanation: '原解析', correct_answer_text: 'B', accepted_answers_text: '',
  options: { a: '甲', b: '乙', c: '丙', d: '丁', e: '', f: '' }, kc_mapping: mapping,
}, question_types: [{ value: 'single_choice', label: '单选题' }], statuses: [{ value: 'active', label: '可用' }], concepts: [{ kc_code: 'KC1', name: '知识点' }] })
async function setup(data = fixture()) {
  mocks.load.mockResolvedValue(data)
  const wrapper = mount(TeacherQuestionEditor, { global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } } })
  await flushPromises()
  return wrapper
}
describe('question editor disclosure preserves saved fields', () => {
  beforeEach(() => { vi.resetAllMocks(); mocks.save.mockResolvedValue({ detail_url: '/teacher/courses/1/banks/2/' }) })
  it('defaults to an answer summary and preserves edits across collapse', async () => {
    const wrapper = await setup()
    const toggle = wrapper.get('[data-answer-toggle]')
    expect(toggle.attributes('aria-expanded')).toBe('false')
    expect(wrapper.get('#question-answer').isVisible()).toBe(false)
    await toggle.trigger('click')
    await wrapper.get('#question-answer').setValue('C')
    await toggle.trigger('click')
    expect(wrapper.text()).toContain('正确答案 C')
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(mocks.save).toHaveBeenCalledWith(1, 2, 3, expect.objectContaining({ correct_answer_text: 'C' }))
  })
  it('expands hidden answer fields for server validation errors', async () => {
    const wrapper = await setup()
    mocks.save.mockRejectedValue(new ApiError(400, { error: { fields: { correct_answer_text: ['答案无效'] } } }))
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(wrapper.get('[data-answer-toggle]').attributes('aria-expanded')).toBe('true')
    expect(wrapper.get('#question-answer').isVisible()).toBe(true)
  })
  it('keeps loaded extra options visible and preserves them and KC mapping on save', async () => {
    const data = fixture(); data.question.options.e = '已有 E'
    const wrapper = await setup(data)
    expect(wrapper.get('details').attributes('open')).toBeDefined()
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(mocks.save).toHaveBeenCalledWith(1, 2, 3, expect.objectContaining({ option_e: '已有 E', teacher_difficulty: '0.100', explanation: '原解析', kc_mapping_json: JSON.stringify(mapping) }))
  })
  it('retains edited options after closing their disclosure', async () => {
    const wrapper = await setup()
    expect(wrapper.get('details').attributes('open')).toBeUndefined()
    await wrapper.get('#option-f').setValue('新增 F')
    await wrapper.get('details').trigger('toggle')
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(mocks.save).toHaveBeenCalledWith(1, 2, 3, expect.objectContaining({ option_f: '新增 F' }))
  })
  it('opens extra options when the server returns a field validation error', async () => {
    const wrapper = await setup()
    mocks.save.mockRejectedValue(new ApiError(400, { error: { message: '请检查选项', fields: { option_e: ['选项 E 不符合要求'] } } }))
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(wrapper.get('details').attributes('open')).toBeDefined()
    expect(wrapper.text()).toContain('选项 E 不符合要求')
    expect(mocks.push).not.toHaveBeenCalled()
  })
})
