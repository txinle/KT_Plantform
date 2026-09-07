import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  api: { tasks: vi.fn() },
  route: { name: 'student-course', params: { courseId: '7' } },
}))

vi.mock('vue-router', () => ({
  useRoute: () => mocks.route,
  RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' },
  RouterView: { template: '<div data-router-view />' },
}))
vi.mock('@/app/bootstrap', () => ({
  getBootstrapContext: () => ({
    csrf_token: 'csrf',
    user: { display_name: '学生一', student_number: 'STUDENT-001', username: 'student-one' },
  }),
}))
vi.mock('./api', () => ({ getStudentCourseApi: () => mocks.api }))

import StudentCoursesLayout from './StudentCoursesLayout.vue'

describe('StudentCoursesLayout', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.route.name = 'student-course'
  })

  it('uses the student identity header and the two approved course tabs', async () => {
    mocks.api.tasks.mockResolvedValue({
      course: { course_name: '知识追踪研究方法', course_code: 'KT101', course_term: '2026春', class_name: '实验一班' },
      items: [],
    })

    const wrapper = mount(StudentCoursesLayout)
    await flushPromises()

    expect(wrapper.get('.kt-workspace-sidebar').text()).toContain('我的课程')
    expect(wrapper.get('.kt-user-name').text()).toContain('学生一')
    expect(wrapper.get('.kt-user-name').text()).toContain('STUDENT-001')
    expect(wrapper.get('.kt-workspace-navigation').text()).toContain('作业')
    expect(wrapper.get('.kt-workspace-navigation').text()).toContain('班级中心')
    expect(wrapper.text()).not.toContain('通知')
  })

  it('keeps course links in the sidebar on feedback pages without another task request', async () => {
    mocks.route.name = 'student-result'

    const wrapper = mount(StudentCoursesLayout)
    await flushPromises()

    expect(mocks.api.tasks).not.toHaveBeenCalled()
    expect(wrapper.get('.kt-workspace-sidebar').text()).toContain('班级中心')
    expect(wrapper.get('.kt-global-nav').text()).toBe('菜单')
    expect(wrapper.find('.kt-course-bar').exists()).toBe(false)
    expect(wrapper.get('#student-main').classes()).toContain('kt-workspace-main')
    expect(wrapper.get('.kt-logout-button').text()).toBe('退出')
  })
})
