import { mount } from '@vue/test-utils'
import { afterEach, expect, it, vi } from 'vitest'
import QuestionDirectoryPanel from './components/QuestionDirectoryPanel.vue'

afterEach(() => vi.unstubAllGlobals())

it('starts collapsed on mobile and opens locally without replacing its content', async () => {
  const remove = vi.fn()
  vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener: vi.fn(), removeEventListener: remove }))
  const wrapper = mount(QuestionDirectoryPanel, { slots: { default: '<a href="/question/1/">第一题</a>' } })
  expect(wrapper.get('details').element.open).toBe(false)
  const originalLink = wrapper.get('a').element
  wrapper.get('details').element.open = true
  await wrapper.get('details').trigger('toggle')
  expect(wrapper.get('a').element).toBe(originalLink)
  expect(wrapper.get('a').attributes('href')).toBe('/question/1/')
  wrapper.unmount()
  expect(remove).toHaveBeenCalledOnce()
})
