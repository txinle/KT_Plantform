import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/client'

const mocks = vi.hoisted(() => ({ login: vi.fn(), loginDestination: vi.fn(), push: vi.fn() }))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: { next: '/student/' } }),
  useRouter: () => ({ push: mocks.push }),
}))
vi.mock('./auth', () => ({
  login: mocks.login,
  loginDestination: mocks.loginDestination,
}))

import LoginPage from './LoginPage.vue'

describe('LoginPage', () => {
  it('uses the login response for an explicitly server-enabled student route', async () => {
    mocks.login.mockResolvedValueOnce({ user: { home_url: '/student/' }, client_navigation: { student_courses: true, course_ids: ['*'] } })
    mocks.loginDestination.mockReturnValue('/student/')
    const assign = vi.fn()
    vi.stubGlobal('location', { assign, origin: 'http://localhost:3000' })
    const wrapper = mount(LoginPage)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(mocks.push).toHaveBeenCalledExactlyOnceWith('/student/')
    expect(assign).not.toHaveBeenCalled()
    wrapper.unmount()
  })
  beforeEach(() => { mocks.login.mockReset(); mocks.loginDestination.mockReset(); mocks.push.mockReset() })
  afterEach(() => { vi.unstubAllGlobals() })

  it('guards repeated form submissions and navigates only once after success', async () => {
    let resolve!: (context: object) => void
    mocks.login.mockImplementation(() => new Promise(done => { resolve = done }))
    mocks.loginDestination.mockReturnValue('/student/')
    const assign = vi.fn()
    vi.stubGlobal('location', { assign })
    const wrapper = mount(LoginPage)
    await wrapper.get('input[name="username"]').setValue('S001')
    await wrapper.get('input[name="password"]').setValue('original-password')
    await wrapper.get('form').trigger('submit')
    await wrapper.get('input[name="password"]').setValue('changed-password')
    await wrapper.get('form').trigger('submit')
    expect(mocks.login).toHaveBeenCalledOnce()
    expect(mocks.login).toHaveBeenCalledWith('S001', 'original-password', expect.objectContaining({ signal: expect.any(AbortSignal) }))
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeDefined()
    resolve({ user: { home_url: '/student/' } })
    await flushPromises()
    expect(assign).toHaveBeenCalledExactlyOnceWith('/student/')
    expect(mocks.loginDestination).toHaveBeenCalledOnce()
    wrapper.unmount()
  })

  it('cancels on unmount and ignores a late successful response', async () => {
    let resolve!: (context: object) => void
    mocks.login.mockImplementation(() => new Promise(done => { resolve = done }))
    const assign = vi.fn()
    vi.stubGlobal('location', { assign })
    const wrapper = mount(LoginPage)
    await wrapper.get('form').trigger('submit')
    const signal = mocks.login.mock.calls[0]![2]?.signal as AbortSignal | undefined
    wrapper.unmount()
    expect(signal?.aborted).toBe(true)
    resolve({ user: { home_url: '/student/' } })
    await flushPromises()
    expect(assign).not.toHaveBeenCalled()
    expect(mocks.loginDestination).not.toHaveBeenCalled()
  })

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

    expect(mocks.login).toHaveBeenCalledWith('teacher', 'incorrect', expect.objectContaining({ signal: expect.any(AbortSignal) }))
    expect(wrapper.get('[role="alert"]').text()).toContain('账号或密码不正确。')
  })

  it('shows the credential error returned by the login API', async () => {
    mocks.login.mockRejectedValueOnce(new ApiError(400, {
      error: {
        code: 'VALIDATION_ERROR',
        message: '请求数据无效。',
        fields: { non_field_errors: ['账号或密码不正确。'] },
      },
    }))
    const wrapper = mount(LoginPage)

    await wrapper.get('input[name="username"]').setValue('teacher@example.com')
    await wrapper.get('input[name="password"]').setValue('incorrect')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toBe('账号或密码不正确。')
  })
})
