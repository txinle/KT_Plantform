import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const route = { params: { courseId: '7', classId: '11' }, meta: {} as Record<string, unknown> }
const router = { push: vi.fn() }
const mocks = vi.hoisted(() => ({
  api: {
    management: vi.fn(),
    updateManagement: vi.fn(),
    deleteManagement: vi.fn(),
    manualEnrollment: vi.fn(),
    previewCopy: vi.fn(),
    confirmCopy: vi.fn(),
    previewCourseImport: vi.fn(),
    confirmCourseImport: vi.fn(),
    moveStudent: vi.fn(),
    removeStudent: vi.fn(),
    resetCourseStudentPassword: vi.fn(),
  },
}))

vi.mock('vue-router', () => ({ useRoute: () => route, useRouter: () => router }))
vi.mock('./api', () => ({ getTeacherAccountManagementApi: () => mocks.api }))

import TeacherClassManagement from './TeacherClassManagement.vue'

function payload() {
  return {
    course: { id: 7, name: '研究方法', code: 'RM101', term: '2026 秋', status: 'active' },
    class_group: {
      id: 11, name: '目标班', academic_year: '2026', is_active: true, state_token: 'state-1', detail_url: '/teacher/courses/7/classes/11/',
    },
    enrollments: [],
    target_classes: [
      { id: 11, name: '目标班', academic_year: '2026', display_name: '2026 目标班', is_active: true },
      { id: 12, name: '转入班', academic_year: '2026', display_name: '2026 转入班', is_active: true },
    ],
    source_classes: [],
    capabilities: {
      can_update: true, can_delete: true, can_add_students: true, can_move_students: true, can_remove_students: true, can_reset_passwords: true, can_update_visibility: true,
    },
    actions: {
      detail_url: '/teacher/courses/7/classes/11/',
      classes_url: '/teacher/courses/7/classes/',
      add_students_url: '/teacher/courses/7/classes/11/students/add/',
      visibility_url: '/teacher/courses/7/classes/11/student-visibility/',
      import_template_url: '/teacher/courses/7/classes/11/students/import-template.xlsx',
    },
  }
}

describe('TeacherClassManagement', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    route.meta = {}
    router.push.mockResolvedValue(undefined)
    mocks.api.management.mockResolvedValue(payload())
    mocks.api.manualEnrollment.mockResolvedValue({
      student: { id: 23, student_number: 'S001', display_name: '新学生', username: 'S001', class_group: null },
      created_account: true,
    })
  })

  it('uses the server-provided class state and submits manual enrollment through the API', async () => {
    const wrapper = mount(TeacherClassManagement)
    await flushPromises()

    expect(wrapper.get('h1').text()).toBe('目标班')
    expect(wrapper.get('[data-course-class-settings] input[name="state_token"]').attributes('value')).toBe('state-1')
    await wrapper.get('[aria-label="班级管理操作"] button:nth-child(2)').trigger('click')
    await wrapper.get('[data-manual-enrollment] input[name="student_number"]').setValue('S001')
    await wrapper.get('[data-manual-enrollment] input[name="display_name"]').setValue('新学生')
    await wrapper.get('[data-manual-enrollment]').trigger('submit')
    await flushPromises()

    expect(mocks.api.manualEnrollment).toHaveBeenCalledWith(7, 11, {
      student_number: 'S001', display_name: '新学生',
    })
    expect(wrapper.text()).toContain('学生已加入当前班级。')
  })

  it('confirms a class deletion through the API before returning to the class list', async () => {
    const wrapper = mount(TeacherClassManagement)
    await flushPromises()

    await wrapper.get('[data-course-class-delete]').trigger('click')
    await wrapper.get('[data-course-class-delete-confirm]').trigger('click')
    await flushPromises()

    expect(mocks.api.deleteManagement).toHaveBeenCalledWith(7, 11)
    expect(router.push).toHaveBeenCalledWith('/teacher/courses/7/classes/')
  })
})
