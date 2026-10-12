import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const route = { query: {} as Record<string, string> }
const mocks = vi.hoisted(() => ({ api: { students: vi.fn(), createStudent: vi.fn(), resetLegacyStudentPassword: vi.fn() } }))

vi.mock('vue-router', () => ({ useRoute: () => route, useRouter: () => ({ push: vi.fn() }) }))
vi.mock('./api', () => ({ getTeacherAccountManagementApi: () => mocks.api }))

import TeacherAccountStudentList from './TeacherAccountStudentList.vue'

function payload() {
  return {
    items: [],
    class_options: [{ id: 4, name: '账户班', academic_year: '2026', display_name: '2026 账户班', is_active: true }],
    query: '', selected_class_id: null,
  }
}

describe('TeacherAccountStudentList', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    route.query = {}
    mocks.api.students.mockResolvedValue(payload())
    mocks.api.createStudent.mockResolvedValue({
      student: { id: 9, student_number: 'A001', display_name: '账户学生', username: 'A001', class_group: payload().class_options[0] },
    })
  })

  it('creates a legacy account through the server-authorized Vue form', async () => {
    const wrapper = mount(TeacherAccountStudentList)
    await flushPromises()

    await wrapper.get('[data-account-student-create] input[name="student_number"]').setValue('A001')
    await wrapper.get('[data-account-student-create] input[name="display_name"]').setValue('账户学生')
    await wrapper.get('[data-account-student-create] select[name="class_group"]').setValue('4')
    await wrapper.get('[data-account-student-create]').trigger('submit')
    await flushPromises()

    expect(mocks.api.createStudent).toHaveBeenCalledWith({
      student_number: 'A001', display_name: '账户学生', class_group: 4,
    })
    expect(wrapper.find('[data-account-student-create] input[name="password"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('初始密码为 S + 学号')
    expect(wrapper.text()).toContain('学生账号已创建。')
  })

  it('requires explicit confirmation before resetting to S plus the current student number', async () => {
    mocks.api.students.mockResolvedValue({
      ...payload(),
      items: [{ id: 9, student_number: 'A001', display_name: '账户学生', username: 'A001', class_group: payload().class_options[0] }],
    })
    mocks.api.resetLegacyStudentPassword.mockResolvedValue({})
    const wrapper = mount(TeacherAccountStudentList)
    await flushPromises()

    await wrapper.get('[data-reset-student-password]').trigger('click')
    expect(wrapper.text()).toContain('S + 当前学号（SA001）')
    expect(wrapper.text()).toContain('现有登录会话将全部失效')
    expect(wrapper.find('input[type="password"]').exists()).toBe(false)
    await wrapper.get('[data-reset-student-password-confirm]').trigger('click')
    await flushPromises()

    expect(mocks.api.resetLegacyStudentPassword).toHaveBeenCalledWith(9)
  })
})
