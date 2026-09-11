import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  api: { advanced: vi.fn(), advancedStudents: vi.fn() },
  push: vi.fn(),
  route: {
    params: { courseId: '7' },
    query: { view: 'overview', experiment_ids: '3,5', class_id: '4', table: 'sessions' } as Record<string, string | undefined>,
  },
  applyRouteQuery: vi.fn(),
}))
vi.mock('vue-router', async () => {
  const { reactive } = await import('vue')
  const route = reactive(mocks.route)
  mocks.applyRouteQuery.mockImplementation((query: Record<string, string | undefined>) => { route.query = query })
  return {
    useRoute: () => route,
    useRouter: () => ({
      push: async (location: { query: Record<string, string | undefined> }) => {
        mocks.push(location)
        route.query = location.query
      },
    }),
  }
})
vi.mock('./api', () => ({ getTeacherDataCenterApi: () => mocks.api, routeQuery: (query: object) => query }))

import TeacherDataAdvanced from './TeacherDataAdvanced.vue'
enableAutoUnmount(afterEach)
afterEach(() => vi.useRealTimers())

const tables = [
  ['sessions', '会话', 3], ['questions', '题目', 5], ['knowledge_points', '知识点', 8],
  ['question_kc_mapping', '题目和知识点映射', 11], ['responses', '逐题作答', 15], ['behavior_events', '行为过程', 96],
].map(([key, label, total_items]) => ({ key, label, path: key + '.csv', description: label + '说明', total_items }))

const sessions = [
  { session_ref: 'S-001', student_id: 31, student_number: '20231001', name: '张同学', course_name: '研究方法', experiment_name: '实验一：线性回归分析', class_name: '实验班', status_label: '已完成', started_at: '2026-09-08T14:20:00Z', ended_at: '2026-09-08T15:05:00Z', submitted_at: '2026-09-08T15:05:00Z', duration_minutes: 45, question_count: 5, behavior_count: 96 },
  { session_ref: 'S-002', student_id: 31, student_number: '20231001', name: '张同学', course_name: '研究方法', experiment_name: '实验二：决策树建模', class_name: '实验班', status_label: '已完成', started_at: '2026-09-08T16:10:00Z', ended_at: '2026-09-08T17:00:00Z', submitted_at: '2026-09-08T17:00:00Z', duration_minutes: 50, question_count: 6, behavior_count: 62 },
]
const responses = [
  { question_ref: 'Q001', question_type: '单选题', assigned_order: 1, first_visit_order: 1, visit_count: 2, clicked_times: ['2026-09-08T14:20:12Z'], entered_times: ['2026-09-08T14:20:15Z'], left_times: ['2026-09-08T14:28:30Z'], submitted_at: '2026-09-08T15:05:00Z', final_answer: 'B', status_label: '正式提交', is_correct: true },
  { question_ref: 'Q002', question_type: '多选题', assigned_order: 2, first_visit_order: 3, visit_count: 1, clicked_times: ['2026-09-08T14:35:40Z'], entered_times: ['2026-09-08T14:35:43Z'], left_times: ['2026-09-08T14:48:20Z'], submitted_at: '2026-09-08T15:05:00Z', final_answer: 'A, C', status_label: '正式提交', is_correct: false },
]
const visits = [
  { key: 'Q001:1', question_ref: 'Q001', visit_index: 1, sequence: 1, clicked_at: '2026-09-08T14:20:12Z', entered_at: '2026-09-08T14:20:15Z', left_at: '2026-09-08T14:28:30Z', duration_seconds: 495, problem: '' },
  { key: 'Q001:2', question_ref: 'Q001', visit_index: 2, sequence: 4, clicked_at: '2026-09-08T14:48:35Z', entered_at: '2026-09-08T14:48:37Z', left_at: '2026-09-08T14:56:10Z', duration_seconds: 453, problem: '' },
]
const behavior = [
  { question_ref: 'Q001', visit_index: 2, sequence: 1, category: '选择选项', action: '选择选项', occurred_at: '2026-09-08T14:48:37Z', before: 'B', after: 'C', details: 'B → C' },
  { question_ref: 'Q001', visit_index: 2, sequence: 2, category: '页面焦点', action: '页面失焦', occurred_at: '2026-09-08T14:48:52Z', before: 'focused', after: 'blurred', details: 'focused → blurred' },
]

