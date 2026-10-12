import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  api: { previewQuestionImport: vi.fn(), confirmQuestionImport: vi.fn() },
  push: vi.fn(),
}))
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { bankId: '11' } }),
  useRouter: () => ({ push: mocks.push }),
  RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' },
}))
vi.mock('./standalone-api', () => ({ getTeacherStandaloneQuestionBankApi: () => mocks.api }))

import TeacherStandaloneQuestionImport from './TeacherStandaloneQuestionImport.vue'

describe('TeacherStandaloneQuestionImport', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.api.previewQuestionImport.mockResolvedValue({
      preview_id: 'preview-1',
      preview: { rows: [
        { sequence: 1, text: '完整题目', question_type: 'short_answer', question_type_label: '简答题', option_count: 0, teacher_difficulty: null, classification: 'ready', draft_reasons: [] },
        { sequence: 2, text: '', question_type: 'short_answer', question_type_label: '简答题', option_count: 0, teacher_difficulty: null, classification: 'draft', draft_reasons: ['题干为空。'] },
      ] },
    })
    mocks.api.confirmQuestionImport.mockResolvedValue({ created: 2, active: 1, draft: 1, detail_url: '/teacher/banks/11/' })
  })

  it('labels complete and incomplete rows as server-classified valid imports', async () => {
    const wrapper = mount(TeacherStandaloneQuestionImport)
    const input = wrapper.get('input[type="file"]')
    Object.defineProperty(input.element, 'files', { value: [new File(['rows'], 'questions.csv')], configurable: true })
    await input.trigger('change')
    await flushPromises()

    expect(wrapper.text()).toContain('导入后可用')
    expect(wrapper.text()).toContain('保存为草稿')
    expect(wrapper.text()).toContain('题干为空。')
    await wrapper.get('[data-confirm-standalone-import]').trigger('click')
    await flushPromises()
    expect(mocks.push).toHaveBeenCalledWith('/teacher/banks/11/')
  })
})
