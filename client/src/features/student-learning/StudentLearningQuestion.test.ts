import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ api: { learningQuestion: vi.fn(), learningExperiment: vi.fn() }, record: vi.fn(), dispose: vi.fn() }))
vi.mock('@/assessment-runtime/session-controller', () => ({ disposeSessionController: vi.fn(), createProcessRuntime: async () => ({
  record: mocks.record, dispose: mocks.dispose, flushBounded: async () => true,
}) }))
vi.mock('@/app/bootstrap', () => ({ getBootstrapContext: () => ({ csrf_token: 'csrf' }) }))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { courseId: '7', experimentId: '11', questionId: '73' } }),
  RouterLink: {
    props: ['to'],
    template: '<a :href="to"><slot /></a>',
  },
}))
vi.mock('../student-courses/api', () => ({ getStudentCourseApi: () => mocks.api }))

import StudentLearningQuestion from './StudentLearningQuestion.vue'

const fullQuestion = {
  generated_at: '2026-08-31T11:10:43+08:00',
  course_id: 7,
  course_name: '认知追踪实验班',
  class_group_name: '一班',
  experiment_id: 11,
  experiment_name: '演示交互数据采集',
  question_id: 73,
  code: 'Q0073',
  question_type: 'single_choice',
  text: '12 + 3 等于多少？',
  kcs: ['数学基础', '数与运算', '基础运算', '加法运算'],
  result_label: '错误',
  result_state: 'incorrect',
  final_answer: 'C：15',
  options: [
    { value: 'A', label: 'A', text: '13', selected_by_student: false, is_correct: false },
    { value: 'B', label: 'B', text: '14', selected_by_student: false, is_correct: false },
    { value: 'C', label: 'C', text: '15', selected_by_student: true, is_correct: false },
    { value: 'D', label: 'D', text: '16', selected_by_student: false, is_correct: true },
  ],
  score: { generated_at: '2026-08-31T11:10:43+08:00', value: '0.00', maximum: '1.00' },
  correct_answer: 'D：16',
  explanation: '已知 12 + 3 = 15，因此本题若正确答案设为 16，则学生作答与标准答案不一致。',
}

