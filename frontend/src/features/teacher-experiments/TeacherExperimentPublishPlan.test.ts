import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  api: {
    plan: vi.fn(),
    detail: vi.fn(),
    list: vi.fn(),
    participants: vi.fn(),
    saveSetup: vi.fn(),
    preview: vi.fn(),
    review: vi.fn(),
    commit: vi.fn(),
    participantExportUrl: vi.fn(() => '/api/v1/teacher/publish-plans/23/participants/export/'),
  },
  push: vi.fn(),
  route: { params: { courseId: '7', experimentId: '11', planId: '23', step: '1' } },
}))

vi.mock('vue-router', () => ({
  useRoute: () => mocks.route,
  useRouter: () => ({ push: mocks.push }),
}))
vi.mock('./api', () => ({ getTeacherExperimentApi: () => mocks.api }))

import TeacherExperimentPublishPlan from './TeacherExperimentPublishPlan.vue'

describe('TeacherExperimentPublishPlan', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.route.params.step = '1'
    mocks.api.plan.mockResolvedValue({
      id: 23,
      experiment_id: 11,
      status: 'draft',
      current_step: 1,
      setup_status: 'empty',
      missing_requirements: ['classes'],
      field_errors: {},
      plan_revision: 1,
      expires_at: '2026-08-31T00:00:00Z',
      default_opens_at: null,
      default_closes_at: null,
      classes: [],
    })
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
    mocks.api.list.mockResolvedValue({
      items: [],
      page: { number: 1, size: 20, total_items: 0, total_pages: 1, start: 0, end: 0 },
      summary: { question_banks: [], classes: [{ id: 5, name: '一班', academic_year: '2026', student_count: 36 }] },
    })
    mocks.api.participants.mockResolvedValue({ items: [], page: { number: 1, size: 20, total_items: 0, total_pages: 1, start: 0, end: 0 } })
    mocks.api.saveSetup.mockResolvedValue({
      id: 23,
      experiment_id: 11,
      status: 'draft',
      current_step: 1,
      setup_status: 'incomplete',
      missing_requirements: ['schedule:5'],
      field_errors: {},
      plan_revision: 3,
      expires_at: '2026-08-31T00:00:00Z',
      default_opens_at: null,
      default_closes_at: null,
      classes: [{ class_id: 5, class_name: '一班', academic_year: '2026', excluded_count: 0, participant_count: 36, schedule_configured: false, schedule_mode: 'unset', opens_at: null, closes_at: null }],
    })
  })

  it('saves an incomplete class selection as a resumable draft without previewing', async () => {
    const wrapper = mount(TeacherExperimentPublishPlan)
    await flushPromises()

    await wrapper.get('#publish-class-5').setValue(true)
    await wrapper.get('[data-action="save-draft"]').trigger('click')
    await flushPromises()

    expect(mocks.api.saveSetup).toHaveBeenCalledWith(23, {
      expected_revision: 1,
      classes: [{ class_id: 5, excluded_student_ids: [] }],
      default_schedule: { opens_at: null, closes_at: null },
      schedules: [],
    })
    expect(mocks.api.preview).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('草稿已保存')
  })

  it('keeps batch schedule controls and real class rows together in the step-one panel', async () => {
    const wrapper = mount(TeacherExperimentPublishPlan)
    await flushPromises()

    expect(wrapper.find('.kt-publish-plan-panel').exists()).toBe(true)
    expect(wrapper.get('#publish-default-opens-at').attributes('type')).toBe('datetime-local')
    expect(wrapper.get('.kt-publish-class-table').text()).toContain('一班')
    expect(wrapper.get('.kt-publish-class-table').text()).toContain('36')
    expect(wrapper.get('.kt-publish-class-table').text()).toContain('开始')
    expect(wrapper.get('.kt-publish-class-table').text()).toContain('截止')
  })

  it('applies the batch schedule only to selected classes without previewing', async () => {
    const wrapper = mount(TeacherExperimentPublishPlan)
    await flushPromises()

    await wrapper.get('#publish-class-5').setValue(true)
    await wrapper.get('[data-action="apply-selected-schedule"]').trigger('click')

    expect(wrapper.get('.kt-publish-class-table').text()).toContain('沿用批量排期')
    expect(mocks.api.preview).not.toHaveBeenCalled()
  })

  it('moves from precheck to confirmation and commits exactly once', async () => {
    mocks.route.params.step = '2'
    const completedPlan = {
      id: 23,
      experiment_id: 11,
      status: 'previewed',
      current_step: 2,
      setup_status: 'complete',
      missing_requirements: [],
      field_errors: {},
      plan_revision: 4,
      expires_at: '2026-08-31T00:00:00Z',
      default_opens_at: '2026-09-01T00:00:00Z',
      default_closes_at: '2026-09-03T00:00:00Z',
      classes: [{ class_id: 5, class_name: '一班', academic_year: '2026', excluded_count: 1, participant_count: 35, schedule_configured: true, schedule_mode: 'default', opens_at: '2026-09-01T00:00:00Z', closes_at: '2026-09-03T00:00:00Z' }],
    }
    mocks.api.plan.mockResolvedValue(completedPlan)
    mocks.api.participants.mockResolvedValue({
      items: [{ id: 9, student_number: 'S009', display_name: '学生甲', class_id: 5, class_name: '一班', item_count: 4, is_valid: true, excluded: false }],
      page: { number: 1, size: 20, total_items: 1, total_pages: 1, start: 0, end: 1 },
    })
    mocks.api.review.mockResolvedValue({
      plan: completedPlan,
      preview: { id: 41, plan_revision: 4, class_count: 1, participant_count: 35, item_count: 140, randomization_validation: '通过', created_at: '2026-08-30T00:00:00Z' },
      effects: ['将冻结当前实验定义。'],
    })
    let resolveCommit: (() => void) | undefined
    mocks.api.commit.mockImplementation(() => new Promise<void>((resolve) => { resolveCommit = resolve }))

    const wrapper = mount(TeacherExperimentPublishPlan)
    await flushPromises()

    expect(wrapper.get('.kt-publish-precheck').text()).toContain('发布前检查结果')
    expect(wrapper.findAll('[data-precheck-metric-icon]').length).toBe(5)
    expect(wrapper.get('.kt-randomization-details').text()).toContain('通过')
    await wrapper.get('[data-action="enter-confirmation"]').trigger('click')
    expect(wrapper.get('.kt-publish-confirmation').text()).toContain('发布确认')
    expect(wrapper.get('.kt-publish-confirmation').text()).toContain('允许断点续答')
    expect(wrapper.get('.kt-publish-confirmation').text()).toContain('不采集')
    expect(wrapper.get('.kt-publish-confirmation').text()).toContain('总限时')
    expect(wrapper.get('.kt-publish-confirmation').text()).toContain('45 分钟')
    expect(wrapper.get('.kt-publish-confirmation').text()).toContain('单题限时')
    expect(wrapper.get('.kt-publish-confirmation').text()).toContain('90 秒')
    await wrapper.get('[data-action="commit-publish"]').trigger('click')
    await wrapper.get('[data-action="commit-publish"]').trigger('click')
    expect(mocks.api.commit).toHaveBeenCalledTimes(1)
    resolveCommit?.()
    await flushPromises()

    expect(mocks.push).toHaveBeenCalledWith('/teacher/courses/7/experiments/11/')
  })
})
