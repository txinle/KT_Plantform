import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ api: { list: vi.fn() } }))

vi.mock('@/app/bootstrap', () => ({
  getBootstrapContext: () => ({ csrf_token: 'csrf-token', user: { display_name: '李老师', username: 'teacher' } }),
}))
vi.mock('./api', () => ({ getTeacherCourseApi: () => mocks.api }))

import TeacherCourseList from './TeacherCourseList.vue'

async function render(path = '/teacher/courses/') {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/teacher/courses/', component: TeacherCourseList },
    { path: '/teacher/courses/:rest(.*)', component: { template: '<div />' } },
  ] })
  await router.push(path)
  await router.isReady()
  const wrapper = mount(TeacherCourseList, { global: { plugins: [router] } })
  await flushPromises()
  return { wrapper, router }
}

describe('TeacherCourseList', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.api.list.mockResolvedValue({
      create_url: '/teacher/courses/create/',
      items: [{
        id: 7, name: '研究方法', code: 'RM101', term: '2026 秋', description: '研究设计课程', owner_name: '李老师',
        status: 'active', status_label: '进行中', class_count: 2, bank_count: 3, experiment_count: 4, detail_url: '/teacher/courses/7/',
      }],
    })
  })

  it('renders real course facts as a card grid and retains the create destination', async () => {
    const { wrapper } = await render()
    await flushPromises()

    expect(wrapper.get('h1').text()).toBe('我的课程')
    expect(wrapper.get('a[href="/teacher/courses/create/"]').text()).toContain('新建课程')
    expect(wrapper.get('[data-course-card="7"]').text()).toContain('研究方法')
    expect(wrapper.get('[data-course-card="7"] .kt-teacher-course-card__term').text()).toBe('2026 秋')
    expect(wrapper.get('[data-course-card="7"] p').text()).toBe('RM101 · 研究设计课程')
    expect(wrapper.find('.kt-teacher-course-card__cover').exists()).toBe(false)
    expect(wrapper.find('[data-course-card="7"] dl').exists()).toBe(false)
    expect(wrapper.get('[data-course-card="7"] a[href="/teacher/courses/7/"]').text()).toContain('进入课程')
  })

  it('keeps the empty state actionable without inventing course data', async () => {
    mocks.api.list.mockResolvedValue({ items: [], create_url: '/teacher/courses/create/' })
    const { wrapper } = await render()
    await flushPromises()

    expect(wrapper.text()).toContain('尚未创建课程')
    expect(wrapper.get('.kt-teacher-course-empty a[href="/teacher/courses/create/"]').text()).toContain('创建第一门课程')
  })
  it('separates archived courses and supports navigation back to current courses', async () => {
    const response = await mocks.api.list()
    response.items.push({ ...response.items[0], id: 8, name: '历史课程', status: 'archived', status_label: '已归档', detail_url: '/teacher/courses/8/' })
    const { wrapper, router } = await render()
    expect(wrapper.find('[data-course-card="8"]').exists()).toBe(false)
    await wrapper.get('a[href="/teacher/courses/?view=archived"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.view).toBe('archived')
    expect(wrapper.get('h1').text()).toBe('课程归档')
    expect(wrapper.find('[data-course-card="7"]').exists()).toBe(false)
    expect(wrapper.get('[data-course-card="8"]').text()).toContain('已归档')
    expect(wrapper.get('[data-course-card="8"] a').attributes('href')).toBe('/teacher/courses/8/')
    await router.back()
    await flushPromises()
    expect(wrapper.get('[data-course-card="7"]').text()).toContain('研究方法')
    expect(wrapper.find('[data-course-card="8"]').exists()).toBe(false)
  })

  it('opens the archive directly and distinguishes its empty state', async () => {
    const { wrapper } = await render('/teacher/courses/?view=archived')
    expect(wrapper.get('h1').text()).toBe('课程归档')
    expect(wrapper.text()).toContain('暂无归档课程')
    expect(wrapper.text()).not.toContain('创建第一门课程')
  })

  it('does not claim there are no courses when all courses are archived', async () => {
    const response = await mocks.api.list()
    response.items[0].status = 'archived'
    const { wrapper } = await render()
    expect(wrapper.text()).toContain('暂无进行中的课程')
    expect(wrapper.text()).not.toContain('尚未创建课程')
  })

  it('preserves loading and failure states in the archive', async () => {
    let reject!: (reason: Error) => void
    mocks.api.list.mockReturnValue(new Promise((_, fail) => { reject = fail }))
    const { wrapper } = await render('/teacher/courses/?view=archived')
    expect(wrapper.get('[role="status"]').text()).toContain('正在加载课程')
    reject(new Error('课程加载失败。'))
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toBe('课程加载失败。')
    expect(wrapper.find('.kt-teacher-course-empty').exists()).toBe(false)
  })

})
