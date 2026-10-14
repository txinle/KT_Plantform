import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ api: { overview: vi.fn() }, applyRouteQuery: vi.fn(), route: { params: { courseId: '7' }, query: {} as Record<string, string> } }))
vi.mock('vue-router', async () => { const { reactive } = await import('vue'); const route = reactive(mocks.route); mocks.applyRouteQuery.mockImplementation((query: Record<string, string>) => { route.query = query }); return { useRoute: () => route } })
vi.mock('./api', () => ({ getTeacherDataCenterApi: () => mocks.api, routeQuery: (query: object) => query }))

import TeacherDataOverview from './TeacherDataOverview.vue'

enableAutoUnmount(afterEach)

describe('TeacherDataOverview', () => {
  beforeEach(() => { vi.clearAllMocks(); mocks.applyRouteQuery({}); mocks.api.overview.mockResolvedValue({
    course: { id: 7, name: '研究方法', code: 'KT101', term: '2026 秋', status: 'active' },
    scope: { experiment_id: 3, class_id: 4, is_complete: true, display: '数据实验 · 实验班' },
    facts: { participants: 30, observed_participants: 27, completed_sessions: 20, total_sessions: 25, submitted_attempts: 80, process_records: 416, quality_warnings: 2, not_evaluable: 1 },
    recent_exports: [{ job_id: 'job-1', package: 'experiment', package_label: '标准实验数据包', experiment_name: '数据实验', class_name: '实验班', status: 'succeeded', status_label: '已完成', created_at: '2026-09-09T00:00:00Z' }],
    actions: { scope_update_url: '/scope/', governance_url: '/settings/#identity-governance', teaching_url: '/teacher/courses/7/data/teaching/', preview_url: '/teacher/courses/7/data/advanced/?experiment_id=3&class_id=4', exports_url: '/teacher/courses/7/data/exports/', quality_url: '/teacher/courses/7/data/quality/', history_url: '/teacher/courses/7/data/exports/history/' },
  }) })

  it('refreshes once for a route scope change, aborts old requests and ignores late responses', async () => {
    const resolvers: Array<(value: unknown) => void> = []
    mocks.api.overview.mockImplementation(() => new Promise(resolve => resolvers.push(resolve)))
    const wrapper = mount(TeacherDataOverview)
    expect(mocks.api.overview).toHaveBeenCalledTimes(1)
    const oldSignal = mocks.api.overview.mock.calls[0]?.[2] as AbortSignal
    mocks.applyRouteQuery({ experiment_ids: '5', class_id: '4' })
    await flushPromises()
    expect(mocks.api.overview).toHaveBeenCalledTimes(2)
    expect(oldSignal.aborted).toBe(true)
    resolvers[0]?.({ facts: {} })
    await flushPromises()
    expect(wrapper.text()).toContain('正在加载数据概览')
    wrapper.unmount()
    expect((mocks.api.overview.mock.calls[1]?.[2] as AbortSignal).aborted).toBe(true)
  })

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

  it('shows queued statistics as pending instead of zero during the answer peak', async () => {
    const original = await mocks.api.overview()
    mocks.api.overview.mockResolvedValue({ ...original,
      preparation: { state: 'preparing', message: '数据正在后台整理，完成后可刷新查看。', updated_at: null },
      facts: { ...original.facts, process_records: null, quality_warnings: null, not_evaluable: null },
    })
    const wrapper = mount(TeacherDataOverview)
    await flushPromises()
    expect(wrapper.get('[data-testid="preparation-state"]').text()).toContain('数据正在后台整理')
    expect(wrapper.text()).toContain('待整理')
  })

  it('shows the update time of completed cached statistics', async () => {
    const original = await mocks.api.overview()
    mocks.api.overview.mockResolvedValue({ ...original,
      preparation: { state: 'ready', message: '研究数据已整理完成。', updated_at: '2026-10-14T02:00:00Z' },
    })
    const wrapper = mount(TeacherDataOverview)
    await flushPromises()
    expect(wrapper.get('[data-testid="statistics-updated-at"]').text()).toContain('统计更新')
  })
})
