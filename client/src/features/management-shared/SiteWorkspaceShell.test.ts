import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
vi.mock('@/app/bootstrap', () => ({ getBootstrapContext: () => ({ user: { display_name: '测试管理员', username: 'admin', role: 'admin', home_url: '/manage/' } }) }))
vi.mock('@/app/auth', () => ({ logout: vi.fn() }))
import SiteWorkspaceShell from './SiteWorkspaceShell.vue'

describe('management workspace navigation', () => {
  it('links to six management pages without Site demonstration chrome', () => {
    const wrapper = mount(SiteWorkspaceShell, { props: { mode: 'admin', active: 'course' } })
    expect(wrapper.get('.workspace-sidebar a[aria-current="page"]').attributes('href')).toBe('/manage/courses/')
    expect(wrapper.findAll('.workspace-sidebar .nav-item')).toHaveLength(6)
    expect(wrapper.find('.workspace-bar').exists()).toBe(false)
    expect(wrapper.find('.aside-note').exists()).toBe(false)
    expect(wrapper.find('[data-site-registration-footer]').exists()).toBe(true)
    expect(wrapper.get('.topbar').text()).toContain('测试管理员')
    expect(wrapper.get('.topbar [data-password-change-link]').attributes('href')).toBe('/password/change/')
  })
  it('uses the granted course modules for navigation without exposing admin links to a teacher', () => {
    const wrapper = mount(SiteWorkspaceShell, { props: { mode: 'course', active: 'settings', courseId: 7, courseName: '课程', permissions: ['bank'], showAdmin: false } })
    expect(wrapper.find('.workspace-sidebar a[href="/teacher/courses/7/banks/"]').exists()).toBe(true)
    expect(wrapper.find('.workspace-sidebar a[href="/teacher/courses/7/data/"]').exists()).toBe(false)
    expect(wrapper.find('a[href="/admin/"]').exists()).toBe(false)
    expect(wrapper.get('.workspace-sidebar a[aria-current="page"]').attributes('href')).toBe('/teacher/courses/7/settings/')
  })
})
