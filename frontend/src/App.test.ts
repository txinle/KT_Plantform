import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import App from './app/App.vue'

describe('App', () => {
  it('provides the Vue module mount boundary', () => {
    const wrapper = mount(App, {
      global: {
        stubs: { RouterView: true },
      },
    })

    expect(wrapper.get('[data-vue-shell]').element.tagName).toBe('DIV')
    expect(wrapper.get('[data-vue-shell]').attributes('aria-label')).toBe('KT Data Lab')
  })
})
