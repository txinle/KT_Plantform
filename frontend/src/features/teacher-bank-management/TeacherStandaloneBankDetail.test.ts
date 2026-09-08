import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  api: { detail: vi.fn(), updateVisibility: vi.fn(), recheckQuestions: vi.fn() },
  replace: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { bankId: '11' } }),
  useRouter: () => ({ replace: mocks.replace }),
  RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' },
}))
vi.mock('./standalone-api', () => ({
  getTeacherStandaloneQuestionBankApi: () => mocks.api,
  isStandaloneBankRedirect: (response: Record<string, unknown>) => 'redirect_url' in response,
}))

import TeacherStandaloneBankDetail from './TeacherStandaloneBankDetail.vue'

describe('TeacherStandaloneBankDetail', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('redirects a course-backed legacy ID to its canonical course bank URL', async () => {
    mocks.api.detail.mockResolvedValue({ redirect_url: '/teacher/courses/7/banks/11/' })

    mount(TeacherStandaloneBankDetail)
    await flushPromises()

    expect(mocks.replace).toHaveBeenCalledWith('/teacher/courses/7/banks/11/')
  })

  it('uses the server-projected content capability instead of inferring it from visibility', async () => {
    mocks.api.detail.mockResolvedValue({
      bank: {
        id: 11,
        name: '独立题库',
        description: '',
        visibility: 'private',
        visibility_label: '私有',
        question_count: 1,
        detail_url: '/teacher/banks/11/',
        question_import_url: '/teacher/banks/11/import/',
        kc_import_url: '/teacher/banks/11/kc-import/',
      },
      can_edit_metadata: false,
      can_edit_content: false,
      questions: [{
        id: 31,
        sequence: 1,
        code: 'Q0001',
        text: '题目',
        question_type: 'short_answer',
        question_type_label: '简答题',
        status: 'draft',
        status_label: '草稿',
        draft_reasons: ['正确答案为空。'],
        teacher_difficulty: null,
        kc_mapping: { root_kc_code: null, nodes: [] },
        edit_url: '/teacher/banks/11/questions/31/edit/',
      }],
    })

    const wrapper = mount(TeacherStandaloneBankDetail)
    await flushPromises()

    expect(wrapper.find('a[href="/teacher/banks/11/questions/31/edit/"]').exists()).toBe(false)
    expect(wrapper.find('a[href="/teacher/banks/11/import/"]').exists()).toBe(false)
    expect(wrapper.find('a[href="/teacher/banks/11/kc-import/"]').exists()).toBe(false)
    expect(wrapper.find('[data-recheck-drafts]').exists()).toBe(false)
    expect(wrapper.text()).toContain('正确答案为空。')
    expect(wrapper.text()).toContain('只读')
    expect(wrapper.text()).not.toContain('Q0001')
    expect(wrapper.get('th').text()).toBe('题库内序号')
  })

  it('rechecks drafts only when the server grants content editing', async () => {
    const payload = {
      bank: {
        id: 11, name: '独立题库', description: '', visibility: 'private', visibility_label: '私有',
        question_count: 1, detail_url: '/teacher/banks/11/', question_import_url: '/teacher/banks/11/import/', kc_import_url: '/teacher/banks/11/kc-import/',
      },
      can_edit_metadata: true,
      can_edit_content: true,
      questions: [{
        id: 31, sequence: 1, code: 'Q0001', text: '', question_type: 'short_answer', question_type_label: '简答题',
        status: 'draft', status_label: '草稿', draft_reasons: ['题干为空。'], teacher_difficulty: null,
        kc_mapping: { root_kc_code: null, nodes: [] }, edit_url: '/teacher/banks/11/questions/31/edit/',
      }],
    }
    mocks.api.detail.mockResolvedValue(payload)
    mocks.api.recheckQuestions.mockResolvedValue({ checked: 1, activated: 0, draft: 1, rows: [{ id: 31, code: 'Q0001', status: 'draft', draft_reasons: ['题干为空。'] }] })
    const wrapper = mount(TeacherStandaloneBankDetail)
    await flushPromises()

    await wrapper.get('[data-recheck-drafts]').trigger('click')
    await flushPromises()

    expect(mocks.api.recheckQuestions).toHaveBeenCalledWith(11)
    expect(mocks.api.detail).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('检查 1 道，转为可用 0 道，仍为草稿 1 道')
  })
})
