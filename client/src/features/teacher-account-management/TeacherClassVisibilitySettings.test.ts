import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ api: { visibility: vi.fn(), saveVisibility: vi.fn() } }))
vi.mock('vue-router', () => ({ useRoute: () => ({ params: { courseId: '7', classId: '11' } }) }))
vi.mock('./api', () => ({ getTeacherAccountManagementApi: () => mocks.api }))

import TeacherClassVisibilitySettings from './TeacherClassVisibilitySettings.vue'

function policy() {
  return {
    show_scores: true, show_correct_rate: true, show_item_results: true, show_class_average: true,
    rank_mode: 'self', show_kc_rates: true, show_trend: true, is_read_only: false,
  }
}

describe('TeacherClassVisibilitySettings', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.api.visibility.mockResolvedValue(policy())
    mocks.api.saveVisibility.mockResolvedValue(policy())
  })

  it('submits the complete server policy without deriving dependent visibility fields', async () => {
    const wrapper = mount(TeacherClassVisibilitySettings)
    await flushPromises()
    await wrapper.get('input[name="show_correct_rate"]').setValue(false)
    await wrapper.get('[data-student-visibility-settings]').trigger('submit')
    await flushPromises()

    expect(mocks.api.saveVisibility).toHaveBeenCalledWith(7, 11, {
      show_scores: true,
      show_correct_rate: false,
      show_item_results: true,
      show_class_average: true,
      rank_mode: 'self',
      show_kc_rates: true,
      show_trend: true,
    })
    expect(wrapper.text()).toContain('学生端显示设置已保存。')
  })
})