function response(view = mocks.route.query.view || 'overview') {
  return {
    course: { id: 7, name: '研究方法', code: 'KT101', term: '2026 秋', status: 'active' },
    scope: { values: { experiment_ids: [3, 5], class_id: 4 }, query: '', display: '六次验收班 · 实验班', is_single_version: false, is_complete: true },
    search: '', generated_at: '2026-09-09T08:34:06Z', tables, selected_table: tables[0], selected_experiment: null,
    session_options: sessions.map(item => ({ value: item.session_ref, label: item.session_ref + ' · ' + item.experiment_name })),
    preview: { columns: [{ name: 'session_ref', description: 'sessionID' }], rows: [['S-001']], page: { number: 1, size: 20, total_items: 3, total_pages: 1, start: 1, end: 3 }, data_status: '已观测' },
    view,
    workspace: {
      students: [{ id: 31, student_number: '20231001', name: '张同学' }, { id: 32, student_number: '20231002', name: '李同学' }],
      student: view === 'overview' ? null : { id: 31, student_number: '20231001', name: '张同学', class_name: '2026 秋 计算机科学与技术 1 班', course_name: '研究方法', experiment_count: 3 },
      sessions,
      session: ['session', 'question', 'timeline'].includes(view) ? sessions[0] : null,
      responses, visits, behavior,
      question: ['question', 'timeline'].includes(view) ? responses[0] : null,
      selected_visit: view === 'timeline' ? 'Q001:2' : null,
    },
  }
}

