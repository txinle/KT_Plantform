import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ api: { list: vi.fn() } }))

vi.mock('vue-router', () => ({ RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' } }))
vi.mock('@/app/bootstrap', () => ({
  getBootstrapContext: () => ({ csrf_token: 'csrf-token', user: { display_name: '李老师', username: 'teacher' } }),
}))
vi.mock('./api', () => ({ getTeacherCourseApi: () => mocks.api }))

import TeacherCourseList from './TeacherCourseList.vue'

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
    const wrapper = mount(TeacherCourseList)
    await flushPromises()

    expect(wrapper.get('h1').text()).toBe('我的课程')
    expect(wrapper.get('a[href="/teacher/courses/create/"]').text()).toContain('新建课程')
    expect(wrapper.get('[data-course-card="7"]').text()).toContain('研究方法')
    expect(wrapper.get('[data-course-card="7"] dt').text()).toBe('班级')
    expect(wrapper.get('[data-course-card="7"] dd').text()).toBe('2')
    expect(wrapper.get('[data-course-card="7"] dl > div:nth-child(3) dd').text()).toBe('4')
    expect(wrapper.get('[data-course-card="7"] a[href="/teacher/courses/7/"]').text()).toContain('进入课程')
  })

  it('keeps the empty state actionable without inventing course data', async () => {
    mocks.api.list.mockResolvedValue({ items: [], create_url: '/teacher/courses/create/' })
    const wrapper = mount(TeacherCourseList)
    await flushPromises()

    expect(wrapper.text()).toContain('尚未创建课程')
    expect(wrapper.get('.kt-teacher-course-empty a[href="/teacher/courses/create/"]').text()).toContain('创建第一门课程')
  })
})
