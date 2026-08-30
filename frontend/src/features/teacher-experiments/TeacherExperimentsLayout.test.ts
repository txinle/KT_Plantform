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
  it('renders the compact experiment shell with seven real navigation destinations', async () => {
    mocks.get.mockResolvedValue({ id: 7, name: '研究方法', code: 'RM101', term: '2026 秋' })

    const wrapper = mount(TeacherExperimentsLayout)
    await flushPromises()

    const links = wrapper.get('[aria-label="全局导航"]').findAll('a')
    expect(links.map((link) => link.text())).toEqual(['我的课程', '概览', '题库', '班级', '实验', '数据', '设置'])
    expect(wrapper.get('header.kt-experiment-shell-header').text()).toContain('KT Data Lab')
    expect(wrapper.get('.kt-experiment-shell-header [aria-current="page"]').text()).toBe('实验')
    expect(wrapper.find('.kt-course-bar').exists()).toBe(false)
    expect(wrapper.get('.kt-experiment-header-user').text()).toContain('李')
    expect(wrapper.find('.kt-teacher-experiments').exists()).toBe(true)

    expect(styles).not.toMatch(/(?:^|[\n,])\s*\.kt-(?!teacher-experiments\b)/)
  })
})
