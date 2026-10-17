import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, routeLocationKey } from 'vue-router'

import App from './App.vue'
import LoginPage from './LoginPage.vue'
import * as authentication from './auth'
import { acceptBootstrapContext, clearBootstrapContext } from './bootstrap'
import { afterEach } from 'vitest'

const routeProvider = { [routeLocationKey as symbol]: { path: '/student/' } }

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
  afterEach(() => clearBootstrapContext())

  it('finishes login navigation before gating the destination without mounting its protected content', async () => {
    clearBootstrapContext()
    const protectedSetup = vi.fn(() => ({}))
    const router = createRouter({ history: createMemoryHistory(), routes: [
      { path: '/login/', component: LoginPage },
      { path: '/student/', component: { setup: protectedSetup, template: '<div data-protected-content />' } },
    ] })
    await router.push('/login/')
    await router.isReady()
    const login = vi.spyOn(authentication, 'login').mockImplementationOnce(async () => acceptBootstrapContext({
      ...context(true), client_navigation: { student_courses: true, course_ids: ['*'] },
    }))
    const wrapper = mount(App, { global: { plugins: [router],
      stubs: { ResearchConsentDialog: { template: '<div data-research-consent-gate />' } } } })
    try {
      await wrapper.get('form').trigger('submit')
      await flushPromises()
      expect(router.currentRoute.value.path).toBe('/student/')
      expect(wrapper.find('[data-research-consent-gate]').exists()).toBe(true)
      expect(wrapper.find('[data-protected-content]').exists()).toBe(false)
      expect(protectedSetup).not.toHaveBeenCalled()
    } finally {
      wrapper.unmount()
      login.mockRestore()
    }
  })

  it('rechecks consent when a new student logs in after the app mounted anonymously', async () => {
    clearBootstrapContext()
    const wrapper = mount(App, { global: { provide: routeProvider, stubs: { RouterView: { template: '<div data-route-content />' } } } })
    acceptBootstrapContext(context(true))
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-research-consent-gate]').exists()).toBe(true)
    expect(wrapper.find('[data-route-content]').exists()).toBe(false)
    acceptBootstrapContext(context(false))
    await wrapper.vm.$nextTick()
    acceptBootstrapContext({ ...context(true), user: { ...context(true).user, id: 9 } })
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-research-consent-gate]').exists()).toBe(true)
    wrapper.unmount()
  })
  it('renders the site registration footer', () => {
    acceptBootstrapContext(context(false))
    const wrapper = mount(App, {
      global: { provide: routeProvider, stubs: { RouterView: { template: '<div data-route-content />' } } },
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
    expect(policeLink.get('img').attributes('src')).toBe('/static/spa-assets/icons/beian-police.png')
  })

  it('does not mount the requested route while consent is required', () => {
    acceptBootstrapContext(context(true))
    const wrapper = mount(App, {
      global: { provide: routeProvider, stubs: { RouterView: { template: '<div data-route-content />' } } },
    })

    expect(wrapper.find('[data-research-consent-gate]').exists()).toBe(true)
    expect(wrapper.find('[data-route-content]').exists()).toBe(false)
  })

  it('mounts the requested route after the consent gate is cleared', async () => {
    acceptBootstrapContext(context(true))
    const wrapper = mount(App, {
      global: { provide: routeProvider, stubs: { RouterView: { template: '<div data-route-content />' } } },
    })

    acceptBootstrapContext(context(false))
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[data-research-consent-gate]').exists()).toBe(false)
    expect(wrapper.find('[data-route-content]').exists()).toBe(true)
  })
})
