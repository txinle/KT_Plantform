import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ api: { overview: vi.fn() } }))
vi.mock('vue-router', () => ({ useRoute: () => ({ params: { courseId: '7' }, query: {} }) }))
vi.mock('./api', () => ({ getTeacherDataCenterApi: () => mocks.api, routeQuery: () => ({}) }))

import TeacherDataOverview from './TeacherDataOverview.vue'

describe('TeacherDataOverview', () => {
  beforeEach(() => mocks.api.overview.mockResolvedValue({
    course: { id: 7, name: '研究方法', code: 'KT101', term: '2026 秋', status: 'active' },
    scope: { experiment_id: 3, class_id: 4, is_complete: true, display: '数据实验 · 实验班' },
    facts: { participants: 30, observed_participants: 27, completed_sessions: 20, total_sessions: 25, submitted_attempts: 80, process_records: 416, quality_warnings: 2, not_evaluable: 1 },
    recent_exports: [{ job_id: 'job-1', package: 'experiment', package_label: '标准实验数据包', experiment_name: '数据实验', class_name: '实验班', status: 'succeeded', status_label: '已完成', created_at: '2026-09-09T00:00:00Z' }],
    actions: { scope_update_url: '/scope/', governance_url: '/settings/#identity-governance', teaching_url: '/teacher/courses/7/data/teaching/', preview_url: '/teacher/courses/7/data/advanced/?experiment_id=3&class_id=4', exports_url: '/teacher/courses/7/data/exports/', quality_url: '/teacher/courses/7/data/quality/', history_url: '/teacher/courses/7/data/exports/history/' },
  }))

  it('renders readable research facts without exposing table-catalog jargon', async () => {
    const wrapper = mount(TeacherDataOverview)
    await flushPromises()
    expect(wrapper.get('h1').text()).toBe('数据中心')
    expect(wrapper.text()).toContain('30')
    expect(wrapper.text()).toContain('20 / 25')
    expect(wrapper.text()).toContain('80')
    expect(wrapper.text()).toContain('416')
    expect(wrapper.text()).toContain('2')
    expect(wrapper.text()).toContain('标准实验数据包')
    expect(wrapper.text()).toContain('7 张 CSV 与 1 个 JSON')
    expect(wrapper.text()).not.toContain('六张中文表')
    expect(wrapper.text()).toContain('实验班')
    expect(wrapper.text()).not.toContain('assessment_events.csv')
    expect(wrapper.get('a[href="/teacher/courses/7/data/advanced/?experiment_id=3&class_id=4"]')).toBeTruthy()
  })
})
