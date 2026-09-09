import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  scope: vi.fn(), updateScope: vi.fn(), get: vi.fn(), replace: vi.fn(),
  applyRouteQuery: vi.fn(),
  route: {
    params: { courseId: '7' },
    query: { version_mode: 'single', status: 'completed', search: '张同学', session_ref: 'S-001', table: 'sessions', page: '2', package: 'experiment' } as Record<string, string | number | undefined>,
    path: '/teacher/courses/7/data/',
  },
}))

vi.mock('vue-router', async () => {
  const { reactive } = await import('vue')
  const route = reactive(mocks.route)
  mocks.applyRouteQuery.mockImplementation((query: Record<string, string | number | undefined>) => { route.query = query })
  return {
    useRoute: () => route,
    useRouter: () => ({ replace: async (location: { query: Record<string, string | number | undefined> }) => {
      mocks.replace(location)
      route.query = location.query
    } }),
    RouterLink: { props: ['to'], template: '<a :href="typeof to === \'string\' ? to : to.path" :data-query="typeof to === \'object\' ? JSON.stringify(to.query) : undefined"><slot /></a>' },
    RouterView: { props: ['scopeRevision'], template: '<div data-router-view :data-scope-revision="scopeRevision" />' },
  }
})
vi.mock('./api', () => ({
  getTeacherDataCenterApi: () => ({ scope: mocks.scope, updateScope: mocks.updateScope }),
  routeQuery: (query: object) => ({ ...query }),
}))
vi.mock('@/api/client', () => ({ createApiClient: () => ({ get: mocks.get }) }))
vi.mock('@/app/bootstrap', () => ({
  getBootstrapContext: () => ({ csrf_token: 'csrf-token', user: { display_name: '数据教师' } }),
}))

import TeacherDataCenterLayout from './TeacherDataCenterLayout.vue'

enableAutoUnmount(afterEach)

