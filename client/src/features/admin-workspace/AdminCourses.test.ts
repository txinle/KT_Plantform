import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'
const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('./api', async (original) => ({ ...await original<typeof import('./api')>(), getAdminApi: () => mocks }))
import AdminCourses from './AdminCourses.vue'
import AdminClassDialog from './AdminClassDialog.vue'

describe('admin class links', () => {
  it('opens an actual class from the directory query and follows changed class links', async () => {
    const classes = [{ id: 4, course_id: 7, name: '甲班' }, { id: 5, course_id: 7, name: '乙班' }]
    mocks.get.mockImplementation((path: string) => Promise.resolve({ items: path === 'classes/' ? classes : [] }))
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/manage/courses/', component: AdminCourses }] })
    await router.push('/manage/courses/?tab=classes&class_id=4')
    const wrapper = mount(AdminCourses, { global: { plugins: [router], stubs: { AdminClassDialog: true, SiteDialog: true } } })
    await flushPromises()
    expect(wrapper.get('.tabs .active').text()).toContain('班级目录')
    expect(wrapper.findComponent(AdminClassDialog).props('row')).toEqual(classes[0])
    await router.push('/manage/courses/?tab=classes&class_id=5'); await flushPromises()
    expect(wrapper.findComponent(AdminClassDialog).props('row')).toEqual(classes[1])
    wrapper.findComponent(AdminClassDialog).vm.$emit('close'); await flushPromises()
    expect(wrapper.findComponent(AdminClassDialog).props('row')).toBeUndefined()
  })
})
