import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  api: { list: vi.fn() },
  push: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { courseId: '7' }, query: {} }),
  useRouter: () => ({ push: mocks.push }),
  RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' },
}))
vi.mock('./api', () => ({ getTeacherClassApi: () => mocks.api }))
vi.mock('@/app/bootstrap', () => ({
  getBootstrapContext: () => ({ csrf_token: 'csrf-token' }),
}))

import TeacherClassList from './TeacherClassList.vue'

describe('TeacherClassList', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.api.list.mockResolvedValue({
      course: { id: 7, name: '研究方法', code: 'KT101', term: '2026 秋', status: 'active' },
      items: [{
        id: 11,
        name: '实验一班',
        academic_year: '2026',
        is_active: true,
        active_student_count: 32,
        detail_url: '/teacher/courses/7/classes/11/',
      }],
      page: { number: 1, size: 20, total_items: 1, total_pages: 1, start: 1, end: 1 },
    })
  })

  it('renders the paged class facts and preserves document-based class creation', async () => {
    const wrapper = mount(TeacherClassList)
    await flushPromises()

    expect(wrapper.get('h1').text()).toBe('班级')
    expect(wrapper.text()).toContain('实验一班')
    expect(wrapper.text()).toContain('32 人')
    const form = wrapper.get('form[method="post"]')
    expect(form.get('input[name="csrfmiddlewaretoken"]').attributes('value')).toBe('csrf-token')
    expect(form.get('input[name="name"]').attributes('name')).toBe('name')
    expect(form.get('input[name="academic_year"]').attributes('name')).toBe('academic_year')
  })
})
