import { flushPromises, mount } from '@vue/test-utils'
import { expect, it, vi } from 'vitest'
const auth = vi.hoisted(() => ({ logout: vi.fn() }))
vi.mock('@/app/auth', () => auth)
import AppHeader from './components/AppHeader.vue'

it('reports a rejected logout without losing the current page', async () => {
  auth.logout.mockRejectedValue(new Error('连接失败，请重试'))
  const wrapper = mount(AppHeader, { props: { userName: '教师', showLogout: true, csrfToken: 'csrf' } })
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(auth.logout).toHaveBeenCalledOnce()
  expect(wrapper.get('[role="alert"]').text()).toBe('连接失败，请重试')
  expect(wrapper.get('button').attributes('disabled')).toBeUndefined()
})
