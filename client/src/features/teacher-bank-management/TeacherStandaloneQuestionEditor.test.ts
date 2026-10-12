import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ load: vi.fn(), save: vi.fn(), push: vi.fn() }))
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { bankId: '2', questionId: '3' } }),
  useRouter: () => ({ push: mocks.push }),
}))
vi.mock('./standalone-api', () => ({
  getTeacherStandaloneQuestionBankApi: () => ({ questionEditor: mocks.load, updateQuestion: mocks.save }),
}))

import TeacherStandaloneQuestionEditor from './TeacherStandaloneQuestionEditor.vue'

const fixture = () => ({
  bank: { id: 2, name: '独立题库' },
  detail_url: '/teacher/banks/2/',
  question: {
    id: 3, sequence: 1, code: 'Q1', text: '', question_type: 'short_answer', status: 'draft',
    teacher_difficulty: '', absolute_tolerance: '', relative_tolerance: '', explanation: '',
    correct_answer_text: '', accepted_answers_text: '', options: { a: '', b: '', c: '', d: '', e: '', f: '' },
    draft_reasons: ['题干为空。', '正确答案为空。'],
  },
  question_types: [{ value: 'short_answer', label: '简答题' }],
  statuses: [{ value: 'active', label: '可用' }, { value: 'draft', label: '草稿' }, { value: 'archived', label: '归档' }],
  concepts: [],
})

describe('TeacherStandaloneQuestionEditor', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.load.mockResolvedValue(fixture())
    mocks.save.mockResolvedValue({ detail_url: '/teacher/banks/2/' })
  })

  it('uses automatic readiness while allowing incomplete drafts to be saved', async () => {
    const wrapper = mount(TeacherStandaloneQuestionEditor, { global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } } })
    await flushPromises()

    expect(wrapper.get('h1').text()).toBe('编辑第 1 题')
    expect(wrapper.text()).toContain('保存后自动判断')
    expect(wrapper.text()).toContain('题干为空。')
    expect(wrapper.find('#standalone-question-status').exists()).toBe(false)
    expect(wrapper.get('#standalone-question-type').attributes('required')).toBeDefined()
    expect(wrapper.get('#standalone-question-text').attributes('required')).toBeUndefined()
    expect(wrapper.get('#standalone-question-answer').attributes('required')).toBeUndefined()
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(mocks.save).toHaveBeenCalledWith(2, 3, expect.objectContaining({ status: 'active', text: '', correct_answer_text: '' }))
  })
})
