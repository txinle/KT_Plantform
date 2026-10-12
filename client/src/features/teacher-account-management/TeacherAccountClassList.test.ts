import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ api: { classes: vi.fn(), createClass: vi.fn() } }))
vi.mock('./api', () => ({ getTeacherAccountManagementApi: () => mocks.api }))

import TeacherAccountClassList from './TeacherAccountClassList.vue'

describe('TeacherAccountClassList', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.api.classes.mockResolvedValue({ items: [] })
    mocks.api.createClass.mockResolvedValue({ items: [] })
  })

  it('creates an account-scoped class through its Vue form', async () => {
    const wrapper = mount(TeacherAccountClassList)
    await flushPromises()
    await wrapper.get('[data-account-class-create] input[name="name"]').setValue('新账户班')
    await wrapper.get('[data-account-class-create] input[name="academic_year"]').setValue('2026')
    await wrapper.get('[data-account-class-create]').trigger('submit')
    await flushPromises()

    expect(mocks.api.createClass).toHaveBeenCalledWith({ name: '新账户班', academic_year: '2026' })
    expect(wrapper.text()).toContain('班级已创建。')
  })
})
