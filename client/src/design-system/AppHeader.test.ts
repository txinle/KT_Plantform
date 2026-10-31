import { mount } from '@vue/test-utils'
import { afterEach, expect, it, vi } from 'vitest'
import AppHeader from './components/AppHeader.vue'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); document.documentElement.style.removeProperty('--kt-header-height') })

it('keeps the reserved header offset equal to its actual wrapped height', () => {
  let resize: ResizeObserverCallback | undefined
  const disconnect = vi.fn()
  vi.stubGlobal('ResizeObserver', class { constructor(callback: ResizeObserverCallback) { resize = callback } observe() {} disconnect = disconnect })
  const size = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ height: 64 } as DOMRect)
  const wrapper = mount(AppHeader, { props: { userName: 'Teacher' } })
  expect(document.documentElement.style.getPropertyValue('--kt-header-height')).toBe('64px')
  size.mockReturnValue({ height: 112 } as DOMRect)
  resize?.([], {} as ResizeObserver)
  expect(document.documentElement.style.getPropertyValue('--kt-header-height')).toBe('112px')
  wrapper.unmount()
  expect(disconnect).toHaveBeenCalledOnce()
})
