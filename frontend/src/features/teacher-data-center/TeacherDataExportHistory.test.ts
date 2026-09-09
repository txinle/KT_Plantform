import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ api: { history: vi.fn(), removeExport: vi.fn() } }))
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { courseId: '7' }, query: {} }),
  useRouter: () => ({ push: vi.fn() }),
}))
vi.mock('./api', () => ({ getTeacherDataCenterApi: () => mocks.api }))
vi.mock('@/app/bootstrap', () => ({ getBootstrapContext: () => ({ csrf_token: 'csrf' }) }))

import TeacherDataExportHistory from './TeacherDataExportHistory.vue'

describe('TeacherDataExportHistory', () => {
  beforeEach(() => {
    mocks.api.history.mockResolvedValue({
      course: { id: 7, name: '研究方法', code: 'KT101', term: '2026 秋', status: 'active' },
      items: [{
        job_id: 'job-1', package: 'complete', experiment_name: '实验', status: 'succeeded', status_label: '已完成',
        artifact_removal_reason: '', created_by_name: '教师', created_at: '2026-09-09T00:00:00Z',
        download_url: '/analytics/courses/7/export-jobs/job-1/download/',
        delete_url: '/api/v1/teacher/courses/7/data/export-jobs/job-1/',
      }],
      page: { number: 1, size: 20, total_items: 1, total_pages: 1, start: 1, end: 1 },
    })
    mocks.api.removeExport.mockResolvedValue({
      job_id: 'job-1', package: 'complete', status: 'invalidated', status_label: '已失效',
      download_url: null, history_url: '/teacher/courses/7/data/exports/history/',
    })
  })

  it('removes an artifact through JSON and updates the authoritative row', async () => {
    const wrapper = mount(TeacherDataExportHistory)
    await flushPromises()
    await wrapper.get('button').trigger('click')
    await flushPromises()

    expect(mocks.api.removeExport).toHaveBeenCalledWith('/api/v1/teacher/courses/7/data/export-jobs/job-1/')
    expect(wrapper.text()).toContain('已失效')
    expect(wrapper.find('a[href="/analytics/courses/7/export-jobs/job-1/download/"]').exists()).toBe(false)
  })
})
