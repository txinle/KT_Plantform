import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/client'

const route = { params: { courseId: '7', classId: '11' }, meta: {} as Record<string, unknown> }
const router = { push: vi.fn() }
const mocks = vi.hoisted(() => ({
  api: {
    management: vi.fn(),
    updateManagement: vi.fn(),
    manualEnrollment: vi.fn(),
    previewCopy: vi.fn(),
    confirmCopy: vi.fn(),
    previewCourseImport: vi.fn(),
    confirmCourseImport: vi.fn(),
    moveStudent: vi.fn(),
    removeStudent: vi.fn(),
    resetCourseStudentPassword: vi.fn(),
    updateStudentIdentity: vi.fn(),
  },
  deletion: {
    previewExperiment: vi.fn(), deleteExperiment: vi.fn(),
    previewClassExperiment: vi.fn(), removeClassExperiment: vi.fn(),
    previewClass: vi.fn(), deleteClass: vi.fn(), cleanup: vi.fn(),
  },
}))

vi.mock('vue-router', () => ({ useRoute: () => route, useRouter: () => router }))
vi.mock('./api', () => ({ getTeacherAccountManagementApi: () => mocks.api }))
vi.mock('@/features/teacher-deletion/api', () => ({ getTeacherDeletionApi: () => mocks.deletion }))

import TeacherClassManagement from './TeacherClassManagement.vue'

