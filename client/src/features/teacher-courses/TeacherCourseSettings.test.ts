import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  api: {
    settings: vi.fn(),
    saveSettings: vi.fn(),
    archive: vi.fn(),
    createPurgeRequest: vi.fn(),
    executePurgeRequest: vi.fn(),
  },
}))

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
    archive_url: '/api/v1/teacher/courses/7/archive/',
    purge_request_create_url: '/api/v1/teacher/courses/7/purge-requests/',
    purge_requests: [{ purge_request_id: 'purge-1', preview_session_count: 2, preview_event_count: 5, status: 'preview', status_label: '预览完成', execute_url: '/api/v1/teacher/courses/7/purge-requests/purge-1/execute/' }],
    capabilities: { can_update: true, can_archive: true },
  }
}

describe('TeacherCourseSettings', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.api.settings.mockResolvedValue(payload())
    mocks.api.saveSettings.mockResolvedValue(payload())
    mocks.api.archive.mockResolvedValue({ ...payload(), course: { ...payload().course, status: 'archived', status_label: '已归档' }, capabilities: { can_update: false, can_archive: false } })
    mocks.api.createPurgeRequest.mockResolvedValue(payload())
    mocks.api.executePurgeRequest.mockResolvedValue({ ...payload(), purge_requests: [{ ...payload().purge_requests[0], status: 'executed', status_label: '已执行', execute_url: null }] })
  })

  it('saves the server-provided state token and runs governance writes through JSON APIs', async () => {
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

    await wrapper.get('[data-course-archive]').trigger('click')
    await flushPromises()
    expect(mocks.api.archive).toHaveBeenCalledWith(7)

    await wrapper.get('select[name="reason_code"]').setValue('collection_error')
    await wrapper.get('[data-purge-create]').trigger('click')
    await flushPromises()
    expect(mocks.api.createPurgeRequest).toHaveBeenCalledWith(7, 'collection_error')

    await wrapper.get('[data-purge-execute="purge-1"]').trigger('click')
    await flushPromises()
    expect(mocks.api.executePurgeRequest).toHaveBeenCalledWith(7, 'purge-1')
    expect(wrapper.find('.kt-teacher-course-danger form, .kt-teacher-course-governance form, .kt-teacher-course-purge form').exists()).toBe(false)
  })

  it('uses server capabilities for update and archive controls', async () => {
    mocks.api.settings.mockResolvedValue({ ...payload(), capabilities: { can_update: false, can_archive: false } })
    const wrapper = mount(TeacherCourseSettings)
    await flushPromises()

    expect(wrapper.get('[data-course-settings] button[type="submit"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-course-archive]').exists()).toBe(false)
  })
})
