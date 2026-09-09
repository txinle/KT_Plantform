import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ api: { history: vi.fn(), removeExport: vi.fn() }, push: vi.fn() }))
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { courseId: '7' }, query: { experiment_id: '3', class_id: '4', search: '张同学', session_ref: 'S-001' } }),
  useRouter: () => ({ push: mocks.push }),
}))
vi.mock('./api', () => ({ getTeacherDataCenterApi: () => mocks.api, routeQuery: (query: object) => query }))

import TeacherDataExportHistory from './TeacherDataExportHistory.vue'

function collection(removalLabel = '') {
  return {
    course: { id: 7, name: '研究方法', code: 'KT101', term: '2026 秋', status: 'active' },
    scope: { values: { experiment_id: 3, class_id: 4, search: '张同学', session_ref: 'S-001' }, query: '', display: '数据实验 · 实验班', is_single_version: false, is_complete: true },
    items: [{
      job_id: 'job-1', package: 'experiment', package_label: '标准实验数据包（ZIP）', experiment_name: '数据实验', class_name: '实验班', status: removalLabel ? 'invalidated' : 'succeeded', status_label: removalLabel ? '已移除文件' : '已完成',
      artifact_removal_reason: removalLabel ? 'user_requested_removal' : '', artifact_removal_label: removalLabel, created_by_name: '教师', created_at: '2026-09-09T00:00:00Z',
      download_url: removalLabel ? null : '/analytics/courses/7/export-jobs/job-1/download/',
      delete_url: removalLabel ? null : '/api/v1/teacher/courses/7/data/export-jobs/job-1/',
    }],
    page: { number: 1, size: 20, total_items: 1, total_pages: 1, start: 1, end: 1 },
  }
}

describe('TeacherDataExportHistory', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.api.history.mockResolvedValueOnce(collection()).mockResolvedValueOnce(collection('用户主动移除'))
    mocks.api.removeExport.mockResolvedValue({
      job_id: 'job-1', package: 'experiment', status: 'invalidated', status_label: '已移除文件',
      download_url: null, history_url: '/teacher/courses/7/data/exports/history/',
    })
  })

  it('loads the scoped history and renders Chinese package, class, and removal labels', async () => {
    const wrapper = mount(TeacherDataExportHistory)
    await flushPromises()

    expect(mocks.api.history).toHaveBeenCalledWith(7, { experiment_id: '3', class_id: '4', search: '张同学', session_ref: 'S-001', page: 1 })
    expect(wrapper.text()).toContain('标准实验数据包（ZIP）')
    expect(wrapper.text()).toContain('实验班')
    expect(wrapper.text()).not.toContain('experiment')

    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(mocks.api.removeExport).toHaveBeenCalledWith('/api/v1/teacher/courses/7/data/export-jobs/job-1/')
    expect(mocks.api.history).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('用户主动移除')
    expect(wrapper.text()).not.toContain('user_requested_removal')
  })
})