describe('StudentLearningQuestion', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.api.learningExperiment.mockResolvedValue({
      item_results: [
        { question_id: 73, code: 'Q0073', final_answer: '15' },
        { question_id: 90, code: 'Q0090', final_answer: '质数' },
      ],
    })
  })

  it('preserves a long experiment label and all breadcrumb destinations', async () => {
    const name = '学生反馈实验-history-local-1010-'.repeat(8)
    mocks.api.learningQuestion.mockResolvedValue({ ...fullQuestion, experiment_name: name })
    const wrapper = mount(StudentLearningQuestion)
    await flushPromises()
    const breadcrumbs = wrapper.get('nav[aria-label="面包屑"]')
    expect(breadcrumbs.get('a').text()).toBe(name)
    expect(breadcrumbs.findAll('a').map((link) => link.attributes('href'))).toEqual([
      '/student/courses/7/', '/student/courses/7/learning/', '/student/courses/7/learning/experiments/11/',
    ])
    expect(breadcrumbs.get('[aria-current="page"]').text()).toBe('Q0073')
    wrapper.unmount()
  })

  it('capture-v2 logs actual expansion stages against a single detail visit', async () => {
    mocks.api.learningQuestion.mockResolvedValue({ ...fullQuestion, feedback_capture: {
      capture_version: 'capture-v2', mode: 'full', answer_event_id: 'answer-event', snapshot_id: 'snapshot',
      authorized: { correctness: true, correct_answer: true, explanation: true },
    } })
    const wrapper = mount(StudentLearningQuestion)
    await flushPromises()
    expect(wrapper.text()).not.toContain(fullQuestion.explanation)
    expect(wrapper.find('.kt-feedback-option--correct').exists()).toBe(false)
    await wrapper.get('[data-show-answer]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-show-explanation]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain(fullQuestion.explanation)
    wrapper.unmount()
    await flushPromises()
    const payloads = mocks.record.mock.calls.map((call) => call[1])
    expect(payloads.map((row) => row.stage)).toEqual(['detail_entered', 'answer_presented', 'explanation_presented', 'detail_left'])
    expect(new Set(payloads.map((row) => row.operation_id)).size).toBe(1)
    expect(payloads.at(-1)).toMatchObject({ included_correctness: true, included_correct_answer: true, included_explanation: true })
  })

  it('renders the P3 choice feedback with student and correct option states', async () => {
    mocks.api.learningQuestion.mockResolvedValue(fullQuestion)

    const wrapper = mount(StudentLearningQuestion)
    await flushPromises()

    expect(wrapper.text()).toContain('12 + 3 等于多少？')
    expect(wrapper.text()).toContain('统计生成时间：2026-08-31 11:10:43')
    expect(wrapper.findAll('.kt-feedback-option')).toHaveLength(4)
    expect(wrapper.find('.kt-question-detail-layout').exists()).toBe(true)
    expect(mocks.api.learningExperiment).toHaveBeenCalledWith(7, 11)
    expect(wrapper.findAll('.kt-question-directory a')).toHaveLength(2)
    expect(wrapper.get('.kt-question-directory a[aria-current="page"]').attributes('href')).toBe('/student/courses/7/learning/experiments/11/questions/73/')
    expect(wrapper.find('.kt-feedback-option--student').text()).toContain('我的作答')
    expect(wrapper.find('.kt-feedback-option--correct').text()).toContain('正确答案')
    expect(wrapper.findAll('.kt-feedback-answer-panel')).toHaveLength(3)
    expect(wrapper.find('.kt-feedback-answer-pair').exists()).toBe(true)
    expect(wrapper.find('.kt-feedback-answer-pair').element.compareDocumentPosition(wrapper.find('.kt-feedback-answer-panel--analysis').element) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(wrapper.text()).toContain('D：16')
    expect(wrapper.text()).toContain(fullQuestion.explanation)
  })

  it('does not infer or expose correct fields omitted by the API', async () => {
    const { correct_answer, explanation, ...correctnessOnly } = fullQuestion
    void correct_answer
    void explanation
    mocks.api.learningQuestion.mockResolvedValue({
      ...correctnessOnly,
      options: fullQuestion.options.map((option) => ({
        value: option.value,
        label: option.label,
        text: option.text,
        selected_by_student: option.selected_by_student,
      })),
    })

    const wrapper = mount(StudentLearningQuestion)
    await flushPromises()

    expect(wrapper.text()).toContain('我的正式作答')
    expect(wrapper.text()).not.toContain('正确答案')
    expect(wrapper.text()).not.toContain('答案解析')
    expect(wrapper.find('.kt-feedback-option--student').exists()).toBe(true)
    expect(wrapper.find('.kt-feedback-option--correct').exists()).toBe(false)
  })

  it('skips the option matrix for non-choice questions and labels an empty authorized explanation', async () => {
    mocks.api.learningQuestion.mockResolvedValue({
      ...fullQuestion,
      question_type: 'short_answer',
      text: '请说明加法结合律。',
      options: [],
      final_answer: '先加前两个数。',
      correct_answer: '改变分组不改变和。',
      explanation: '',
    })

    const wrapper = mount(StudentLearningQuestion)
    await flushPromises()

    expect(wrapper.find('.kt-feedback-options').exists()).toBe(false)
    expect(wrapper.text()).toContain('教师未填写解析')
  })

  it('renders the API error state', async () => {
    mocks.api.learningQuestion.mockRejectedValue(new Error('题目反馈不可用。'))

    const wrapper = mount(StudentLearningQuestion)
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('题目反馈不可用。')
    expect(wrapper.find('.kt-question-feedback-card').exists()).toBe(false)
  })
})
