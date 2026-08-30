import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  api: { tasks: vi.fn() },
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { courseId: '7' } }),
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
  it('uses the student identity header and the two approved course tabs', async () => {
    mocks.api.tasks.mockResolvedValue({
      course: { course_name: '知识追踪研究方法', course_code: 'KT101', course_term: '2026春', class_name: '实验一班' },
      items: [],
    })

    const wrapper = mount(StudentCoursesLayout)
    await flushPromises()

    expect(wrapper.get('.kt-global-nav').text()).toBe('我的课程')
    expect(wrapper.get('.kt-user-name').text()).toContain('学生一')
    expect(wrapper.get('.kt-user-name').text()).toContain('STUDENT-001')
    expect(wrapper.get('.kt-course-nav').text()).toContain('实验任务')
    expect(wrapper.get('.kt-course-nav').text()).toContain('我的学习')
    expect(wrapper.text()).not.toContain('通知')
  })
})
