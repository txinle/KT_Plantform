import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ get: vi.fn() }))

vi.mock('vue-router', () => ({
  RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' },
  RouterView: { template: '<div data-router-view />' },
  useRoute: () => ({ params: { courseId: '7' } }),
}))
vi.mock('@/api/client', () => ({ createApiClient: () => ({ get: mocks.get }) }))
vi.mock('@/app/bootstrap', () => ({
  getBootstrapContext: () => ({
    csrf_token: 'csrf-token',
    user: { display_name: '班级教师', username: 'class-teacher' },
  }),
}))

import TeacherClassesLayout from './TeacherClassesLayout.vue'

describe('TeacherClassesLayout', () => {
  it('shows a denied context without an unhandled rejection or empty content', async () => {
    mocks.get.mockRejectedValue(new Error('无权访问此课程'))
    const wrapper = mount(TeacherClassesLayout)
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('无权访问此课程')
    expect(wrapper.find('[data-router-view]').exists()).toBe(false)
  })
  it('keeps classes active and includes the existing settings destination', async () => {
    mocks.get.mockResolvedValue({ id: 7, name: '研究方法', code: 'KT101', term: '2026 秋' })
    const wrapper = mount(TeacherClassesLayout)
    await flushPromises()

    const navigation = wrapper.get('[aria-label="课程导航"]').element
    const links = Array.from(navigation.children).filter((item): item is HTMLAnchorElement => item instanceof HTMLAnchorElement)
    expect(links.map((link) => link.textContent?.trim())).toEqual(['我的课程', '概览', '题库', '班级', '实验', '数据', '设置'])
    expect(wrapper.get('.kt-workspace-sidebar [aria-current="page"]').text()).toBe('班级')
    expect(wrapper.get('.kt-workspace-context').text()).toContain('KT101 · 2026 秋')
  })
})
