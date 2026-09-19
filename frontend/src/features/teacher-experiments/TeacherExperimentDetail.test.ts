import { flushPromises, mount } from '@vue/test-utils'
import { nextTick, reactive } from 'vue'
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

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function collectionResult(displayName: string, query: string) {
  return {
    items: [{
      participant_id: query === 'old' ? 101 : 102,
      student_id: query === 'old' ? 19 : 20,
      student_number: query === 'old' ? 'S019' : 'S020',
      display_name: displayName,
      class_id: 5,
      class_name: '实验班',
      status: 'expired',
      status_label: '已超时',
      started_at: null,
      ended_at: null,
      submission_count: 0,
    }],
    page: { number: 1, size: 25, total_items: 1, total_pages: 1, start: 1, end: 1 },
    filters: { class_id: null, status: 'expired', q: query },
    class_choices: [{ id: 5, name: '实验班' }],
    status_choices: [{ value: 'expired', label: '已超时' }],
  }
}

describe('TeacherExperimentDetail', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.api.collectionParticipants.mockReset()
    mocks.route.query = reactive({})
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
      id: 31, class_id: 5, class_name: '实验班', participant_count: 15,
      opens_at: '2026-09-01T00:00:00Z', closes_at: '2026-09-03T00:00:00Z', business_status: '采集中',
      grading: { enabled: false, short_answer_questions: 0, submitted: 0, pending: 0, graded: 0 }, available_actions: [],
    }] })
    mocks.api.progress.mockResolvedValue({
      task_id: 31, total_participants: 15, not_started: 1, in_progress: 2, completed: 3, expired: 4, invalidated: 5,
    })
    const participant = {
        participant_id: 101, student_id: 19, student_number: 'S019', display_name: '李小满', class_id: 5,
        class_name: '实验班', status: 'expired', status_label: '已超时',
        started_at: '2026-09-01T01:00:00Z', ended_at: '2026-09-01T01:30:00Z', submission_count: 2,
    }
    mocks.api.collectionParticipants.mockResolvedValue({
      items: [participant],
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
    expect(summaryCells[2]?.text()).toBe('15')
    expect(summaryCells[3]?.text()).toBe('1')
    expect(summaryCells[4]?.text()).toBe('2')
    expect(summaryCells[5]?.text()).toBe('3')
    expect(summaryCells[6]?.text()).toBe('4')
    expect(summaryCells[7]?.text()).toBe('5')

    const desktop = wrapper.get('.kt-desktop-only')
    const mobile = wrapper.get('.kt-mobile-only')
    const desktopText = desktop.text()
    expect(desktopText).toContain('李小满')
    expect(desktopText).toContain('S019')
    expect(desktopText).toContain('实验班')
    expect(desktopText).toContain('已超时')
    expect(desktopText).toContain('提交 2 题')
    const mobileText = mobile.text()
    expect(mobileText).toContain('李小满')
    expect(mobileText).toContain('S019')
    expect(mobileText).toContain('实验班')
    expect(mobileText).toContain('已超时')
    expect(mobileText).toContain('提交 2 题')

    const expectedStart = new Date(participant.started_at).toLocaleString('zh-CN')
    const expectedEnd = new Date(participant.ended_at).toLocaleString('zh-CN')
    const desktopStarts = desktop.findAll('[data-collection-start]')
    const desktopEnds = desktop.findAll('[data-collection-end]')
    const mobileStarts = mobile.findAll('[data-collection-start]')
    const mobileEnds = mobile.findAll('[data-collection-end]')
    expect(desktopStarts.length).toBeGreaterThanOrEqual(1)
    expect(desktopEnds.length).toBeGreaterThanOrEqual(1)
    expect(mobileStarts.length).toBeGreaterThanOrEqual(1)
    expect(mobileEnds.length).toBeGreaterThanOrEqual(1)
    expect(desktopStarts.every((element) => element.text().includes(expectedStart))).toBe(true)
    expect(desktopEnds.every((element) => element.text().includes(expectedEnd))).toBe(true)
    expect(mobileStarts.every((element) => element.text().includes(expectedStart))).toBe(true)
    expect(mobileEnds.every((element) => element.text().includes(expectedEnd))).toBe(true)
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
    mocks.route.query = reactive({ tab: 'collection', class_id: '5', status: 'expired', q: 'S019', page: '2' })
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
    mocks.route.query = reactive({ tab: 'collection', class_id: '5', status: 'expired', q: 'S019' })
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
    mocks.route.query = reactive({ tab: 'collection' })
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
    mocks.route.query = reactive({ tab: 'collection', class_id: '5', status: 'expired', q: 'S019' })
    mocks.api.detail.mockResolvedValue({
      id: 11, name: '认知诊断实验', description: '', question_bank: { id: 3, name: '代数题库' }, status: 'published', status_label: '已发布',
      selection_mode_label: '全量题池', ordering_mode_label: '固定顺序', feedback_mode_label: '仅显示已提交',
      feedback_release_policy_label: '教师人工发布', time_mode_label: '无时限', resume_mode_label: '允许断点续答',
      confidence_mode_label: '不采集', can_edit: false, can_manage: true, updated_at: '2026-08-30T00:00:00Z',
    })
    mocks.api.collectionParticipants.mockResolvedValue({
      items: [{
        participant_id: 101, student_id: 19, student_number: 'S019', display_name: '超时学生', class_id: 5,
        class_name: '实验班', status: 'expired', status_label: '已超时', started_at: null, ended_at: null,
        submission_count: 0,
      }], page: { number: 1, size: 25, total_items: 26, total_pages: 2, start: 1, end: 25 },
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

  it('ignores a stale collection failure after a newer route request succeeds', async () => {
    const wrapper = mount(TeacherExperimentDetail)
    await flushPromises()
    const oldRequest = deferred<ReturnType<typeof collectionResult>>()
    const newRequest = deferred<ReturnType<typeof collectionResult>>()
    mocks.api.collectionParticipants.mockImplementation(
      (_experimentId: number, filters: { query?: string }) => filters.query === 'old' ? oldRequest.promise : newRequest.promise,
    )

    mocks.route.query.tab = 'collection'
    mocks.route.query.q = 'old'
    await nextTick()
    await vi.waitFor(() => expect(mocks.api.collectionParticipants).toHaveBeenCalledTimes(1))
    mocks.route.query.q = 'new'
    await nextTick()
    await vi.waitFor(() => expect(mocks.api.collectionParticipants).toHaveBeenCalledTimes(2))

    newRequest.resolve(collectionResult('新学生', 'new'))
    await flushPromises()
    oldRequest.reject(new Error('旧请求失败'))
    await flushPromises()

    expect(wrapper.text()).toContain('新学生')
    expect(wrapper.text()).not.toContain('旧请求失败')
    expect(wrapper.text()).not.toContain('正在加载学生采集状态')
  })

  it('requests the latest route filters while the initial collection request is pending', async () => {
    mocks.route.query = reactive({ tab: 'collection', q: 'old' })
    const oldRequest = deferred<ReturnType<typeof collectionResult>>()
    const newRequest = deferred<ReturnType<typeof collectionResult>>()
    mocks.api.collectionParticipants.mockImplementation(
      (_experimentId: number, filters: { query?: string }) => filters.query === 'old' ? oldRequest.promise : newRequest.promise,
    )

    const wrapper = mount(TeacherExperimentDetail)
    await vi.waitFor(() => expect(mocks.api.collectionParticipants).toHaveBeenCalledWith(11, { query: 'old', page: 1 }))
    mocks.route.query.q = 'new'
    await nextTick()
    expect(mocks.api.collectionParticipants).toHaveBeenCalledWith(11, { query: 'new', page: 1 })

    newRequest.resolve(collectionResult('新学生', 'new'))
    await flushPromises()
    oldRequest.resolve(collectionResult('旧学生', 'old'))
    await flushPromises()

    expect(wrapper.text()).toContain('新学生')
    expect(wrapper.text()).not.toContain('旧学生')
  })
})
