import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ scope: vi.fn(), get: vi.fn() }))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { courseId: '7' }, query: {}, path: '/teacher/courses/7/data/' }),
  RouterLink: { props: ['to'], template: '<a :href="typeof to === \'string\' ? to : to.path"><slot /></a>' },
  RouterView: { template: '<div data-router-view />' },
}))
vi.mock('./api', () => ({
  getTeacherDataCenterApi: () => ({ scope: mocks.scope }),
  routeQuery: () => ({}),
}))
vi.mock('@/api/client', () => ({ createApiClient: () => ({ get: mocks.get }) }))
vi.mock('@/app/bootstrap', () => ({
  getBootstrapContext: () => ({ csrf_token: 'csrf-token', user: { display_name: '数据教师' } }),
}))

import TeacherDataCenterLayout from './TeacherDataCenterLayout.vue'

describe('TeacherDataCenterLayout', () => {
  it('renders the same-url workbench navigation and document scope form', async () => {
    mocks.get.mockResolvedValue({ id: 7, name: '研究方法', code: 'KT101', term: '2026 秋', status: 'active' })
    mocks.scope.mockResolvedValue({
      course: { id: 7, name: '研究方法', code: 'KT101', term: '2026 秋', status: 'active' },
      current: { experiment_id: 3, class_id: 4, is_complete: true, display: '数据实验 · 实验班' },
      experiments: [{ id: 3, name: '数据实验', versions: [{ id: 8, version_number: 1 }] }],
      classes: [{ id: 4, name: '实验班', academic_year: '2026' }],
      scope_update_url: '/teacher/courses/7/data/scope/',
    })
    const wrapper = mount(TeacherDataCenterLayout)
    await flushPromises()

    const navigation = wrapper.get('[aria-label="课程导航"]').element
    const links = Array.from(navigation.children).filter((item): item is HTMLAnchorElement => item instanceof HTMLAnchorElement)
    expect(links.map((link) => link.textContent?.trim())).toEqual(['我的课程', '概览', '题库', '班级', '实验', '数据', '设置'])
    expect(wrapper.get('nav[aria-label="数据中心导航"]').classes()).toContain('kt-sidebar-secondary-nav')
    const form = wrapper.get('form[action="/teacher/courses/7/data/scope/"]')
    expect(form.attributes('method')).toBe('post')
    expect(form.get('input[name="csrfmiddlewaretoken"]').attributes('value')).toBe('csrf-token')
    expect((form.get('select[name="experiment_id"]').element as HTMLSelectElement).value).toBe('3')
    expect((form.get('select[name="class_id"]').element as HTMLSelectElement).value).toBe('4')
    expect(wrapper.get('a[href="/teacher/courses/7/data/teaching/"]')).toBeTruthy()
  })
})
