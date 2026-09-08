import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ login: vi.fn(), loginDestination: vi.fn() }))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: { next: '/student/' } }),
}))
vi.mock('./auth', () => ({
  login: mocks.login,
  loginDestination: mocks.loginDestination,
}))

import LoginPage from './LoginPage.vue'

describe('LoginPage', () => {
  it('submits the entered account and password through the authentication API', async () => {
    mocks.login.mockRejectedValueOnce(new Error('账号或密码不正确。'))
    const wrapper = mount(LoginPage)

    await wrapper.get('input[name="username"]').setValue('teacher')
    await wrapper.get('input[name="password"]').setValue('incorrect')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(mocks.login).toHaveBeenCalledWith('teacher', 'incorrect')
    expect(wrapper.get('[role="alert"]').text()).toContain('账号或密码不正确。')
  })
})
