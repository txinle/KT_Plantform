import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const route = {
  params: {
    courseId: '7',
    classId: '11',
    studentId: '23',
    experimentId: '41',
    questionId: '59',
  },
}
const mocks = vi.hoisted(() => ({
  api: {
    studentDetail: vi.fn(),
    studentExperimentDetail: vi.fn(),
    questionDetail: vi.fn(),
    attemptProcess: vi.fn(),
  },
}))

vi.mock('vue-router', () => ({ useRoute: () => route }))
vi.mock('./api', () => ({ getTeacherAnalysisApi: () => mocks.api }))

import TeacherAttemptProcess from './TeacherAttemptProcess.vue'
import TeacherQuestionDetail from './TeacherQuestionDetail.vue'
import TeacherStudentDetail from './TeacherStudentDetail.vue'
import TeacherStudentExperimentDetail from './TeacherStudentExperimentDetail.vue'

function metric(percentDisplay = '50.0%') {
  return {
    numerator: '1.00000000',
    denominator: '2.00000000',
    ratio: '0.50000000',
    state: 'available',
    sample_size: 2,
    percent_display: percentDisplay,
  }
}

function base() {
  return {
    course: { id: 7, name: '研究方法', code: 'KT101', term: '2026 秋', status: 'active' },
    class_group: { id: 11, name: '实验一班', academic_year: '2026' },
  }
}

