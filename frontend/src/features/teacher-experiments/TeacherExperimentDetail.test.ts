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
    releaseFeedback: vi.fn(),
    collectionParticipants: vi.fn(),
  },
  deletion: {
    previewExperiment: vi.fn(), deleteExperiment: vi.fn(),
    previewClassExperiment: vi.fn(), removeClassExperiment: vi.fn(),
    previewClass: vi.fn(), deleteClass: vi.fn(), cleanup: vi.fn(),
  },
  push: vi.fn(),
  replace: vi.fn(),
  route: {
    params: { courseId: '7', experimentId: '11' },
    query: {} as Record<string, string>,
  },
}))

vi.mock('vue-router', () => ({
  useRoute: () => mocks.route,
  useRouter: () => ({ push: mocks.push, replace: mocks.replace }),
}))

vi.mock('./api', () => ({ getTeacherExperimentApi: () => mocks.api }))
vi.mock('@/features/teacher-deletion/api', () => ({ getTeacherDeletionApi: () => mocks.deletion }))

import TeacherExperimentDetail from './TeacherExperimentDetail.vue'

describe('TeacherExperimentDetail', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.route.query = {}
    mocks.api.detail.mockResolvedValue({
      id: 11,
      name: '认知诊断实验',
      description: '比较两种反馈策略',
      question_bank: { id: 3, name: '代数题库' },
      status: 'draft',
      status_label: '草稿',
      selection_mode_label: '全量题池',
      cross_experiment_item_policy: 'exclude_previous',
      review_count: 0,
      ordering_mode_label: '固定顺序',
      feedback_mode_label: '仅显示已提交',
      feedback_release_policy_label: '学生完成后',
      time_mode_label: '无时限',
      total_time_limit_minutes: 45,
      per_question_time_limit_seconds: 90,
      resume_mode_label: '允许断点续答',
      confidence_mode_label: '不采集',
      can_edit: true,
      can_manage: true,
      updated_at: '2026-08-30T00:00:00Z',
    })
    mocks.api.readiness.mockResolvedValue({ ready: true, issues: [], candidate_count: 12, participant_count: 0 })
    mocks.api.classTasks.mockResolvedValue({ items: [] })
    mocks.api.progress.mockResolvedValue({ total_participants: 0, not_started: 0, in_progress: 0, completed: 0 })
    mocks.api.collectionParticipants.mockResolvedValue({
      items: [],
      page: { number: 1, size: 25, total_items: 0, total_pages: 1, start: 0, end: 0 },
      filters: { class_id: null, status: '', q: '' },
      class_choices: [{ id: 5, name: '实验班' }],
      status_choices: [
        { value: 'not_started', label: '未开始' },
        { value: 'in_progress', label: '进行中' },
        { value: 'completed', label: '正常完成' },
        { value: 'expired', label: '已超时' },
        { value: 'invalidated', label: '已作废' },
      ],
    })
    mocks.api.startPlan.mockResolvedValue({ id: 23 })
    mocks.api.releaseFeedback.mockResolvedValue({ id: 31 })
    mocks.deletion.previewExperiment.mockResolvedValue({
      action: 'delete_experiment', object_id: 11, object_name: '认知诊断实验', allowed: true, blocked_reason: '',
      experiments: [], counts: { class_count: 0, session_count: 0, export_count: 0, member_count: 0 }, confirmation_token: 'token-11',
    })
    mocks.deletion.deleteExperiment.mockResolvedValue({ deleted: true, cleanup_pending: false, cleanup_id: null })
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
    expect(wrapper.get('.kt-experiment-tabs').classes()).toContain('kt-sidebar-secondary-nav')
    expect(wrapper.find('.kt-experiment-draft-grid').exists()).toBe(true)
    expect(tabs[0]?.attributes('aria-selected')).toBe('true')
    expect(wrapper.findAll('[data-summary-icon]')).toHaveLength(10)
    expect(wrapper.get('.kt-definition-summary').text()).toContain('排除已正式作答题目')
    expect(wrapper.get('.kt-definition-summary').text()).not.toContain('时限模式')
    expect(wrapper.get('.kt-definition-summary').text()).toContain('总限时')
    expect(wrapper.get('.kt-definition-summary').text()).toContain('45 分钟')
    expect(wrapper.get('.kt-definition-summary').text()).toContain('单题限时')
    expect(wrapper.get('.kt-definition-summary').text()).toContain('90 秒')
    expect(wrapper.find('.kt-definition-summary__action').exists()).toBe(true)
    expect(wrapper.find('.kt-publish-empty-illustration').exists()).toBe(true)
    expect(wrapper.findAll('.kt-readiness-checklist li').length).toBeGreaterThanOrEqual(3)
  })

  it('deletes an experiment after preview and returns to the experiment list', async () => {
    const wrapper = mount(TeacherExperimentDetail)
    await flushPromises()

    await wrapper.get('[data-delete-experiment]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-deletion-confirm]').trigger('click')
    await flushPromises()

    expect(mocks.deletion.deleteExperiment).toHaveBeenCalledWith(7, 11, 'token-11')
    expect(mocks.push).toHaveBeenCalledWith('/teacher/courses/7/experiments/')
  })

  it('shows a deleted draft bank as unselected', async () => {
    mocks.api.detail.mockResolvedValueOnce({
      id: 11, name: '待修复实验', description: '', question_bank: null,
      status: 'draft', status_label: '草稿', selection_mode_label: '全量题池', ordering_mode_label: '固定顺序',
      feedback_mode_label: '不反馈', feedback_release_policy_label: '个人完成后', total_time_limit_minutes: null,
      per_question_time_limit_seconds: null, resume_mode_label: '允许断点续答', confidence_mode_label: '不采集',
      can_edit: true, can_manage: true, updated_at: '2026-08-30T00:00:00Z',
    })
    const wrapper = mount(TeacherExperimentDetail)
    await flushPromises()

    expect(wrapper.get('.kt-definition-summary').text()).toContain('未选择题库')
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
      feedback_release_policy_label: '教师人工发布',
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
        grading: { enabled: true, short_answer_questions: 1, submitted: 12, pending: 4, graded: 8 },
        available_actions: ['grade_short_answers', 'revise_schedule', 'manual_close', 'release_feedback'],
      }],
    })
    mocks.api.progress.mockResolvedValue({ total_participants: 36, not_started: 11, in_progress: 8, completed: 17 })

    const wrapper = mount(TeacherExperimentDetail)
    await flushPromises()

    expect(wrapper.get('[role="tab"][aria-selected="true"]').text()).toBe('班级发布')
    expect(wrapper.get('.kt-published-task-table').text()).toContain('实验班')
    expect(wrapper.get('[data-action="revise-schedule"]').text()).toBe('修订排期')
    expect(wrapper.get('[data-action="manual-close"]').text()).toBe('提前结束')
    expect(wrapper.get('[data-action="release-feedback"]').text()).toBe('发布反馈')
    expect(wrapper.get('[data-action="grade-short-answers"]').text()).toContain('待批改 4')

    await wrapper.get('[data-action="release-feedback"]').trigger('click')
    await flushPromises()

    expect(mocks.api.releaseFeedback).toHaveBeenCalledWith(31)
    expect(mocks.api.detail).toHaveBeenCalledTimes(2)
    expect(mocks.api.classTasks).toHaveBeenCalledTimes(2)
  })

  it('loads collection participants only after opening the collection tab and renders all status details', async () => {
    mocks.api.detail.mockResolvedValue({
      id: 11, name: '认知诊断实验', description: '比较两种反馈策略', question_bank: { id: 3, name: '代数题库' },
      status: 'published', status_label: '已发布', selection_mode_label: '全量题池', ordering_mode_label: '固定顺序',
      feedback_mode_label: '仅显示已提交', feedback_release_policy_label: '教师人工发布', time_mode_label: '无时限',
      resume_mode_label: '允许断点续答', confidence_mode_label: '不采集', can_edit: false, can_manage: true,
      updated_at: '2026-08-30T00:00:00Z',
    })
    mocks.api.classTasks.mockResolvedValue({ items: [{
      id: 31, class_id: 5, class_name: '实验班', participant_count: 5,
      opens_at: '2026-09-01T00:00:00Z', closes_at: '2026-09-03T00:00:00Z', business_status: '采集中',
      grading: { enabled: false, short_answer_questions: 0, submitted: 0, pending: 0, graded: 0 }, available_actions: [],
    }] })
    mocks.api.progress.mockResolvedValue({
      task_id: 31, total_participants: 5, not_started: 1, in_progress: 1, completed: 1, expired: 1, invalidated: 1,
    })
    mocks.api.collectionParticipants.mockResolvedValue({
      items: [{
        participant_id: 101, student_id: 19, student_number: 'S019', display_name: '李小满', class_id: 5,
        class_name: '实验班', status: 'expired', status_label: '已超时',
        started_at: '2026-09-01T01:00:00Z', ended_at: '2026-09-01T01:30:00Z', submission_count: 2,
      }],
      page: { number: 1, size: 25, total_items: 1, total_pages: 1, start: 1, end: 1 },
      filters: { class_id: null, status: '', q: '' },
      class_choices: [{ id: 5, name: '实验班' }],
      status_choices: [
        { value: 'not_started', label: '未开始' }, { value: 'in_progress', label: '进行中' },
        { value: 'completed', label: '正常完成' }, { value: 'expired', label: '已超时' },
        { value: 'invalidated', label: '已作废' },
      ],
    })

    const wrapper = mount(TeacherExperimentDetail)
    await flushPromises()

    expect(mocks.api.collectionParticipants).not.toHaveBeenCalled()
    await wrapper.get('#experiment-tab-collection').trigger('click')
    await flushPromises()

    expect(mocks.api.collectionParticipants).toHaveBeenCalledTimes(1)
    expect(mocks.api.collectionParticipants).toHaveBeenCalledWith(11, { page: 1 })
    const summary = wrapper.get('[data-collection-summary]')
    expect(summary.findAll('thead th').map((cell) => cell.text())).toEqual([
      '班级', '状态', '参与者', '未开始', '进行中', '正常完成', '已超时', '已作废',
    ])
    const summaryCells = summary.get('tbody tr').findAll('td')
    expect(summaryCells[2]?.text()).toBe('5')
    expect(summaryCells[3]?.text()).toBe('1')
    expect(summaryCells[4]?.text()).toBe('1')
    expect(summaryCells[5]?.text()).toBe('1')
    expect(summaryCells[6]?.text()).toBe('1')
    expect(summaryCells[7]?.text()).toBe('1')

    const desktopCells = wrapper.get('.kt-desktop-only tbody tr').findAll('td')
    expect(desktopCells[0]?.text()).toContain('李小满')
    expect(desktopCells[1]?.text()).toContain('S019')
    expect(desktopCells[2]?.text()).toContain('实验班')
    expect(desktopCells[3]?.text()).toContain('2026')
    expect(desktopCells[3]?.text()).not.toBe('')
    expect(desktopCells[4]?.text()).toContain('2026')
    expect(desktopCells[4]?.text()).not.toBe('')
    expect(desktopCells[5]?.text()).toContain('已超时')
    expect(desktopCells[6]?.text()).toContain('提交 2 题')

    const mobileText = wrapper.get('.kt-mobile-only').text()
    expect(mobileText).toContain('李小满')
    expect(mobileText).toContain('S019')
    expect(mobileText).toContain('实验班')
    expect(mobileText).toContain('已超时')
    expect(mobileText).toContain('提交 2 题')
    expect(mobileText).toMatch(/开始：[\s\S]*?2026/)
    expect(mobileText).toMatch(/结束：[\s\S]*?2026/)
  })

  it('persists collection filters in the URL and requests the corresponding participants', async () => {
    mocks.api.detail.mockResolvedValue({
      id: 11, name: '认知诊断实验', description: '', question_bank: { id: 3, name: '代数题库' }, status: 'published', status_label: '已发布',
      selection_mode_label: '全量题池', ordering_mode_label: '固定顺序', feedback_mode_label: '仅显示已提交',
      feedback_release_policy_label: '教师人工发布', time_mode_label: '无时限', resume_mode_label: '允许断点续答',
      confidence_mode_label: '不采集', can_edit: false, can_manage: true, updated_at: '2026-08-30T00:00:00Z',
    })
    const wrapper = mount(TeacherExperimentDetail)
    await flushPromises()
    await wrapper.get('#experiment-tab-collection').trigger('click')
    await flushPromises()

    await wrapper.get('#collection-filter-class').setValue('5')
    await wrapper.get('#collection-filter-status').setValue('expired')
    await wrapper.get('#collection-filter-query').setValue('S019')
    await wrapper.get('[data-collection-filter]').trigger('submit')
    await flushPromises()

    expect(mocks.replace).toHaveBeenCalledWith({ query: { tab: 'collection', class_id: '5', status: 'expired', q: 'S019' } })
    expect(mocks.api.collectionParticipants).toHaveBeenLastCalledWith(11, {
      classId: 5, status: 'expired', query: 'S019', page: 1,
    })
  })

  it('restores collection filters and page from the URL on initial load', async () => {
    mocks.route.query = { tab: 'collection', class_id: '5', status: 'expired', q: 'S019', page: '2' }
    mocks.api.detail.mockResolvedValue({
      id: 11, name: '认知诊断实验', description: '', question_bank: { id: 3, name: '代数题库' }, status: 'published', status_label: '已发布',
      selection_mode_label: '全量题池', ordering_mode_label: '固定顺序', feedback_mode_label: '仅显示已提交',
      feedback_release_policy_label: '教师人工发布', time_mode_label: '无时限', resume_mode_label: '允许断点续答',
      confidence_mode_label: '不采集', can_edit: false, can_manage: true, updated_at: '2026-08-30T00:00:00Z',
    })
    const wrapper = mount(TeacherExperimentDetail)
    await flushPromises()

    expect(wrapper.get('#experiment-tab-collection').attributes('aria-selected')).toBe('true')
    expect(mocks.api.collectionParticipants).toHaveBeenCalledWith(11, {
      classId: 5, status: 'expired', query: 'S019', page: 2,
    })
  })

  it('shows a clearable empty state and resets filters to the collection tab', async () => {
    mocks.route.query = { tab: 'collection', class_id: '5', status: 'expired', q: 'S019' }
    mocks.api.detail.mockResolvedValue({
      id: 11, name: '认知诊断实验', description: '', question_bank: { id: 3, name: '代数题库' }, status: 'published', status_label: '已发布',
      selection_mode_label: '全量题池', ordering_mode_label: '固定顺序', feedback_mode_label: '仅显示已提交',
      feedback_release_policy_label: '教师人工发布', time_mode_label: '无时限', resume_mode_label: '允许断点续答',
      confidence_mode_label: '不采集', can_edit: false, can_manage: true, updated_at: '2026-08-30T00:00:00Z',
    })
    mocks.api.collectionParticipants.mockResolvedValueOnce({
      items: [], page: { number: 1, size: 25, total_items: 0, total_pages: 1, start: 0, end: 0 },
      filters: { class_id: 5, status: 'expired', q: 'S019' }, class_choices: [{ id: 5, name: '实验班' }],
      status_choices: [{ value: 'expired', label: '已超时' }],
    })
    const wrapper = mount(TeacherExperimentDetail)
    await flushPromises()

    expect(wrapper.text()).toContain('当前筛选条件下没有学生')
    expect(wrapper.find('[data-collection-clear]').exists()).toBe(true)
    await wrapper.get('[data-collection-clear]').trigger('click')
    await flushPromises()

    expect(mocks.replace).toHaveBeenCalledWith({ query: { tab: 'collection' } })
    expect(mocks.api.collectionParticipants).toHaveBeenLastCalledWith(11, { page: 1 })
  })

  it('clears stale participant rows when a subsequent collection request fails', async () => {
    mocks.route.query = { tab: 'collection' }
    mocks.api.detail.mockResolvedValue({
      id: 11, name: '认知诊断实验', description: '', question_bank: { id: 3, name: '代数题库' }, status: 'published', status_label: '已发布',
      selection_mode_label: '全量题池', ordering_mode_label: '固定顺序', feedback_mode_label: '仅显示已提交',
      feedback_release_policy_label: '教师人工发布', time_mode_label: '无时限', resume_mode_label: '允许断点续答',
      confidence_mode_label: '不采集', can_edit: false, can_manage: true, updated_at: '2026-08-30T00:00:00Z',
    })
    mocks.api.collectionParticipants.mockResolvedValueOnce({
      items: [{
        participant_id: 101, student_id: 19, student_number: 'S019', display_name: '超时学生', class_id: 5,
        class_name: '实验班', status: 'expired', status_label: '已超时', started_at: '2026-09-01T01:00:00Z',
        ended_at: '2026-09-01T01:30:00Z', submission_count: 2,
      }],
      page: { number: 1, size: 25, total_items: 1, total_pages: 1, start: 1, end: 1 }, filters: { class_id: null, status: '', q: '' },
      class_choices: [{ id: 5, name: '实验班' }], status_choices: [{ value: 'expired', label: '已超时' }],
    })
    const wrapper = mount(TeacherExperimentDetail)
    await flushPromises()
    expect(wrapper.text()).toContain('超时学生')

    mocks.api.collectionParticipants.mockRejectedValueOnce(new Error('学生采集状态加载失败'))
    await wrapper.get('[data-collection-filter]').trigger('submit')
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('学生采集状态加载失败')
    expect(wrapper.text()).not.toContain('超时学生')
  })

  it('keeps active collection filters when navigating to the next participant page', async () => {
    mocks.route.query = { tab: 'collection', class_id: '5', status: 'expired', q: 'S019' }
    mocks.api.detail.mockResolvedValue({
      id: 11, name: '认知诊断实验', description: '', question_bank: { id: 3, name: '代数题库' }, status: 'published', status_label: '已发布',
      selection_mode_label: '全量题池', ordering_mode_label: '固定顺序', feedback_mode_label: '仅显示已提交',
      feedback_release_policy_label: '教师人工发布', time_mode_label: '无时限', resume_mode_label: '允许断点续答',
      confidence_mode_label: '不采集', can_edit: false, can_manage: true, updated_at: '2026-08-30T00:00:00Z',
    })
    mocks.api.collectionParticipants.mockResolvedValue({
      items: [], page: { number: 1, size: 25, total_items: 26, total_pages: 2, start: 1, end: 25 },
      filters: { class_id: 5, status: 'expired', q: 'S019' }, class_choices: [{ id: 5, name: '实验班' }],
      status_choices: [{ value: 'expired', label: '已超时' }],
    })
    const wrapper = mount(TeacherExperimentDetail)
    await flushPromises()
    await wrapper.get('[data-next]').trigger('click')
    await flushPromises()

    expect(mocks.replace).toHaveBeenCalledWith({
      query: { tab: 'collection', class_id: '5', status: 'expired', q: 'S019', page: '2' },
    })
    expect(mocks.api.collectionParticipants).toHaveBeenLastCalledWith(11, {
      classId: 5, status: 'expired', query: 'S019', page: 2,
    })
  })
})
