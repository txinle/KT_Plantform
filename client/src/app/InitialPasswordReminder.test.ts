import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const auth = vi.hoisted(() => ({ markInitialPasswordReminderShown: vi.fn() }))
vi.mock('./auth', () => auth)

import InitialPasswordReminder from './InitialPasswordReminder.vue'

describe('InitialPasswordReminder', () => {
  beforeEach(() => vi.clearAllMocks())

  it('confirms only after the reminder has rendered and keeps the platform usable on failure', async () => {
    auth.markInitialPasswordReminderShown.mockRejectedValueOnce(new Error('network unavailable'))
    const wrapper = mount(InitialPasswordReminder)

    expect(wrapper.get('[role="dialog"]').text()).toContain('你目前仍在使用初始密码')
    expect(wrapper.get('[data-reminder-change-password]').attributes('href')).toBe('/password/change/')
    expect(wrapper.get('[data-reminder-dismiss]').text()).toBe('暂不修改')
    await flushPromises()

    expect(auth.markInitialPasswordReminderShown).toHaveBeenCalledOnce()
    expect(wrapper.get('[role="dialog"]').isVisible()).toBe(true)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    await wrapper.get('[data-reminder-dismiss]').trigger('click')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
  })
})
