import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  api: { list: vi.fn(), courseConcepts: vi.fn() },
  push: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { courseId: '7' }, query: {} }),
  useRouter: () => ({ push: mocks.push }),
  RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' },
}))
vi.mock('./api', () => ({ getTeacherQuestionBankApi: () => mocks.api }))
vi.mock('@/app/bootstrap', () => ({
  getBootstrapContext: () => ({ csrf_token: 'csrf-token' }),
}))

import TeacherQuestionBankList from './TeacherQuestionBankList.vue'

describe('TeacherQuestionBankList', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.api.list.mockResolvedValue({
      course: { id: 7, name: '研究方法', code: 'KT101', term: '2026 秋', status: 'active' },
      items: [{
        id: 11,
        name: '演示题库',
        description: '覆盖六类题型',
        visibility: 'private',
        visibility_label: '私有',
        question_count: 4,
        created_by_name: '课程教师',
        updated_at: '2026-08-30T08:00:00Z',
        detail_url: '/teacher/courses/7/banks/11/',
        visibility_update_url: '/teacher/courses/7/banks/11/visibility/',
        archive_url: '/teacher/courses/7/banks/11/archive/',
        can_edit_metadata: true,
      }],
      page: { number: 1, size: 20, total_items: 1, total_pages: 1, start: 1, end: 1 },
      summary: {
        archived_count: 2,
        archived_banks_url: '/teacher/courses/7/banks/archived/',
        shared_sources_url: '/teacher/courses/7/banks/shared/',
      },
    })
    mocks.api.courseConcepts.mockResolvedValue({
      course: { id: 7, name: '研究方法', code: 'KT101', term: '2026 秋', status: 'active' },
      items: [{
        id: 23,
        name: '基础运算',
        question_count: 2,
        bank_count: 1,
        distribution: [{
          bank_id: 11,
          bank_name: '演示题库',
          question_count: 2,
          detail_url: '/teacher/courses/7/banks/11/',
        }],
        can_rename: true,
        can_delete: false,
        rename_url: '/teacher/courses/7/concepts/23/rename/',
        delete_url: null,
      }],
      page: { number: 1, size: 20, total_items: 1, total_pages: 1, start: 1, end: 1 },
    })
  })

  it('renders paged bank and KC facts with document-based management forms', async () => {
    const wrapper = mount(TeacherQuestionBankList)
    await flushPromises()

    expect(wrapper.get('h1').text()).toBe('题库')
    expect(wrapper.text()).toContain('演示题库')
    expect(wrapper.text()).toContain('基础运算')
    expect(wrapper.get('a[href="/teacher/courses/7/banks/archived/"]').text()).toContain('2')

    const createBank = wrapper.get('form[data-create-bank]')
    expect(createBank.attributes('method')).toBe('post')
    expect(createBank.get('input[name="csrfmiddlewaretoken"]').attributes('value')).toBe('csrf-token')
    expect(createBank.get('input[name="name"]')).toBeTruthy()
    expect(createBank.get('textarea[name="description"]')).toBeTruthy()
    expect(createBank.get('select[name="visibility"]')).toBeTruthy()

    const visibility = wrapper.get('form[action="/teacher/courses/7/banks/11/visibility/"]')
    expect((visibility.get('select[name="visibility"]').element as HTMLSelectElement).value).toBe('private')
    expect(wrapper.get('form[action="/teacher/courses/7/banks/11/archive/"]')).toBeTruthy()
    expect((wrapper.get('form[action="/teacher/courses/7/concepts/23/rename/"] input[name="name"]').element as HTMLInputElement).value).toBe('基础运算')
    expect(wrapper.get('form[data-create-concept] input[name="name"]')).toBeTruthy()
  })
})