function payload() {
  return {
    course: { id: 7, name: '研究方法', code: 'RM101', term: '2026 秋', status: 'active' },
    class_group: {
      id: 11, name: '目标班', academic_year: '2026', is_active: true, state_token: 'state-1', detail_url: '/teacher/courses/7/classes/11/',
    },
    enrollments: [{
      enrollment_id: 31,
      student: { id: 23, student_number: 'S001', display_name: '原姓名', username: 'S001', class_group: null },
      identity_state_token: 'identity-1',
      affects_other_courses: true,
    }],
    target_classes: [
      { id: 11, name: '目标班', academic_year: '2026', display_name: '2026 目标班', is_active: true },
      { id: 12, name: '转入班', academic_year: '2026', display_name: '2026 转入班', is_active: true },
    ],
    source_classes: [],
    capabilities: {
      can_update: true, can_delete: true, can_add_students: true, can_edit_students: true, can_move_students: true, can_remove_students: true, can_reset_passwords: true, can_update_visibility: true,
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
    mocks.deletion.previewClass.mockResolvedValue({
      action: 'delete_class', object_id: 11, object_name: '目标班', allowed: true, blocked_reason: '',
      experiments: [], counts: { class_count: 1, session_count: 0, export_count: 0, member_count: 0 }, confirmation_token: 'class-token',
    })
    mocks.deletion.deleteClass.mockResolvedValue({ deleted: true, cleanup_pending: false, cleanup_id: null })
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
    await flushPromises()
    await wrapper.get('[data-deletion-confirm]').trigger('click')
    await flushPromises()

    expect(mocks.deletion.previewClass).toHaveBeenCalledWith(7, 11)
    expect(mocks.deletion.deleteClass).toHaveBeenCalledWith(7, 11, 'class-token')
    expect(router.push).toHaveBeenCalledWith('/teacher/courses/7/classes/')
  })

  it('edits only student identity fields with the enrollment state token and refreshes management', async () => {
    mocks.api.updateStudentIdentity.mockResolvedValue({
      student: { id: 23, student_number: 'S002', display_name: '新姓名', username: 'S001', class_group: null },
      identity_state_token: 'identity-2',
      affects_other_courses: true,
    })
    const wrapper = mount(TeacherClassManagement)
    await flushPromises()

    await wrapper.get('[data-edit-student-identity]').trigger('click')
    expect(wrapper.text()).toContain('该修改会同步影响此学生在其他课程中的身份显示')
    await wrapper.get('[data-student-identity-form] input[name="student_number"]').setValue('S002')
    await wrapper.get('[data-student-identity-form] input[name="display_name"]').setValue('新姓名')
    await wrapper.get('[data-student-identity-form]').trigger('submit')
    await flushPromises()

    expect(mocks.api.updateStudentIdentity).toHaveBeenCalledWith(7, 11, 23, {
      student_number: 'S002', display_name: '新姓名', state_token: 'identity-1',
    })
    expect(mocks.api.management).toHaveBeenCalledTimes(2)
  })

  it('closes a stale identity editor and refreshes authoritative management state', async () => {
    mocks.api.updateStudentIdentity.mockRejectedValueOnce(new ApiError(409, { error: {
      code: 'STALE_CONFIRMATION', message: '学生身份已被其他操作更新，请重新打开编辑。', fields: {},
    } }))
    const wrapper = mount(TeacherClassManagement)
    await flushPromises()

    await wrapper.get('[data-edit-student-identity]').trigger('click')
    await wrapper.get('[data-student-identity-form]').trigger('submit')
    await flushPromises()

    expect(wrapper.find('[data-student-identity-form]').exists()).toBe(false)
    expect(wrapper.text()).toContain('学生身份已被其他操作更新，请重新打开编辑。')
    expect(mocks.api.management).toHaveBeenCalledTimes(2)
  })

  it('confirms a name conflict using the exact submitted manual enrollment values', async () => {
    const conflict = new ApiError(409, { error: {
      code: 'STUDENT_NAME_CONFLICT',
      message: '该学号已绑定其他姓名。',
      fields: {
        student_number: 'S001', existing_name: '原姓名', submitted_name: '新姓名',
        confirmation_token: 'confirm-1', affects_other_courses: true,
      },
    } })
    mocks.api.manualEnrollment.mockRejectedValueOnce(conflict).mockResolvedValueOnce({
      student: { id: 23, student_number: 'S001', display_name: '新姓名', username: 'S001', class_group: null },
      created_account: false,
    })
    route.meta = { workflow: 'manual' }
    const wrapper = mount(TeacherClassManagement)
    await flushPromises()

    await wrapper.get('[data-manual-enrollment] input[name="student_number"]').setValue('S001')
    await wrapper.get('[data-manual-enrollment] input[name="display_name"]').setValue('新姓名')
    await wrapper.get('[data-manual-enrollment]').trigger('submit')
    await flushPromises()
    const dialog = wrapper.get('[role="dialog"]')
    expect(dialog.element.tagName).toBe('DIALOG')
    expect(dialog.attributes('open')).toBeDefined()
    expect(dialog.text()).toContain('原姓名')
    expect(dialog.text()).toContain('新姓名')
    expect(dialog.text()).toContain('该修改会同步影响此学生在其他课程中的身份显示')
    expect(wrapper.get('[data-confirm-student-name]').text()).toBe('确认更正并加入')
    await wrapper.get('[data-manual-enrollment] input[name="display_name"]').setValue('随后修改但不应提交')

    await wrapper.get('[data-confirm-student-name]').trigger('click')
    await flushPromises()
    expect(mocks.api.manualEnrollment).toHaveBeenLastCalledWith(7, 11, {
      student_number: 'S001', display_name: '新姓名', confirmation_token: 'confirm-1',
    })
  })

  it('cancels the name-conflict modal without another write', async () => {
    mocks.api.manualEnrollment.mockRejectedValueOnce(new ApiError(409, { error: {
      code: 'STUDENT_NAME_CONFLICT', message: '该学号已绑定其他姓名。', fields: {
        student_number: 'S001', existing_name: '原姓名', submitted_name: '新姓名',
        confirmation_token: 'confirm-1', affects_other_courses: false,
      },
    } }))
    route.meta = { workflow: 'manual' }
    const wrapper = mount(TeacherClassManagement)
    await flushPromises()

    await wrapper.get('[data-manual-enrollment] input[name="student_number"]').setValue('S001')
    await wrapper.get('[data-manual-enrollment] input[name="display_name"]').setValue('新姓名')
    await wrapper.get('[data-manual-enrollment]').trigger('submit')
    await flushPromises()
    await wrapper.get('[data-cancel-student-name]').trigger('click')

    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(mocks.api.manualEnrollment).toHaveBeenCalledTimes(1)
  })

  it('discards a stale confirmation and refreshes authoritative management state', async () => {
    const conflict = new ApiError(409, { error: {
      code: 'STUDENT_NAME_CONFLICT',
      message: '该学号已绑定其他姓名。',
      fields: {
        student_number: 'S001', existing_name: '原姓名', submitted_name: '新姓名',
        confirmation_token: 'confirm-1', affects_other_courses: false,
      },
    } })
    const stale = new ApiError(409, { error: {
      code: 'STALE_CONFIRMATION', message: '确认状态已过期，请重试。', fields: {},
    } })
    mocks.api.manualEnrollment.mockRejectedValueOnce(conflict).mockRejectedValueOnce(stale)
    route.meta = { workflow: 'manual' }
    const wrapper = mount(TeacherClassManagement)
    await flushPromises()

    await wrapper.get('[data-manual-enrollment] input[name="student_number"]').setValue('S001')
    await wrapper.get('[data-manual-enrollment] input[name="display_name"]').setValue('新姓名')
    await wrapper.get('[data-manual-enrollment]').trigger('submit')
    await flushPromises()
    await wrapper.get('[data-confirm-student-name]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('确认状态已过期，请重试。')
    expect(mocks.api.management).toHaveBeenCalledTimes(2)
  })

  it('resets the global password without accepting a custom password', async () => {
    mocks.api.resetCourseStudentPassword.mockResolvedValue({})
    const wrapper = mount(TeacherClassManagement)
    await flushPromises()

    await wrapper.get('[data-course-reset-password]').trigger('click')
    expect(wrapper.text()).toContain('S + 当前学号（SS001）')
    expect(wrapper.text()).toContain('现有登录会话将全部失效')
    expect(wrapper.find('[aria-labelledby="reset-password-title"] input[type="password"]').exists()).toBe(false)
    await wrapper.get('[data-course-reset-password-confirm]').trigger('click')
    await flushPromises()

    expect(mocks.api.resetCourseStudentPassword).toHaveBeenCalledWith(7, 11, 23)
  })
})