describe('TeacherDataCenterLayout', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.applyRouteQuery({ version_mode: 'single', status: 'completed', search: '张同学', session_ref: 'S-001', table: 'sessions', page: '2', package: 'experiment' })
  })

  it('updates the scope through JSON and renders the authoritative response', async () => {
    mocks.get.mockResolvedValue({ id: 7, name: '研究方法', code: 'KT101', term: '2026 秋', status: 'active' })
    mocks.scope.mockResolvedValue({
      course: { id: 7, name: '研究方法', code: 'KT101', term: '2026 秋', status: 'active' },
      current: { experiment_id: 3, experiment_ids: [3, 5], class_id: 4, is_complete: true, display: '数据实验 · 实验班' },
      experiments: [{ id: 3, name: '数据实验', versions: [{ id: 8, version_number: 1 }] }, { id: 5, name: '追踪实验', versions: [] }],
      classes: [{ id: 4, name: '实验班', academic_year: '2026' }],
      scope_update_url: '/teacher/courses/7/data/scope/',
    })
    mocks.updateScope.mockResolvedValue({
      course: { id: 7, name: '研究方法', code: 'KT101', term: '2026 秋', status: 'active' },
      current: { experiment_id: 3, experiment_ids: [3, 5], class_id: 4, is_complete: true, display: '数据实验 · 实验班（已保存）' },
      experiments: [{ id: 3, name: '数据实验', versions: [{ id: 8, version_number: 1 }] }, { id: 5, name: '追踪实验', versions: [] }],
      classes: [{ id: 4, name: '实验班', academic_year: '2026' }],
      scope_update_url: '/api/v1/teacher/courses/7/data/scope/',
    })
    const wrapper = mount(TeacherDataCenterLayout)
    await flushPromises()

    const navigation = wrapper.get('[aria-label="课程导航"]').element
    const links = Array.from(navigation.children).filter((item): item is HTMLAnchorElement => item instanceof HTMLAnchorElement)
    expect(links.map((link) => link.textContent?.trim())).toEqual(['我的课程', '概览', '题库', '班级', '实验', '数据', '设置'])
    expect(wrapper.get('nav[aria-label="数据中心导航"]').classes()).toContain('kt-sidebar-secondary-nav')
    expect(wrapper.findAll('nav[aria-label="课程导航"] .kt-workspace-navigation-icon')).toHaveLength(7)
    expect(wrapper.get('[aria-label="当前数据范围"]').text()).toContain('课程：KT101 研究方法')
    expect(wrapper.get('[aria-label="当前数据范围"]').text()).toContain('班级：2026 实验班')
    expect(wrapper.get('[aria-label="当前数据范围"]').text()).toContain('已选择实验：数据实验、追踪实验（2 个实验）')
    expect(wrapper.get('.kt-scope-bar summary').text()).toBe('更改范围')
    const form = wrapper.get('.kt-scope-bar form')
    expect(Array.from((form.get('select[name="experiment_ids"]').element as HTMLSelectElement).selectedOptions).map(option => option.value)).toEqual(['3', '5'])
    expect((form.get('select[name="class_id"]').element as HTMLSelectElement).value).toBe('4')
    await form.trigger('submit')
    await flushPromises()
    expect(mocks.updateScope).toHaveBeenCalledWith(7, { experiment_ids: [3, 5], class_id: 4 })
    expect(mocks.replace).toHaveBeenCalledWith({ query: { experiment_ids: '3,5', class_id: 4, view: undefined } })
    expect(wrapper.get('[aria-label="当前数据范围"]').text()).toContain('已选择实验：数据实验、追踪实验（2 个实验）')
    expect(wrapper.get('.kt-data-page').attributes('data-scope-revision')).toBe('1')
    const navigationQuery = JSON.parse(wrapper.get('a[href="/teacher/courses/7/data/advanced/"]').attributes('data-query') || '{}')
    expect(navigationQuery).toEqual({ experiment_ids: '3,5', class_id: 4 })
    expect(wrapper.get('a[href="/teacher/courses/7/data/teaching/"]')).toBeTruthy()
  })

  it('keeps research scope query fields and fills persisted experiment and class values', async () => {
    mocks.get.mockResolvedValue({ id: 7, name: '研究方法', code: 'KT101', term: '2026 秋', status: 'active' })
    mocks.scope.mockResolvedValue({
      course: { id: 7, name: '研究方法', code: 'KT101', term: '2026 秋', status: 'active' },
      current: { experiment_id: 3, experiment_ids: [3, 5], class_id: 4, is_complete: true, display: '数据实验 · 实验班' },
      experiments: [{ id: 3, name: '数据实验', versions: [] }, { id: 5, name: '追踪实验', versions: [] }],
      classes: [{ id: 4, name: '实验班', academic_year: '2026' }],
      scope_update_url: '/api/v1/teacher/courses/7/data/scope/',
    })
    const wrapper = mount(TeacherDataCenterLayout)
    await flushPromises()

    const navigationQuery = JSON.parse(wrapper.get('a[href="/teacher/courses/7/data/advanced/"]').attributes('data-query') || '{}')
    expect(navigationQuery).toEqual({
      version_mode: 'single', status: 'completed', search: '张同学',
      experiment_ids: '3,5', class_id: 4,
    })
  })

  it('keeps student and session scope for exports, quality and history but clears drill-down state from research preview', async () => {
    mocks.applyRouteQuery({ experiment_ids: '3,5', class_id: '4', student_id: '31', session_ref: 'S-001', question_ref: 'Q001', visit: 'Q001:2', view: 'timeline' })
    mocks.get.mockResolvedValue({ id: 7, name: '研究方法', code: 'KT101', term: '2026 秋', status: 'active' })
    mocks.scope.mockResolvedValue({
      course: { id: 7, name: '研究方法', code: 'KT101', term: '2026 秋', status: 'active' },
      current: { experiment_id: 3, experiment_ids: [3, 5], class_id: 4, is_complete: true, display: '数据实验 · 实验班' },
      experiments: [{ id: 3, name: '数据实验', versions: [] }, { id: 5, name: '追踪实验', versions: [] }],
      classes: [{ id: 4, name: '实验班', academic_year: '2026' }],
      scope_update_url: '/api/v1/teacher/courses/7/data/scope/',
    })
    const wrapper = mount(TeacherDataCenterLayout)
    await flushPromises()

    for (const path of ['exports/', 'quality/', 'exports/history/']) {
      const query = JSON.parse(wrapper.get(`a[href="/teacher/courses/7/data/${path}"]`).attributes('data-query') || '{}')
      expect(query).toMatchObject({ experiment_ids: '3,5', class_id: '4', student_id: '31', session_ref: 'S-001' })
      expect(query).not.toHaveProperty('question_ref')
      expect(query).not.toHaveProperty('visit')
      expect(query).not.toHaveProperty('view')
    }
    const advancedQuery = JSON.parse(wrapper.get('a[href="/teacher/courses/7/data/advanced/"]').attributes('data-query') || '{}')
    expect(advancedQuery).toEqual({ experiment_ids: '3,5', class_id: '4' })
  })
})
