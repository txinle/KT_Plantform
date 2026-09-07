import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ api: { settings: vi.fn(), saveSettings: vi.fn() } }))

vi.mock('vue-router', () => ({
  RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' },
  useRoute: () => ({ params: { courseId: '7' } }),
}))
vi.mock('@/app/bootstrap', () => ({
  getBootstrapContext: () => ({ csrf_token: 'csrf-token', user: { display_name: '李老师', username: 'teacher' } }),
}))
vi.mock('./api', () => ({ getTeacherCourseApi: () => mocks.api }))

import TeacherCourseSettings from './TeacherCourseSettings.vue'

function payload() {
  return {
    course: {
      id: 7, name: '研究方法', code: 'RM101', term: '2026 秋', description: '研究设计课程', status: 'active', status_label: '进行中',
    },
    form: {
      name: '研究方法', code: 'RM101', term: '2026 秋', description: '研究设计课程', state_token: 'state-1', teachers: [2],
      teacher_options: [{ id: 2, display_name: '李老师', username: 'teacher' }, { id: 3, display_name: '王老师', username: 'collaborator' }],
    },
    archive_url: '/teacher/courses/7/archive/',
    purge_request_create_url: '/analytics/courses/7/purge-requests/create/',
    purge_requests: [{ purge_request_id: 'purge-1', preview_session_count: 2, preview_event_count: 5, status: 'preview', status_label: '预览完成', execute_url: '/analytics/courses/7/purge-requests/purge-1/execute/' }],
    capabilities: { can_update: true, can_archive: true },
  }
}

describe('TeacherCourseSettings', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.api.settings.mockResolvedValue(payload())
    mocks.api.saveSettings.mockResolvedValue(payload())
  })

  it('saves the server-provided state token and teacher selection without changing normal form actions', async () => {
    const wrapper = mount(TeacherCourseSettings)
    await flushPromises()
    await wrapper.get('input[name="name"]').setValue('高级研究方法')
    await wrapper.get('input[value="3"]').setValue(true)
    await wrapper.get('form[data-course-settings]').trigger('submit')
    await flushPromises()

    expect(mocks.api.saveSettings).toHaveBeenCalledWith(7, {
      name: '高级研究方法', code: 'RM101', term: '2026 秋', description: '研究设计课程', state_token: 'state-1', teachers: [2, 3],
    })
    expect(wrapper.text()).toContain('课程设置已保存。')
    expect(wrapper.get('form[action="/teacher/courses/7/archive/"] input[name="csrfmiddlewaretoken"]').attributes('value')).toBe('csrf-token')
    expect(wrapper.get('form[action="/analytics/courses/7/purge-requests/create/"] input[name="confirmation"]').attributes('value')).toBe('PURGE')
    expect(wrapper.get('form[action="/analytics/courses/7/purge-requests/purge-1/execute/"]')).toBeTruthy()
  })

  it('uses server capabilities for update and archive controls', async () => {
    mocks.api.settings.mockResolvedValue({ ...payload(), capabilities: { can_update: false, can_archive: false } })
    const wrapper = mount(TeacherCourseSettings)
    await flushPromises()

    expect(wrapper.get('[data-course-settings] button[type="submit"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('form[action="/teacher/courses/7/archive/"]').exists()).toBe(false)
  })
})
