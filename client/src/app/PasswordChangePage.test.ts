import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/client'

const mocks = vi.hoisted(() => ({
  changePassword: vi.fn(),
  logout: vi.fn(),
  redirectPasswordChangeAuthenticationRequired: vi.fn(),
  homeUrl: '/manage/',
}))

vi.mock('./bootstrap', () => ({ getBootstrapContext: () => ({ user: { home_url: mocks.homeUrl } }) }))

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
    mocks.homeUrl = '/manage/'
    window.history.replaceState({}, '')
    vi.spyOn(document, 'referrer', 'get').mockReturnValue('')
  })

  it('offers cancellation back to the originating workspace without submitting passwords', async () => {
    window.history.replaceState({ back: '/manage/data/?tab=exports' }, '')
    const wrapper = mount(PasswordChangePage)
    const cancel = wrapper.get('[data-password-change-cancel]')
    expect(cancel.text()).toBe('取消并返回')
    expect(cancel.attributes('href')).toBe('/manage/data/?tab=exports')
    expect(mocks.changePassword).not.toHaveBeenCalled()
    expect(mocks.logout).not.toHaveBeenCalled()
  })

  it.each(['/manage/', '/teacher/courses/', '/student/courses/'])('returns direct visitors to their home %s', (home) => {
    mocks.homeUrl = home
    expect(mount(PasswordChangePage).get('[data-password-change-cancel]').attributes('href')).toBe(home)
  })

  it('uses a same-origin referrer for a full page entry, but never an external or password page', () => {
    vi.spyOn(document, 'referrer', 'get').mockReturnValue(`${window.location.origin}/teacher/courses/7/settings/`)
    expect(mount(PasswordChangePage).get('[data-password-change-cancel]').attributes('href')).toBe('/teacher/courses/7/settings/')
    vi.spyOn(document, 'referrer', 'get').mockReturnValue('https://example.com/')
    expect(mount(PasswordChangePage).get('[data-password-change-cancel]').attributes('href')).toBe('/manage/')
    window.history.replaceState({ back: '/password/change/' }, '')
    expect(mount(PasswordChangePage).get('[data-password-change-cancel]').attributes('href')).toBe('/manage/')
  })

  it('submits the current password and two accessible new-password fields', async () => {
    mocks.changePassword.mockRejectedValueOnce(new Error('两次输入的密码不一致。'))
    const wrapper = mount(PasswordChangePage)

    const current = wrapper.get('input[name="current_password"]')
    const first = wrapper.get('input[name="new_password1"]')
    const second = wrapper.get('input[name="new_password2"]')
    expect(current.attributes('autocomplete')).toBe('current-password')
    expect(first.attributes('minlength')).toBe('8')
    expect(second.attributes('minlength')).toBe('8')
    await current.setValue('current-password')
    await first.setValue('new-password')
    await second.setValue('different-password')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(mocks.changePassword).toHaveBeenCalledWith(
      'current-password', 'new-password', 'different-password',
    )
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
        current_password: ['当前密码不正确。'],
        new_password1: ['新密码不能与初始密码相同。'],
        new_password2: ['两次输入的密码不一致。'],
        non_field_errors: ['请修正密码后重试。'],
      },
    } }))
    const wrapper = mount(PasswordChangePage)

    await wrapper.get('input[name="current_password"]').setValue('current-password')
    await wrapper.get('input[name="new_password1"]').setValue('Same-password')
    await wrapper.get('input[name="new_password2"]').setValue('Different-password')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    const alert = wrapper.get('[role="alert"]').text()
    expect(alert).toContain('当前密码不正确。')
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

    await wrapper.get('input[name="current_password"]').setValue('current-password')
    await wrapper.get('input[name="new_password1"]').setValue('new-password')
    await wrapper.get('input[name="new_password2"]').setValue('new-password')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(mocks.redirectPasswordChangeAuthenticationRequired).toHaveBeenCalledWith(authenticationRequired)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  })
})
