import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { defineComponent } from 'vue'
import WorkspaceShell from './components/WorkspaceShell.vue'
import SidebarSection from './components/SidebarSection.vue'

afterEach(() => { document.body.innerHTML = ''; vi.unstubAllGlobals() })

describe('workspace navigation', () => {
  it('places secondary controls immediately after the current primary link in DOM order', async () => {
    const wrapper = mount(WorkspaceShell, {
      attachTo: document.body,
      props: { userName: '教师', csrfToken: 'token', mainId: 'content' },
      slots: {
        navigation: '<a href="/overview/">概览</a><a href="/experiments/" aria-current="page">实验</a><a href="/data/">数据</a>',
        default: defineComponent({ components: { SidebarSection }, template: '<SidebarSection><nav><button>实验定义</button></nav></SidebarSection>' }),
      },
    })
    await flushPromises()
    expect(wrapper.findAll('.kt-workspace-navigation a, .kt-workspace-navigation button').map(item => item.text())).toEqual(['概览', '实验', '实验定义', '数据'])
    wrapper.unmount()
  })

  it('keeps secondary controls live in the sidebar and preserves logout CSRF', async () => {
    const clicked = vi.fn()
    const wrapper = mount(WorkspaceShell, {
      attachTo: document.body,
      props: { userName: '教师甲', csrfToken: 'token', mainId: 'content' },
      slots: { navigation: '<a href="/teacher/courses/">我的课程</a>', default: defineComponent({
        components: { SidebarSection }, methods: { clicked },
        template: '<SidebarSection><nav><button @click="clicked">实验定义</button></nav></SidebarSection><p>正文</p>',
      }) },
    })
    await flushPromises()
    expect(wrapper.get('aside').text()).toContain('我的课程')
    await wrapper.get('#workspace-secondary-nav button').trigger('click')
    expect(clicked).toHaveBeenCalledOnce()
    expect(wrapper.get('main').text()).toBe('正文')
    expect(wrapper.get('input[name="csrfmiddlewaretoken"]').attributes('value')).toBe('token')
    wrapper.unmount()
  })

  it('opens the mobile drawer, traps focus, and restores it on Escape', async () => {
    vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }))
    const wrapper = mount(WorkspaceShell, { attachTo: document.body,
      props: { userName: '学生甲', csrfToken: 'token', mainId: 'content' },
      slots: { navigation: '<a href="/student/">我的课程</a>', default: '<input aria-label="正文输入" />' },
    })
    await flushPromises()
    const trigger = wrapper.get('[data-workspace-menu]')
    await trigger.trigger('click')
    expect(wrapper.get('aside').attributes('aria-modal')).toBe('true')
    expect(wrapper.get('main').attributes()).toHaveProperty('inert')
    const lastLink = wrapper.get('aside a')
    ;(lastLink.element as HTMLElement).focus()
    await lastLink.trigger('keydown', { key: 'Tab' })
    expect(document.activeElement).toBe(wrapper.get('[data-workspace-close]').element)
    await wrapper.get('aside').trigger('keydown', { key: 'Escape' })
    await flushPromises()
    expect(trigger.attributes('aria-expanded')).toBe('false')
    expect(document.activeElement).toBe(trigger.element)
    wrapper.unmount()
  })
})
