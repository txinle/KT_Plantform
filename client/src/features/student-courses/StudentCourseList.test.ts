import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { acceptBootstrapContext, clearBootstrapContext } from '@/app/bootstrap'
import { afterEach } from 'vitest'

const mocks = vi.hoisted(() => ({
  api: { courses: vi.fn() },
}))

vi.mock('./api', () => ({ getStudentCourseApi: () => mocks.api }))

import StudentCourseList from './StudentCourseList.vue'

describe('StudentCourseList', () => {
  afterEach(() => clearBootstrapContext())
  it('links only the courses returned by the student allow-list API', async () => {
    acceptBootstrapContext({ csrf_token: 'csrf', user: { id: 7, role: 'student', display_name: '学生', username: 'S7', student_number: 'S7', home_url: '/student/', uses_initial_password: false, initial_password_reminder_pending: false, research_consent_required: false, research_consent_version: 'v1' } })
    mocks.api.courses.mockResolvedValue({
      items: [{
        course_id: 7,
        course_name: '知识追踪研究方法',
        course_code: 'KT101',
        course_term: '2026春',
        course_status: 'active',
        class_name: '实验一班',
      }],
    })

    const wrapper = mount(StudentCourseList)
    await flushPromises()

    expect(wrapper.get('h1').text()).toBe('我的课程')
    expect(wrapper.text()).toContain('知识追踪研究方法')
    expect(wrapper.get('.kt-student-course-card__cover').text()).toContain('KT101')
    expect(wrapper.get('a.kt-button').attributes('href')).toBe('/student/courses/7/')
  })
})
