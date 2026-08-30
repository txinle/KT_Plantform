import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  api: {
    detail: vi.fn(),
    readiness: vi.fn(),
    classTasks: vi.fn(),
    progress: vi.fn(),
    startPlan: vi.fn(),
    reviseSchedule: vi.fn(),
    cancel: vi.fn(),
    manualClose: vi.fn(),
  },
  push: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { courseId: '7', experimentId: '11' } }),
  useRouter: () => ({ push: mocks.push }),
}))

vi.mock('./api', () => ({ getTeacherExperimentApi: () => mocks.api }))

import TeacherExperimentDetail from './TeacherExperimentDetail.vue'

describe('TeacherExperimentDetail', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.api.detail.mockResolvedValue({
      id: 11,
      name: '认知诊断实验',
      description: '比较两种反馈策略',
      question_bank: { id: 3, name: '代数题库' },
      status: 'draft',
      status_label: '草稿',
      selection_mode_label: '全量题池',
      ordering_mode_label: '固定顺序',
      feedback_mode_label: '仅显示已提交',
      time_mode_label: '无时限',
      total_time_limit_minutes: 45,
      per_question_time_limit_seconds: 90,
      resume_mode_label: '允许断点续答',
      confidence_mode_label: '不采集',
      can_edit: true,
      updated_at: '2026-08-30T00:00:00Z',
    })
    mocks.api.readiness.mockResolvedValue({ ready: true, issues: [], candidate_count: 12, participant_count: 0 })
    mocks.api.classTasks.mockResolvedValue({ items: [] })
    mocks.api.progress.mockResolvedValue({ total_participants: 0, not_started: 0, in_progress: 0, completed: 0 })
    mocks.api.startPlan.mockResolvedValue({ id: 23 })
  })

  it('renders authoritative readiness and resumes the server publish plan', async () => {
    const wrapper = mount(TeacherExperimentDetail)
    await flushPromises()

    expect(wrapper.text()).toContain('认知诊断实验')
    expect(wrapper.text()).toContain('12 道可用题目')
    expect(wrapper.find('.kt-experiment-page-header [data-action="publish"]').exists()).toBe(false)
    await wrapper.get('[data-action="publish-from-definition"]').trigger('click')
    await flushPromises()

    expect(mocks.api.startPlan).toHaveBeenCalledWith(7, 11)
    expect(mocks.push).toHaveBeenCalledWith('/teacher/courses/7/experiments/11/publish-plan/23/step/1/')
  })

  it('uses local definition, publishing, and collection tabs for a draft experiment', async () => {
    const wrapper = mount(TeacherExperimentDetail)
    await flushPromises()

    const tabs = wrapper.get('.kt-experiment-tabs').findAll('[role="tab"]')
    expect(tabs.map((tab) => tab.text())).toEqual(['实验定义', '班级发布', '采集状态'])
    expect(wrapper.find('.kt-experiment-draft-grid').exists()).toBe(true)
    expect(tabs[0]?.attributes('aria-selected')).toBe('true')
    expect(wrapper.findAll('[data-summary-icon]')).toHaveLength(8)
    expect(wrapper.get('.kt-definition-summary').text()).not.toContain('时限模式')
    expect(wrapper.get('.kt-definition-summary').text()).toContain('总限时')
    expect(wrapper.get('.kt-definition-summary').text()).toContain('45 分钟')
    expect(wrapper.get('.kt-definition-summary').text()).toContain('单题限时')
    expect(wrapper.get('.kt-definition-summary').text()).toContain('90 秒')
    expect(wrapper.find('.kt-definition-summary__action').exists()).toBe(true)
    expect(wrapper.find('.kt-publish-empty-illustration').exists()).toBe(true)
    expect(wrapper.findAll('.kt-readiness-checklist li').length).toBeGreaterThanOrEqual(3)
  })

  it('renders server-authorized task actions in the published task table', async () => {
    mocks.api.detail.mockResolvedValue({
      id: 11,
      name: '认知诊断实验',
      description: '比较两种反馈策略',
      question_bank: { id: 3, name: '代数题库' },
      status: 'published',
      status_label: '已发布',
      selection_mode_label: '全量题池',
      ordering_mode_label: '固定顺序',
      feedback_mode_label: '仅显示已提交',
      time_mode_label: '无时限',
      resume_mode_label: '允许断点续答',
      confidence_mode_label: '不采集',
      can_edit: false,
      updated_at: '2026-08-30T00:00:00Z',
    })
    mocks.api.classTasks.mockResolvedValue({
      items: [{
        id: 31,
        class_id: 5,
        class_name: '实验班',
        participant_count: 36,
        opens_at: '2026-09-01T00:00:00Z',
        closes_at: '2026-09-03T00:00:00Z',
        business_status: '采集中',
        available_actions: ['revise_schedule', 'manual_close'],
      }],
    })
    mocks.api.progress.mockResolvedValue({ total_participants: 36, not_started: 11, in_progress: 8, completed: 17 })

    const wrapper = mount(TeacherExperimentDetail)
    await flushPromises()

    expect(wrapper.get('[role="tab"][aria-selected="true"]').text()).toBe('班级发布')
    expect(wrapper.get('.kt-published-task-table').text()).toContain('实验班')
    expect(wrapper.get('[data-action="revise-schedule"]').text()).toBe('修订排期')
    expect(wrapper.get('[data-action="manual-close"]').text()).toBe('提前结束')
  })
})
