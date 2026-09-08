import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  api: { detail: vi.fn(), questions: vi.fn(), bankConcepts: vi.fn() },
  managementApi: { archiveBank: vi.fn(), restoreBank: vi.fn(), deleteBank: vi.fn(), deleteQuestion: vi.fn(), recheckQuestions: vi.fn() },
  push: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { courseId: '7', bankId: '11' }, query: {} }),
  useRouter: () => ({ push: mocks.push }),
  RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' },
}))
vi.mock('./api', () => ({ getTeacherQuestionBankApi: () => mocks.api }))
vi.mock('../teacher-bank-management/api', () => ({ getTeacherBankManagementApi: () => mocks.managementApi }))
vi.mock('@/app/bootstrap', () => ({
  getBootstrapContext: () => ({ csrf_token: 'csrf-token' }),
}))

import TeacherQuestionBankDetail from './TeacherQuestionBankDetail.vue'

describe('TeacherQuestionBankDetail', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.api.detail.mockResolvedValue({
      id: 11,
      name: '演示题库',
      description: '示例说明',
      visibility: 'private',
      visibility_label: '私有',
      question_count: 1,
      created_by_name: '课程教师',
      updated_at: '2026-08-30T08:00:00Z',
      can_edit_metadata: true,
      can_edit_content: true,
      list_url: '/teacher/courses/7/banks/',
      edit_url: '/teacher/courses/7/banks/11/edit/',
      visibility_update_url: '/teacher/courses/7/banks/11/visibility/',
      archive_url: '/teacher/courses/7/banks/11/archive/',
      restore_url: null,
      delete_url: '/teacher/courses/7/banks/11/delete/',
      question_import_url: '/teacher/courses/7/banks/11/import/',
      kc_import_url: '/teacher/courses/7/banks/11/kc-import/',
      course: { id: 7, name: '研究方法', code: 'KT101', term: '2026 秋', status: 'active' },
    })
    mocks.api.questions.mockResolvedValue({
      bank: { id: 11, name: '演示题库' },
      items: [{
        id: 31,
        course_sequence: 51,
        stable_code: 'C000007-Q000051',
        sequence: 7,
        code: 'Q001',
        text: '2 + 2 等于多少？',
        question_type: 'single_choice',
        question_type_label: '单选题',
        kc_mapping: { schema_version: 'question-kc-tree-v2', root_kc_code: 'KC000001', mapping_hash: 'abc', nodes: [{ kc_code: 'KC000001', name: '基础运算', description: '', status: 'active', depth: 1, parent_kc_code: null, order: 1 }] },
        teacher_difficulty: '0.125',
        status: 'active',
        status_label: '启用',
        draft_reasons: [],
        edit_url: '/teacher/courses/7/banks/11/questions/31/edit/',
        delete_url: '/teacher/courses/7/banks/11/questions/31/delete/',
      }],
      page: { number: 1, size: 20, total_items: 1, total_pages: 1, start: 1, end: 1 },
      filters: { query: '', question_type: '', status: '', question_types: [], statuses: [] },
    })
    mocks.api.bankConcepts.mockResolvedValue({
      bank: { id: 11, name: '演示题库' },
      items: [{
        id: 23,
        kc_code: 'KC000001',
        name: '基础运算',
        level_1_count: 1,
        level_2_count: 0,
        level_3_count: 0,
        question_count: 1,
        questions: [{ id: 31, sequence: 7, code: 'Q001', edit_url: '/teacher/courses/7/banks/11/questions/31/edit/' }],
      }],
      page: { number: 1, size: 20, total_items: 1, total_pages: 1, start: 1, end: 1 },
    })
    mocks.managementApi.archiveBank.mockResolvedValue({ id: 11, visibility: 'archived' })
    mocks.managementApi.restoreBank.mockResolvedValue({ id: 11, visibility: 'private' })
    mocks.managementApi.deleteBank.mockResolvedValue(undefined)
    mocks.managementApi.deleteQuestion.mockResolvedValue(undefined)
    mocks.managementApi.recheckQuestions.mockResolvedValue({ checked: 1, activated: 1, draft: 0, rows: [{ id: 31, code: 'Q001', status: 'active', draft_reasons: [] }] })
  })

  it('renders list-safe question facts and delegates management actions to the JSON API', async () => {
    const wrapper = mount(TeacherQuestionBankDetail)
    await flushPromises()

    expect(wrapper.get('h1').text()).toBe('演示题库')
    expect(wrapper.get('[aria-label="层级目录"]').text()).toContain('演示题库')
    expect(wrapper.get('.kt-bank-course-context').text()).toContain('KT101 · 2026 秋')
    expect(wrapper.text()).toContain('2 + 2 等于多少？')
    expect(wrapper.text()).not.toContain('C000007-Q000051')
    expect(wrapper.text()).not.toContain('Q001')
    expect(wrapper.findAll('.kt-bank-question-section th').map((cell) => cell.text())).toEqual(['题库内序号', '题目', '题型', '知识点结构', '难度', '状态', '操作'])
    expect(wrapper.get('.kt-bank-question-section').findAll('td')[0]?.text()).toBe('7')
    expect(wrapper.get('.kt-mobile-question-card').text()).toContain('第 7 题')
    expect(wrapper.get('.kt-bank-concept-section').get('a').text()).toBe('第 7 题')
    expect(wrapper.text()).toContain('基础运算')
    expect(wrapper.text()).not.toContain('correct_answer')
    expect(wrapper.get('[data-bank-edit]').attributes('href')).toBe('/teacher/courses/7/banks/11/edit/')
    expect(wrapper.get('[data-question-import]').attributes('href')).toBe('/teacher/courses/7/banks/11/import/')
    expect(wrapper.get('[data-question-import]').text()).toBe('导入题库')
    expect(wrapper.find('[data-kc-import]').exists()).toBe(false)
    await wrapper.get('[data-bank-archive]').trigger('click')
    await flushPromises()
    expect(mocks.managementApi.archiveBank).toHaveBeenCalledWith(7, 11)

    await wrapper.get('[data-delete-question]').trigger('click')
    await flushPromises()
    expect(mocks.managementApi.deleteQuestion).toHaveBeenCalledWith(7, 11, 31)
  })

  it('shows draft reasons and refreshes the authoritative bank after rechecking drafts', async () => {
    const collection = await mocks.api.questions()
    collection.items[0].status = 'draft'
    collection.items[0].status_label = '草稿'
    collection.items[0].draft_reasons = ['正确答案为空。']
    mocks.api.questions.mockClear()
    mocks.api.questions.mockResolvedValue(collection)
    const wrapper = mount(TeacherQuestionBankDetail)
    await flushPromises()

    expect(wrapper.text()).toContain('正确答案为空。')
    await wrapper.get('[data-recheck-drafts]').trigger('click')
    await flushPromises()

    expect(mocks.managementApi.recheckQuestions).toHaveBeenCalledWith(7, 11)
    expect(mocks.api.questions).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('检查 1 道，转为可用 1 道，仍为草稿 0 道')
  })
})
