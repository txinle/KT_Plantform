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
  it('explains the identified export and submission boundaries without exposing demo credentials', () => {
    const text = mount(LoginPage).text()

    expect(text).toContain('实名主导出仅限授权场景')
    expect(text).toContain('导出文件均包含学生学号和姓名')
    expect(text).toContain('发布后使用不可变题目快照')
    expect(text).toContain('草稿保存与正式提交严格区分')
    expect(text).not.toContain('teacher123')
    expect(text).not.toContain('student123')
  })

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
