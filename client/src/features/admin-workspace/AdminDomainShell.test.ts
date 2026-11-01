import { mount, flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
const mocks = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('./api', () => ({ getAdminApi: () => mocks }))
vi.mock('vue-router', () => ({ useRoute: () => ({ path: '/manage/resources/banks/1/' }) }))
import AdminDomainShell from './AdminDomainShell.vue'
const global = { stubs: { SiteWorkspaceShell: { template: '<div><slot /></div>' } } }
describe('administrator reused-domain entry', () => {
  beforeEach(() => vi.resetAllMocks())
  it('mounts domain content only after server administrator authorization', async () => {
    let resolve!: (value: unknown) => void
    mocks.get.mockReturnValue(new Promise(r => { resolve = r }))
    const wrapper = mount(AdminDomainShell, { props: { active: 'bank' }, global, slots: { default: '<p data-domain>管理表单</p>' } })
    expect(wrapper.find('[data-domain]').exists()).toBe(false)
    resolve({ allowed: true }); await flushPromises()
    expect(mocks.get).toHaveBeenCalledWith('access/')
    expect(wrapper.find('[data-domain]').exists()).toBe(true)
  })
  it('keeps teacher-only sessions outside the administrator domain', async () => {
    mocks.get.mockRejectedValue(new Error('没有管理员权限'))
    const wrapper = mount(AdminDomainShell, { props: { active: 'bank' }, global, slots: { default: '<p data-domain>管理表单</p>' } })
    await flushPromises()
    expect(wrapper.find('[data-domain]').exists()).toBe(false)
    expect(wrapper.get('[role="alert"]').text()).toContain('没有管理员权限')
  })
})
