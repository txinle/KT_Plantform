import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const route = { params: { courseId: '7', experimentId: '41', sessionId: '88' }, query: {} as Record<string, string> }
const mocks = vi.hoisted(() => ({
  api: { experimentResultsResolution: vi.fn(), experimentSessionResolution: vi.fn() },
  push: vi.fn(),
}))

vi.mock('vue-router', () => ({ useRoute: () => route, useRouter: () => ({ push: mocks.push }) }))
vi.mock('./api', () => ({ getTeacherAnalysisApi: () => mocks.api }))
vi.mock('@/app/bootstrap', () => ({ getBootstrapContext: () => ({ csrf_token: 'csrf-token' }) }))

import TeacherExperimentResultsCompatibility from './TeacherExperimentResultsCompatibility.vue'
import TeacherExperimentSessionCompatibility from './TeacherExperimentSessionCompatibility.vue'

function base() {
  return {
    course: { id: 7, name: '研究方法', code: 'KT101', term: '2026 秋', status: 'active' },
    experiment: { id: 41, name: '电路实验', status: 'published' },
  }
}

describe('teacher experiment compatibility pages', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    route.query = {}
    mocks.api.experimentResultsResolution.mockResolvedValue({
      ...base(), kind: 'class_tasks', canonical_url: null, sessions: [], session_status_choices: [], selected_status: '',
      tasks: [{ academic_year: '2026', class_name: '实验一班', class_snapshot_name: '实验一班（发布时）', analysis_url: '/teacher/courses/7/classes/11/?section=experiments&experiment_id=41' }],
      actions: { back_url: '/teacher/courses/7/experiments/', clear_filter_url: '/teacher/courses/7/experiments/41/results/' },
    })
    mocks.api.experimentSessionResolution.mockResolvedValue({
      ...base(), kind: 'legacy_report', canonical_url: null,
      report: {
        student_name: '测试学生', student_number: 'S001', class_snapshot_name: '实验一班（发布时）', version_number: 2, session_status: '已完成', question_count: 1,
        questions: [{ assigned_sequence_index: 1, stable_code: 'C7-Q1', question_type: 'single_choice', question_type_label: '单选题', text: '题干', options: [{ key: 'A', text: '选项 A', display: 'A：选项 A' }], student_answer: 'A：选项 A', correct_answer: 'A：选项 A', answer_status: '回答正确' }],
      },
      actions: { back_url: '/teacher/courses/7/experiments/41/results/', word_url: '/teacher/courses/7/experiments/41/sessions/88/word/' },
    })
  })

  it('renders class task facts when no single canonical destination exists', async () => {
    const wrapper = mount(TeacherExperimentResultsCompatibility)
    await flushPromises()

    expect(wrapper.text()).toContain('请选择要查看的班级')
    expect(wrapper.text()).toContain('实验一班（发布时）')
    expect(wrapper.get('a[href*="/classes/11/"]').text()).toContain('查看班级分析')
  })

  it('renders display-safe legacy session report data and keeps Word download action', async () => {
    const wrapper = mount(TeacherExperimentSessionCompatibility)
    await flushPromises()

    expect(wrapper.text()).toContain('测试学生')
    expect(wrapper.text()).toContain('C7-Q1')
    expect(wrapper.text()).toContain('回答正确')
    expect(wrapper.get('form').attributes('action')).toContain('/word/')
    expect(wrapper.get('input[name="csrfmiddlewaretoken"]').attributes('value')).toBe('csrf-token')
  })
})
