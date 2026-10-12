import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

vi.mock('vue-router', () => ({
  RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' },
  RouterView: { template: '<div data-router-view />' },
  useRoute: () => ({ params: { courseId: '7' } }),
}))
vi.mock('@/app/bootstrap', () => ({
  getBootstrapContext: () => ({
    csrf_token: 'csrf-token',
    user: { display_name: '题库教师', username: 'bank-teacher' },
  }),
}))

import TeacherQuestionBanksLayout from './TeacherQuestionBanksLayout.vue'

describe('TeacherQuestionBanksLayout', () => {
  it('keeps every existing teacher course destination in the workspace sidebar', () => {
    const wrapper = mount(TeacherQuestionBanksLayout)

    const navigation = wrapper.get('[aria-label="课程导航"]').element
    const links = Array.from(navigation.children).filter((item): item is HTMLAnchorElement => item instanceof HTMLAnchorElement)
    expect(links.map((link) => link.textContent?.trim())).toEqual(['我的课程', '概览', '题库', '班级', '实验', '数据', '设置'])
    expect(wrapper.get('.kt-workspace-sidebar [aria-current="page"]').text()).toBe('题库')
    expect(wrapper.find('main#question-bank-main').exists()).toBe(true)
  })
})
