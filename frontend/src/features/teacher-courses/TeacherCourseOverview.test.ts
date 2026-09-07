import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ api: { detail: vi.fn() } }))

vi.mock('vue-router', () => ({
  RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' },
  useRoute: () => ({ params: { courseId: '7' } }),
}))
vi.mock('@/app/bootstrap', () => ({
  getBootstrapContext: () => ({ csrf_token: 'csrf-token', user: { display_name: '李老师', username: 'teacher' } }),
}))
vi.mock('./api', () => ({ getTeacherCourseApi: () => mocks.api }))

import TeacherCourseOverview from './TeacherCourseOverview.vue'

function payload(isCourseOwner = true) {
  return {
    course: {
      id: 7, name: '研究方法', code: 'RM101', term: '2026 秋', description: '研究设计课程', owner_name: '李老师',
      status: 'active', status_label: '进行中', updated_at: '2026-09-06T10:00:00Z',
      banks_url: '/teacher/courses/7/banks/', classes_url: '/teacher/courses/7/classes/', experiments_url: '/teacher/courses/7/experiments/', data_url: '/teacher/courses/7/data/', settings_url: '/teacher/courses/7/settings/', detail_url: '/teacher/courses/7/',
    },
    metrics: { class_count: 2, bank_count: 3, experiment_count: 4, student_count: 40 },
    recent_experiments: [{ id: 8, name: '反馈实验', question_bank_name: '代数题库', status: 'published', status_label: '已发布', detail_url: '/teacher/courses/7/experiments/8/' }],
    recent_banks: [{ id: 9, name: '代数题库', description: '覆盖基础题', question_count: 12, visibility: 'private', visibility_label: '私有', updated_at: '2026-09-06T10:00:00Z', detail_url: '/teacher/courses/7/banks/9/' }],
    class_center_enabled: true,
    is_course_owner: isCourseOwner,
  }
}

describe('TeacherCourseOverview', () => {
  beforeEach(() => { vi.clearAllMocks(); mocks.api.detail.mockResolvedValue(payload()) })

  it('renders server-projected course metrics and recent resources in the course workspace', async () => {
    const wrapper = mount(TeacherCourseOverview)
    await flushPromises()

    expect(wrapper.get('.kt-workspace-context').text()).toContain('RM101 · 2026 秋')
    expect(wrapper.get('h1').text()).toBe('研究方法')
    expect(wrapper.get('[aria-label="课程资源概况"]').text()).toContain('40')
    expect(wrapper.text()).toContain('反馈实验')
    expect(wrapper.text()).toContain('代数题库')
    expect(wrapper.get('[data-course-settings-action]').text()).toContain('课程设置')
  })

  it('does not infer settings access when the server omits the owner capability', async () => {
    mocks.api.detail.mockResolvedValue(payload(false))
    const wrapper = mount(TeacherCourseOverview)
    await flushPromises()

    expect(wrapper.find('[data-course-settings-action]').exists()).toBe(false)
  })
})