describe('teacher analysis views', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.api.studentDetail.mockResolvedValue({
      ...base(),
      actions: { back_url: '/teacher/courses/7/classes/11/?section=students' },
      detail: {
        generated_at: '2026-09-01T00:00:00Z', student_id: 23, student_number: 'S001', display_name: '测试学生',
        completed_experiments: 1, eligible_experiments: 2, correct_rate: metric(), score_rate: metric(),
        average_answer_time_ms: '1000.000', average_answer_time_display: '1.0s',
        show_trend_chart: true,
        trend_rows: [{ experiment_id: 41, name: '电路实验', student_correct_rate: metric(), class_average_correct_rate: metric('75.0%'), x: '5.00000000', student_y: '50.00000000', class_y: '25.00000000' }],
        kc_rows: [{ name: 'KVL', comparison: { student_value: metric(), class_average: metric('75.0%'), sample_size: 2 } }],
        experiments: [{ experiment_id: 41, name: '电路实验', completed: true, correct_rate: metric(), score_rate: metric(), rank: 1, rank_sample_size: 2, average_answer_time_ms: '1000.000', average_answer_time_display: '1.0s', detail_url: '/teacher/courses/7/classes/11/students/23/experiments/41/' }],
      },
    })
    mocks.api.studentExperimentDetail.mockResolvedValue({
      ...base(), actions: { back_url: '/teacher/courses/7/classes/11/?section=experiments&experiment_id=41' },
      detail: {
        generated_at: '2026-09-01T00:00:00Z', experiment_id: 41, experiment_name: '电路实验', student_id: 23, student_number: 'S001', display_name: '历史学生', is_current_member: false, completed: true,
        correct_rate: metric(), score_rate: metric(), average_answer_time_ms: '1000.000', average_answer_time_display: '1.0s', rank: 2, rank_sample_size: 2,
        questions: [{ question_id: 59, code: 'Q-1', kcs: ['KVL'], result_label: '正确', first_answer: 'A', final_answer: 'B', revision_count: 1, answer_time_ms: '1000.000', answer_time_display: '1.0s', process_complete: true, question_url: '/teacher/courses/7/classes/11/experiments/41/questions/59/', process_url: '/teacher/courses/7/classes/11/experiments/41/questions/59/students/23/process/' }],
      },
    })
    mocks.api.questionDetail.mockResolvedValue({
      ...base(), actions: { back_url: '/teacher/courses/7/classes/11/?section=experiments&experiment_id=41' },
      detail: {
        generated_at: '2026-09-01T00:00:00Z', experiment_id: 41, experiment_name: '电路实验', question_id: 59, code: 'Q-1', text: '题干', question_type: 'single_choice', kcs: ['KVL'],
        eligible_count: 2, current_member_count: 1, historical_participant_count: 1, answer_count: 2, correct_count: 1, incorrect_count: 1, correct_rate: metric(), chain_coverage: metric(),
        timing: { median_display: '1.0s', iqr_display: '1.0s–1.0s', sample_size: 2, quality: { submitted_count: 2, exact_count: 2, estimated_count: 0, wall_clock_count: 0, unavailable_count: 0 } },
        option_rows: [{ key: 'A', label: '选项 A', count: 1, is_correct: false }, { key: 'B', label: '选项 B', count: 1, is_correct: true }],
        transition_applicability: 'applicable', transition_rows: [{ key: 'incorrect_to_correct', label: '首答错误 → 终答正确', count: 1 }],
        participants: [{ student_id: 23, student_number: 'S001', display_name: '历史学生', is_current_member: false, result_label: '正确', final_answer: 'B', process_complete: false, student_experiment_url: '/teacher/courses/7/classes/11/students/23/experiments/41/', process_url: '/teacher/courses/7/classes/11/experiments/41/questions/59/students/23/process/' }],
      },
    })
    mocks.api.attemptProcess.mockResolvedValue({
      ...base(), actions: { back_url: '/teacher/courses/7/classes/11/experiments/41/questions/59/' },
      detail: {
        generated_at: '2026-09-01T00:00:00Z', experiment_id: 41, experiment_name: '电路实验', question_id: 59, question_code: 'Q-1', question_text: '题干', student_id: 23, student_number: 'S001', display_name: '历史学生', is_current_member: false, result_label: '正确', score_display: '1 / 1',
        process: { question_type: 'single_choice', completeness: 'incomplete', completeness_label: '不完整链', first_value: null, final_value: 'B', revision_count: null, steps: [{ label: '选择', occurred_at: '2026-09-01T00:00:00Z', value: 'B', length_before: null, length_after: null, time_source: 'server_received_at', interval_ms: 50 }] },
        coverage_status_title: '记录不完整', coverage_status_description: '存在可确认事实。', coverage_time_title: '同步估算', coverage_time_description: '时间来自同步估算。', coverage_reason_labels: ['跨页面顺序不确定'], has_cross_instance_order_uncertainty: true,
      },
    })
  })

  it('renders the server-projected student trend and fact actions', async () => {
    const wrapper = mount(TeacherStudentDetail)
    await flushPromises()

    expect(wrapper.get('h1').text()).toContain('测试学生')
    expect(wrapper.find('svg[role="img"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('学生本人')
    expect(wrapper.get('a[href*="/students/23/experiments/41/"]').text()).toContain('查看逐题事实')
  })

  it('renders historical membership and per-question process actions', async () => {
    const wrapper = mount(TeacherStudentExperimentDetail)
    await flushPromises()

    expect(wrapper.text()).toContain('历史参与者')
    expect(wrapper.text()).toContain('查看作答过程')
    expect(wrapper.get('a[href*="/questions/59/"]').text()).toContain('Q-1')
  })

  it('renders question timing quality and explicit process availability', async () => {
    const wrapper = mount(TeacherQuestionDetail)
    await flushPromises()

    expect(wrapper.text()).toContain('时长质量构成')
    expect(wrapper.text()).toContain('历史参与者')
    expect(wrapper.text()).toContain('查看可用事实')
    expect(wrapper.get('progress').attributes('max')).toBe('2')
  })

  it('renders incomplete process and cross-instance uncertainty without inventing steps', async () => {
    const wrapper = mount(TeacherAttemptProcess)
    await flushPromises()

    expect(wrapper.text()).toContain('不完整链')
    expect(wrapper.text()).toContain('跨页面顺序依据同步估计')
    expect(wrapper.text()).toContain('选择')
    expect(wrapper.text()).toContain('最终答案')
  })
})
