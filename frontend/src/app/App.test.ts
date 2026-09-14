import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import App from './App.vue'

function context(researchConsentRequired: boolean) {
  return {
    csrf_token: 'csrf-token',
    user: {
      id: 8,
      role: 'student',
      display_name: '测试学生',
      username: 'S001',
      student_number: 'S001',
      home_url: '/student/',
      uses_initial_password: true,
      initial_password_reminder_pending: true,
      research_consent_required: researchConsentRequired,
      research_consent_version: '2026-09-14',
    },
  }
}

describe('App research consent gate', () => {
  it('renders the site registration footer', () => {
    const wrapper = mount(App, {
      props: { bootstrapContext: context(false) },
      global: { stubs: { RouterView: { template: '<div data-route-content />' } } },
    })

    const footer = wrapper.get('[data-site-registration-footer]')
    const icpLink = footer.get('[data-icp-registration]')
    const policeLink = footer.get('[data-police-registration]')

    expect(icpLink.text()).toBe('湘ICP备2026036826号-1')
    expect(icpLink.attributes('href')).toBe('https://beian.miit.gov.cn/')
    expect(policeLink.text()).toContain('湘公网安备43010302002391号')
    expect(policeLink.attributes('href')).toBe(
      'https://beian.mps.gov.cn/#/query/webSearch?code=43010302002391',
    )
    expect(policeLink.get('img').attributes('src')).toBe('/static/icons/beian-police.png')
  })

  it('does not mount the requested route while consent is required', () => {
    const wrapper = mount(App, {
      props: { bootstrapContext: context(true) },
      global: { stubs: { RouterView: { template: '<div data-route-content />' } } },
    })

    expect(wrapper.find('[data-research-consent-gate]').exists()).toBe(true)
    expect(wrapper.find('[data-route-content]').exists()).toBe(false)
  })

  it('mounts the requested route after the consent gate is cleared', async () => {
    const wrapper = mount(App, {
      props: { bootstrapContext: context(true) },
      global: { stubs: { RouterView: { template: '<div data-route-content />' } } },
    })

    await wrapper.getComponent({ name: 'ResearchConsentDialog' }).vm.$emit('accepted')

    expect(wrapper.find('[data-research-consent-gate]').exists()).toBe(false)
    expect(wrapper.find('[data-route-content]').exists()).toBe(true)
  })
})
