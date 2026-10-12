import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import styles from './teacher-experiments.css?raw'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
}))

vi.mock('vue-router', () => ({
  RouterLink: { template: '<a><slot /></a>' },
  RouterView: { template: '<div />' },
  useRoute: () => ({ params: { courseId: '7' } }),
}))

vi.mock('@/api/client', () => ({
  createApiClient: () => ({ get: mocks.get }),
}))

vi.mock('@/app/bootstrap', () => ({
  getBootstrapContext: () => ({
    csrf_token: 'csrf-token',
    user: { display_name: '李老师' },
  }),
}))

import TeacherExperimentsLayout from './TeacherExperimentsLayout.vue'

describe('TeacherExperimentsLayout', () => {
  it('renders teacher course navigation in the persistent workspace sidebar', async () => {
    mocks.get.mockResolvedValue({ id: 7, name: '研究方法', code: 'RM101', term: '2026 秋' })

    const wrapper = mount(TeacherExperimentsLayout)
    await flushPromises()

    const navigation = wrapper.get('[aria-label="课程导航"]').element
    const links = Array.from(navigation.children).filter((item): item is HTMLAnchorElement => item instanceof HTMLAnchorElement)
    expect(links.map((link) => link.textContent?.trim())).toEqual(['我的课程', '概览', '题库', '班级', '实验', '数据', '设置'])
    expect(wrapper.get('header.kt-app-header').text()).toContain('KT Data Lab')
    expect(wrapper.get('.kt-workspace-sidebar [aria-current="page"]').text()).toBe('实验')
    expect(wrapper.get('.kt-workspace-context').text()).toContain('研究方法')
    expect(wrapper.get('.kt-workspace-context').text()).toContain('RM101 · 2026 秋')
    expect(wrapper.get('.kt-user-name').text()).toContain('李老师')
    expect(wrapper.get('form[data-logout] button').text()).toBe('退出')
    expect(wrapper.find('.kt-workspace').exists()).toBe(true)

    expect(styles).not.toMatch(/(?:^|[\n,])\s*\.kt-(?!teacher-experiments\b)/)
  })
})
