import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ api: { exports: vi.fn() } }))
vi.mock('vue-router', () => ({ useRoute: () => ({ path: '/teacher/courses/7/data/exports/', params: { courseId: '7' }, query: { experiment_id: '3', version_mode: 'single', version_id: '8', package: 'teaching' } }) }))
vi.mock('./api', () => ({
  getTeacherDataCenterApi: () => mocks.api,
  routeQuery: (query: object) => query,
  queryString: () => '?package=teaching',
}))
vi.mock('@/app/bootstrap', () => ({ getBootstrapContext: () => ({ csrf_token: 'csrf-token' }) }))

import TeacherDataExports from './TeacherDataExports.vue'

describe('TeacherDataExports', () => {
  beforeEach(() => mocks.api.exports.mockResolvedValue({
    course: { id: 7, name: '研究方法', code: 'KT101', term: '2026 秋', status: 'active' },
    scope: { values: { scope_version: 1, experiment_id: 3, version_mode: 'single', version_id: 8 }, query: 'scope_version=1&experiment_id=3&version_mode=single&version_id=8', display: '数据实验 · Version 1', is_single_version: true },
    packages: [{ key: 'teaching', label: '教学成绩（XLSX）', description: '当前有效成绩与导出血缘', blocked: false }],
    selected_package: { key: 'teaching', label: '教学成绩（XLSX）', description: '当前有效成绩与导出血缘', blocked: false },
    export_versions: [{ id: 8, version_number: 1 }],
    selected_export_version: { id: 8, version_number: 1 },
    identified_data_notice: '所有数据包均含实名身份数据。',
    actions: { create_url: '/analytics/courses/7/export-jobs/create/', reset_url: '/teacher/courses/7/data/exports/' },
  }))

  it('requires an explicit document POST with the complete frozen scope', async () => {
    const wrapper = mount(TeacherDataExports)
    await flushPromises()
    expect(wrapper.get('h1').text()).toBe('导出数据包')
    expect(wrapper.text()).toContain('实名身份数据')
    const form = wrapper.get('form[action="/analytics/courses/7/export-jobs/create/"]')
    expect(form.attributes('method')).toBe('post')
    expect(form.get('input[name="csrfmiddlewaretoken"]').attributes('value')).toBe('csrf-token')
    expect(form.get('input[name="confirm_identified"]').attributes('value')).toBe('yes')
    expect(form.get('input[name="version_id"]').attributes('value')).toBe('8')
    expect(form.get('input[name="package"]').attributes('value')).toBe('teaching')
  })
})
