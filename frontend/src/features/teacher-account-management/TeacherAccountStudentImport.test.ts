import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ api: { previewLegacyImport: vi.fn(), confirmLegacyImport: vi.fn() } }))

vi.mock('./api', () => ({ getTeacherAccountManagementApi: () => mocks.api }))

import TeacherAccountStudentImport from './TeacherAccountStudentImport.vue'

describe('TeacherAccountStudentImport', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.api.previewLegacyImport.mockResolvedValue({
      preview: [{ student_number: 'A001', display_name: '导入学生', class_id: 4, class_name: '2026 账户班', account_action: 'create' }],
    })
    mocks.api.confirmLegacyImport.mockResolvedValue({ imported: 1 })
  })

  it('keeps upload preview separate from confirmation and reports the committed count', async () => {
    const wrapper = mount(TeacherAccountStudentImport)
    const file = new File(['xlsx'], 'students.xlsx')
    const input = wrapper.get('input[type="file"]').element
    Object.defineProperty(input, 'files', { value: [file] })
    await wrapper.get('input[type="file"]').trigger('change')
    await wrapper.get('[data-legacy-import-upload]').trigger('submit')
    await flushPromises()

    expect(mocks.api.previewLegacyImport).toHaveBeenCalledOnce()
    expect(wrapper.text()).toContain('导入学生')
    expect(wrapper.text()).toContain('尚未写入数据库')

    await wrapper.get('[data-legacy-import-confirm]').trigger('submit')
    await flushPromises()

    expect(mocks.api.confirmLegacyImport).toHaveBeenCalledOnce()
    expect(wrapper.text()).toContain('已导入 1 名学生。')
  })
})
