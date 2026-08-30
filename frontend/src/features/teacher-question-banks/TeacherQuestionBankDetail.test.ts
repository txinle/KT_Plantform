import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  api: { detail: vi.fn(), questions: vi.fn(), bankConcepts: vi.fn() },
  push: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { courseId: '7', bankId: '11' }, query: {} }),
  useRouter: () => ({ push: mocks.push }),
  RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' },
}))
vi.mock('./api', () => ({ getTeacherQuestionBankApi: () => mocks.api }))
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
        course_sequence: 1,
        stable_code: 'C000001',
        sequence: 1,
        code: 'Q001',
        text: '2 + 2 等于多少？',
        question_type: 'single_choice',
        question_type_label: '单选题',
        primary_concept: { id: 23, name: '基础运算' },
        teacher_difficulty: '0.125',
        status: 'active',
        status_label: '启用',
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
        name: '基础运算',
        primary_count: 1,
        secondary_count: 0,
        question_count: 1,
        questions: [{ id: 31, sequence: 1, code: 'Q001', edit_url: '/teacher/courses/7/banks/11/questions/31/edit/' }],
      }],
      page: { number: 1, size: 20, total_items: 1, total_pages: 1, start: 1, end: 1 },
    })
  })

  it('renders list-safe question facts and preserves legacy edit, import, archive, and delete posts', async () => {
    const wrapper = mount(TeacherQuestionBankDetail)
    await flushPromises()

    expect(wrapper.get('h1').text()).toBe('演示题库')
    expect(wrapper.text()).toContain('2 + 2 等于多少？')
    expect(wrapper.text()).toContain('C000001')
    expect(wrapper.text()).toContain('基础运算')
    expect(wrapper.text()).not.toContain('correct_answer')
    expect(wrapper.get('a[href="/teacher/courses/7/banks/11/edit/"]')).toBeTruthy()
    expect(wrapper.get('a[href="/teacher/courses/7/banks/11/import/"]')).toBeTruthy()
    expect(wrapper.get('a[href="/teacher/courses/7/banks/11/kc-import/"]')).toBeTruthy()

    const archive = wrapper.get('form[action="/teacher/courses/7/banks/11/archive/"]')
    expect(archive.get('input[name="csrfmiddlewaretoken"]').attributes('value')).toBe('csrf-token')
    const deleteBank = wrapper.get('form[action="/teacher/courses/7/banks/11/delete/"]')
    expect(deleteBank.get('input[name="confirmation"]').attributes('value')).toBe('delete')
    const deleteQuestion = wrapper.get('form[action="/teacher/courses/7/banks/11/questions/31/delete/"]')
    expect(deleteQuestion.get('input[name="confirmation"]').attributes('value')).toBe('delete')
  })
})
