import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/client'

const mocks = vi.hoisted(() => ({
  changePassword: vi.fn(),
  logout: vi.fn(),
  redirectPasswordChangeAuthenticationRequired: vi.fn(),
}))

vi.mock('./auth', () => ({
  changePassword: mocks.changePassword,
  logout: mocks.logout,
  redirectPasswordChangeAuthenticationRequired: mocks.redirectPasswordChangeAuthenticationRequired,
}))

import PasswordChangePage from './PasswordChangePage.vue'

describe('PasswordChangePage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.redirectPasswordChangeAuthenticationRequired.mockReturnValue(false)
  })

  it('submits two accessible password fields with an eight-character minimum', async () => {
    mocks.changePassword.mockRejectedValueOnce(new Error('两次输入的密码不一致。'))
    const wrapper = mount(PasswordChangePage)

    const first = wrapper.get('input[name="new_password1"]')
    const second = wrapper.get('input[name="new_password2"]')
    expect(first.attributes('minlength')).toBe('8')
    expect(second.attributes('minlength')).toBe('8')
    await first.setValue('new-password')
    await second.setValue('different-password')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(mocks.changePassword).toHaveBeenCalledWith('new-password', 'different-password')
    expect(wrapper.get('[role="alert"]').text()).toContain('两次输入的密码不一致。')
  })

  it('keeps logout available on the required password-change screen', async () => {
    mocks.logout.mockRejectedValueOnce(new Error('退出失败。'))
    const wrapper = mount(PasswordChangePage)

    await wrapper.get('[data-password-change-logout]').trigger('click')
    await flushPromises()

    expect(mocks.logout).toHaveBeenCalledOnce()
    expect(wrapper.get('[role="alert"]').text()).toContain('退出失败。')
  })

  it('shows structured password validation reasons instead of only the generic API message', async () => {
    mocks.changePassword.mockRejectedValueOnce(new ApiError(400, { error: {
      code: 'VALIDATION_ERROR',
      message: '请求数据无效。',
      fields: {
        new_password1: ['新密码不能与初始密码相同。'],
        new_password2: ['两次输入的密码不一致。'],
        non_field_errors: ['请修正密码后重试。'],
      },
    } }))
    const wrapper = mount(PasswordChangePage)

    await wrapper.get('input[name="new_password1"]').setValue('Same-password')
    await wrapper.get('input[name="new_password2"]').setValue('Different-password')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    const alert = wrapper.get('[role="alert"]').text()
    expect(alert).toContain('新密码不能与初始密码相同。')
    expect(alert).toContain('两次输入的密码不一致。')
    expect(alert).toContain('请修正密码后重试。')
    expect(alert).not.toContain('请求数据无效。')
  })

  it('redirects to login when a concurrent reset invalidates authentication', async () => {
    const authenticationRequired = new ApiError(401, { error: {
      code: 'AUTHENTICATION_REQUIRED', message: '登录状态已失效，请重新登录。', fields: {},
    } })
    mocks.changePassword.mockRejectedValueOnce(authenticationRequired)
    mocks.redirectPasswordChangeAuthenticationRequired.mockReturnValueOnce(true)
    const wrapper = mount(PasswordChangePage)

    await wrapper.get('input[name="new_password1"]').setValue('new-password')
    await wrapper.get('input[name="new_password2"]').setValue('new-password')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(mocks.redirectPasswordChangeAuthenticationRequired).toHaveBeenCalledWith(authenticationRequired)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  })
})
