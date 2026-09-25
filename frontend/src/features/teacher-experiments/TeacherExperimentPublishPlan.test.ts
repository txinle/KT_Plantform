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
    operationJob: vi.fn(),
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
    sessionStorage.clear()
    mocks.route.params.step = '1'
    mocks.api.plan.mockResolvedValue({
      id: 23,
      experiment_id: 11,
      status: 'draft',
      current_step: 1,
      setup_status: 'empty',
      missing_requirements: ['classes'],
      blocking_issues: [],
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
      setup_status: 'complete',
      can_preview: true,
      missing_requirements: [],
      blocking_issues: [],
      field_errors: {},
      plan_revision: 3,
      expires_at: '2026-08-31T00:00:00Z',
      default_opens_at: null,
      default_closes_at: null,
      classes: [{ class_id: 5, class_name: '一班', academic_year: '2026', excluded_count: 0, participant_count: 36, schedule_mode: 'default', opens_at: null, closes_at: null }],
    })
    mocks.api.preview.mockResolvedValue({
      job_id: 'preview-job', operation: 'preview', status: 'queued', status_url: '/jobs/preview-job/',
      plan_revision: 3, stage: 'queued', processed: 0, total: 36, error_code: '', error_message: '',
      preview_run_id: null, release_snapshot_id: null,
    })
    mocks.api.operationJob.mockResolvedValue({
      job_id: 'preview-job', operation: 'preview', status: 'succeeded', status_url: '/jobs/preview-job/',
      plan_revision: 3, stage: 'completed', processed: 36, total: 36, error_code: '', error_message: '',
      preview_run_id: 41, release_snapshot_id: null,
    })
  })

  it('saves an immediate indefinite class schedule without previewing', async () => {
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

  it('shows student-filtered inventory and never shows success with a blocking issue', async () => {
    mocks.api.plan.mockResolvedValue({
      id: 23, experiment_id: 11, status: 'draft', current_step: 1,
      setup_status: 'complete', missing_requirements: [], can_preview: false,
      blocking_issues: [{
        code: 'QUESTION_TYPE_QUOTA_INSUFFICIENT', scope: 'student', class_id: 5, kc_id: null,
        field: 'sampling_type_quotas', question_type: 'numeric', requested: 3, available: 1,
        student_id: 9, student_number: 'A-001', message: '学生 A-001 的数值题需要 3 题，排除历史题后可用 1 题。',
      }],
      global_inventory: {
        scope: 'question_bank', candidate_count: 129, requested_count: 52,
        question_types: [{ question_type: 'numeric', requested: 3, available: 3 }],
      },
      student_inventory: {
        scope: 'selected_participants', checked: true, participant_count: 1,
        minimum_candidate_count: 41,
        question_types: [{ question_type: 'numeric', requested: 3, minimum_available: 1, insufficient_student_count: 1 }],
        blocking_issue_count: 1,
      },
      field_errors: {}, plan_revision: 2, expires_at: '2026-08-31T00:00:00Z',
      default_opens_at: null, default_closes_at: null,
      classes: [{ class_id: 5, class_name: '一班', academic_year: '2026', excluded_count: 0, participant_count: 1, schedule_mode: 'default', opens_at: null, closes_at: null }],
    })
    const wrapper = mount(TeacherExperimentPublishPlan)
    await flushPromises()

    expect(wrapper.text()).toContain('学生 A-001 的数值题需要 3 题')
    expect(wrapper.text()).toContain('题库全局候选')
    expect(wrapper.text()).toContain('本次学生过滤后')
    expect(wrapper.text()).not.toContain('设置完整，可以执行发布检查。')
  })

  it('hides a saved success result after a local class change or preview failure', async () => {
    const wrapper = mount(TeacherExperimentPublishPlan)
    await flushPromises()
    await wrapper.get('#publish-class-5').setValue(true)
    await wrapper.get('[data-action="save-draft"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('设置完整，可以执行发布检查。')

    await wrapper.get('#publish-class-5').setValue(false)
    expect(wrapper.text()).not.toContain('设置完整，可以执行发布检查。')
    expect(wrapper.text()).toContain('未保存的修改')

    await wrapper.get('#publish-class-5').setValue(true)
    mocks.api.preview.mockRejectedValueOnce(new Error('历史题量发生变化'))
    await wrapper.get('[data-action="run-preview"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('历史题量发生变化')
    expect(wrapper.text()).not.toContain('设置完整，可以执行发布检查。')
  })

  it('renders a draft with a deleted bank without crashing', async () => {
    mocks.route.params.step = '2'
    const completedPlan = {
      id: 23, experiment_id: 11, status: 'previewed', current_step: 2, setup_status: 'complete',
      missing_requirements: [], blocking_issues: [], field_errors: {}, plan_revision: 4,
      expires_at: '2026-08-31T00:00:00Z', default_opens_at: null, default_closes_at: null,
      classes: [{ class_id: 5, class_name: '一班', academic_year: '2026', excluded_count: 0, participant_count: 36, schedule_mode: 'default', opens_at: null, closes_at: null }],
    }
    mocks.api.plan.mockResolvedValue(completedPlan)
    mocks.api.review.mockResolvedValue({
      plan: completedPlan,
      preview: { id: 41, plan_revision: 4, class_count: 1, participant_count: 36, item_count: 36, randomization_validation: '通过', created_at: '2026-08-30T00:00:00Z' },
      effects: [],
    })
    mocks.api.detail.mockResolvedValue({
      id: 11, name: '待修复实验', description: '', question_bank: null,
      status: 'draft', status_label: '草稿', selection_mode_label: '全量题池', ordering_mode_label: '固定顺序',
      feedback_mode_label: '不反馈', time_mode_label: '无时限', total_time_limit_minutes: null,
      per_question_time_limit_seconds: null, resume_mode_label: '允许断点续答', confidence_mode_label: '不采集',
      can_edit: true, can_manage: true, updated_at: '2026-08-30T00:00:00Z',
    })
    const wrapper = mount(TeacherExperimentPublishPlan)
    await flushPromises()

    await wrapper.get('[data-action="enter-confirmation"]').trigger('click')
    expect(wrapper.get('.kt-definition-summary__list').text()).toContain('未选择题库')
  })

  it('applies the batch schedule only to selected classes without previewing', async () => {
    const wrapper = mount(TeacherExperimentPublishPlan)
    await flushPromises()

    await wrapper.get('#publish-class-5').setValue(true)
    await wrapper.get('[data-action="apply-selected-schedule"]').trigger('click')

    expect(wrapper.get('.kt-publish-class-table').text()).toContain('沿用批量排期')
    expect(mocks.api.preview).not.toHaveBeenCalled()
  })

  it('waits for the queued preview job before opening the review step', async () => {
    const completedPlan = {
      id: 23, experiment_id: 11, status: 'previewed', current_step: 2, setup_status: 'complete',
      missing_requirements: [], blocking_issues: [], field_errors: {}, plan_revision: 3,
      expires_at: '2026-08-31T00:00:00Z', default_opens_at: null, default_closes_at: null,
      classes: [{ class_id: 5, class_name: '一班', academic_year: '2026', excluded_count: 0, participant_count: 36, schedule_mode: 'default', opens_at: null, closes_at: null }],
    }
    mocks.api.review.mockResolvedValue({
      plan: completedPlan,
      preview: { id: 41, plan_revision: 3, class_count: 1, participant_count: 36, item_count: 36, randomization_validation: '通过', created_at: '2026-08-30T00:00:00Z' },
      effects: [],
    })
    const wrapper = mount(TeacherExperimentPublishPlan)
    await flushPromises()

    await wrapper.get('#publish-class-5').setValue(true)
    await wrapper.get('[data-action="run-preview"]').trigger('click')
    await flushPromises()

    expect(mocks.api.operationJob).toHaveBeenCalledWith('preview-job')
    expect(mocks.api.review).toHaveBeenCalledWith(23)
    expect(mocks.push).toHaveBeenCalledWith('/teacher/courses/7/experiments/11/publish-plan/23/step/2/')
  })

  it('restores a queued preview job after a page refresh', async () => {
    sessionStorage.setItem('kt-publish-operation:23', JSON.stringify({
      job_id: 'restored-job',
      operation: 'preview',
    }))
    const completedPlan = {
      id: 23, experiment_id: 11, status: 'previewed', current_step: 2, setup_status: 'complete',
      missing_requirements: [], blocking_issues: [], field_errors: {}, plan_revision: 3,
      expires_at: '2026-08-31T00:00:00Z', default_opens_at: null, default_closes_at: null,
      classes: [{ class_id: 5, class_name: '一班', academic_year: '2026', excluded_count: 0, participant_count: 36, schedule_mode: 'default', opens_at: null, closes_at: null }],
    }
    mocks.api.operationJob.mockResolvedValue({
      job_id: 'restored-job', operation: 'preview', status: 'succeeded', status_url: '/jobs/restored-job/',
      plan_revision: 3, stage: 'completed', processed: 36, total: 36, error_code: '', error_message: '',
      preview_run_id: 41, release_snapshot_id: null,
    })
    mocks.api.review.mockResolvedValue({
      plan: completedPlan,
      preview: { id: 41, plan_revision: 3, class_count: 1, participant_count: 36, item_count: 36, randomization_validation: '通过', created_at: '2026-08-30T00:00:00Z' },
      effects: [],
    })

    mount(TeacherExperimentPublishPlan)
    await flushPromises()

    expect(mocks.api.operationJob).toHaveBeenCalledWith('restored-job')
    expect(sessionStorage.getItem('kt-publish-operation:23')).toBeNull()
    expect(mocks.push).toHaveBeenCalledWith('/teacher/courses/7/experiments/11/publish-plan/23/step/2/')
  })

  it('shows the backend stale reason and returns to setup', async () => {
    mocks.api.operationJob.mockResolvedValue({
      job_id: 'preview-job', operation: 'preview', status: 'stale', status_url: '/jobs/preview-job/',
      plan_revision: 3, stage: 'failed', processed: 12, total: 36,
      error_code: 'PREVIEW_INVALIDATED', error_message: '班级名单已变化，请重新运行发布前检查。',
      preview_run_id: null, release_snapshot_id: null,
    })
    const wrapper = mount(TeacherExperimentPublishPlan)
    await flushPromises()

    await wrapper.get('#publish-class-5').setValue(true)
    await wrapper.get('[data-action="run-preview"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('班级名单已变化，请重新运行发布前检查。')
    expect(mocks.push).not.toHaveBeenCalled()
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
      blocking_issues: [],
      field_errors: {},
      plan_revision: 4,
      expires_at: '2026-08-31T00:00:00Z',
      default_opens_at: '2026-09-01T00:00:00Z',
      default_closes_at: '2026-09-03T00:00:00Z',
      classes: [{ class_id: 5, class_name: '一班', academic_year: '2026', excluded_count: 1, participant_count: 35, schedule_mode: 'default', opens_at: '2026-09-01T00:00:00Z', closes_at: '2026-09-03T00:00:00Z' }],
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
    const queuedCommit = {
      job_id: 'commit-job', operation: 'commit' as const, status: 'queued' as const, status_url: '/jobs/commit-job/',
      plan_revision: 4, stage: 'queued', processed: 0, total: 35, error_code: '', error_message: '',
      preview_run_id: 41, release_snapshot_id: null,
    }
    let resolveCommit: ((job: typeof queuedCommit) => void) | undefined
    mocks.api.commit.mockImplementation(() => new Promise<typeof queuedCommit>((resolve) => { resolveCommit = resolve }))
    mocks.api.operationJob.mockResolvedValue({
      ...queuedCommit, status: 'succeeded', stage: 'completed', processed: 35, release_snapshot_id: 12,
    })

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
    resolveCommit?.(queuedCommit)
    await flushPromises()

    expect(mocks.push).toHaveBeenCalledWith('/teacher/courses/7/experiments/11/')
  })

  it('shows a specific empty-class blocker and does not call preview', async () => {
    mocks.api.saveSetup.mockResolvedValue({
      id: 23,
      experiment_id: 11,
      status: 'draft',
      current_step: 1,
      setup_status: 'complete',
      missing_requirements: [],
      blocking_issues: [{
        code: 'CLASS_NO_PARTICIPANTS',
        message: '班级“一班”没有可发布参与者。',
        class_id: 5,
        kc_id: null,
        field: 'classes',
      }],
      field_errors: {},
      plan_revision: 2,
      expires_at: '2026-08-31T00:00:00Z',
      default_opens_at: null,
      default_closes_at: null,
      classes: [{ class_id: 5, class_name: '一班', academic_year: '2026', excluded_count: 0, participant_count: 0, schedule_mode: 'default', opens_at: null, closes_at: null }],
    })
    const wrapper = mount(TeacherExperimentPublishPlan)
    await flushPromises()

    await wrapper.get('#publish-class-5').setValue(true)
    await wrapper.get('[data-action="run-preview"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('班级“一班”没有可发布参与者。')
    expect(mocks.api.preview).not.toHaveBeenCalled()
  })
})