describe('TeacherDataAdvanced', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.applyRouteQuery({ view: 'overview', experiment_ids: '3,5', class_id: '4', table: 'sessions' })
    mocks.api.advanced.mockImplementation(async () => response())
    mocks.api.advancedStudents.mockResolvedValue({ items: [{ id: 31, student_number: '20231001', name: '张同学' }], limit: 20 })
  })

  it('keeps its page heading when the research scope is incomplete', async () => {
    const incomplete = response()
    incomplete.scope.is_complete = false
    mocks.api.advanced.mockResolvedValueOnce(incomplete)
    const wrapper = mount(TeacherDataAdvanced)
    await flushPromises()
    expect(wrapper.findAll('h1')).toHaveLength(1)
    expect(wrapper.get('h1').text()).toBe('研究数据预览')
    expect(wrapper.text()).toContain('请先选择实验和班级')
  })

  it('shows six cards and applies a uniquely selected student', async () => {
    const wrapper = mount(TeacherDataAdvanced)
    await flushPromises()
    expect(wrapper.get('h1').text()).toBe('研究数据预览')
    expect(wrapper.findAll('[data-testid="research-card"]')).toHaveLength(6)
    expect(wrapper.text()).toContain('课程题目固定库')
    expect(wrapper.text()).toContain('课程知识点库')
    expect(wrapper.text()).toContain('某会话中学生对一道题的最终作答事实')
    expect(wrapper.text()).not.toContain('实际分配的实验题目内容')
    expect(wrapper.text()).not.toContain('某会话中分题的学生的一道题')
    await wrapper.get('input[aria-label="按学生姓名或学号搜索"]').setValue('张')
    await wrapper.get('[data-testid="student-suggestion"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('步骤 1/5：筛选单一学生')
    expect((wrapper.get('input[aria-label="按学生姓名或学号搜索"]').element as HTMLInputElement).value).toBe('张同学 / 20231001')
    expect(mocks.push).not.toHaveBeenCalled()
    await wrapper.get('form[role="search"]').trigger('submit')
    expect(mocks.push).toHaveBeenLastCalledWith({ query: expect.objectContaining({ view: 'student', student_id: '31' }) })
  })

  it('selects a session in student overview and opens its full data', async () => {
    mocks.applyRouteQuery({ view: 'student', experiment_ids: '3,5', class_id: '4', student_id: '31' })
    const wrapper = mount(TeacherDataAdvanced)
    await flushPromises()
    expect(wrapper.text()).toContain('学生数据总览')
    await wrapper.get('[data-session-ref="S-002"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('实验二：决策树建模')
    await wrapper.get('[data-testid="open-session"]').trigger('click')
    expect(mocks.push).toHaveBeenLastCalledWith({ query: expect.objectContaining({ view: 'session', session_ref: 'S-002' }) })
  })

  it('opens a response and the session behavior timeline through query state', async () => {
    mocks.applyRouteQuery({ view: 'session', experiment_ids: '3,5', class_id: '4', student_id: '31', session_ref: 'S-001' })
    const wrapper = mount(TeacherDataAdvanced)
    await flushPromises()
    expect(wrapper.text()).toContain('会话详情')
    await wrapper.get('[data-card-key="behavior_events"]').trigger('click')
    expect(mocks.push).toHaveBeenLastCalledWith({ query: expect.objectContaining({ view: 'timeline' }) })
    mocks.applyRouteQuery({ view: 'session', experiment_ids: '3,5', class_id: '4', student_id: '31', session_ref: 'S-001' })
    await flushPromises()
    await wrapper.get('[data-question-ref="Q001"]').trigger('click')
    expect(mocks.push).toHaveBeenLastCalledWith({ query: expect.objectContaining({ view: 'question', question_ref: 'Q001' }) })
  })

  it('keeps visit selection in the URL and responds to browser history', async () => {
    mocks.applyRouteQuery({ view: 'timeline', experiment_ids: '3,5', class_id: '4', student_id: '31', session_ref: 'S-001', question_ref: 'Q001', visit: 'Q001:2' })
    const wrapper = mount(TeacherDataAdvanced)
    await flushPromises()
    expect(wrapper.text()).toContain('单一学生全过程')
    await wrapper.get('[data-visit-key="Q001:1"]').trigger('click')
    expect(mocks.push).toHaveBeenLastCalledWith({ query: expect.objectContaining({ visit: 'Q001:1' }) })
    mocks.applyRouteQuery({ view: 'question', experiment_ids: '3,5', class_id: '4', student_id: '31', session_ref: 'S-001', question_ref: 'Q002' })
    await flushPromises()
    expect(mocks.api.advanced).toHaveBeenLastCalledWith(7, expect.objectContaining({ view: 'question', question_ref: 'Q002' }), expect.any(AbortSignal))
    expect(wrapper.text()).toContain('题目行为过程')
  })

  it('searches students remotely with a bounded endpoint', async () => {
    vi.useFakeTimers()
    const wrapper = mount(TeacherDataAdvanced)
    await flushPromises()
    await wrapper.get('input[aria-label="按学生姓名或学号搜索"]').setValue('张')
    await wrapper.get('input[aria-label="按学生姓名或学号搜索"]').trigger('input')
    await vi.advanceTimersByTimeAsync(250)
    await flushPromises()
    expect(mocks.api.advancedStudents).toHaveBeenCalledWith(
      7,
      expect.objectContaining({ q: '张' }),
      expect.any(AbortSignal),
    )
    expect(wrapper.findAll('[data-testid="student-suggestion"]')).toHaveLength(1)
    vi.useRealTimers()
  })

  it('aborts superseded data requests and exposes refresh busy state', async () => {
    const resolvers: Array<(value: ReturnType<typeof response>) => void> = []
    mocks.api.advanced.mockImplementation(() => new Promise(resolve => resolvers.push(resolve)))
    const wrapper = mount(TeacherDataAdvanced)
    expect(wrapper.text()).toContain('正在加载研究数据')
    resolvers.shift()?.(response())
    await flushPromises()

    mocks.applyRouteQuery({ ...mocks.route.query, table: 'questions' })
    await flushPromises()
    const firstRefreshSignal = mocks.api.advanced.mock.calls.at(-1)?.[2] as AbortSignal
    expect(wrapper.get('[aria-busy="true"]')).toBeTruthy()
    expect(wrapper.text()).toContain('正在刷新研究数据')
    mocks.applyRouteQuery({ ...mocks.route.query, table: 'knowledge_points' })
    await flushPromises()
    expect(firstRefreshSignal.aborted).toBe(true)
    resolvers.at(-1)?.(response())
    await flushPromises()
  })

  it('shows materialization preparation state and navigates behavior with its fixed cursor', async () => {
    const payload = response() as ReturnType<typeof response> & {
      preparation: { state: string; message: string; updated_at: null; result_version: null; projection_version: string; stale: boolean }
      preview: ReturnType<typeof response>['preview'] & { cursor: { previous: null; next: string; result_version: string } }
    }
    payload.preparation = {
      state: 'preparing',
      message: '会话已结束，研究数据正在后台整理。',
      updated_at: null,
      result_version: null,
      projection_version: 'v1',
      stale: false,
    }
    payload.selected_table = tables[5]
    payload.preview.cursor = { previous: null, next: 'signed-next', result_version: 'fixed-v1' }
    mocks.api.advanced.mockResolvedValueOnce(payload)
    const wrapper = mount(TeacherDataAdvanced)
    await flushPromises()

    expect(wrapper.get('[data-testid="preparation-state"]').text()).toContain('正在后台整理')
    await wrapper.get('nav[aria-label="行为记录翻页"] button:last-child').trigger('click')
    expect(mocks.push).toHaveBeenLastCalledWith({ query: expect.objectContaining({ cursor: 'signed-next' }) })
  })

  it('clears a behavior cursor when the selected session changes', async () => {
    mocks.applyRouteQuery({
      view: 'overview', experiment_ids: '3,5', class_id: '4',
      table: 'behavior_events', cursor: 'signed-old',
    })
    const wrapper = mount(TeacherDataAdvanced)
    await flushPromises()

    await wrapper.get('select[aria-label="选择作答会话"]').setValue('S-002')

    expect(mocks.push).toHaveBeenLastCalledWith({
      query: expect.objectContaining({
        session_ref: 'S-002', page: undefined, cursor: undefined,
      }),
    })
  })
})
