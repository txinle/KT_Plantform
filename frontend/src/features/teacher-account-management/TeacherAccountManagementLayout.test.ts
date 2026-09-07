import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

vi.mock('vue-router', () => ({
  RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' },
  RouterView: { template: '<div data-router-view />' },
}))
vi.mock('@/app/bootstrap', () => ({
  getBootstrapContext: () => ({
    csrf_token: 'csrf-token',
    user: { display_name: '账户教师', username: 'account-teacher' },
  }),
}))

import TeacherAccountManagementLayout from './TeacherAccountManagementLayout.vue'

describe('TeacherAccountManagementLayout', () => {
  it('puts global account pages in the shared workspace shell', () => {
    const wrapper = mount(TeacherAccountManagementLayout)

    expect(wrapper.find('#teacher-account-management-main').exists()).toBe(true)
    const navigation = wrapper.get('[aria-label="课程导航"]').text()
    expect(navigation).toContain('我的课程')
    expect(navigation).toContain('班级管理')
    expect(navigation).toContain('学生账号')
  })
})
