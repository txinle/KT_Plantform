import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ api: { learningQuestion: vi.fn() } }))

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
  beforeEach(() => vi.clearAllMocks())

  it('renders the P3 choice feedback with student and correct option states', async () => {
    mocks.api.learningQuestion.mockResolvedValue(fullQuestion)

    const wrapper = mount(StudentLearningQuestion)
    await flushPromises()

    expect(wrapper.text()).toContain('12 + 3 等于多少？')
    expect(wrapper.text()).toContain('统计生成时间：2026-08-31 11:10:43')
    expect(wrapper.findAll('.kt-feedback-option')).toHaveLength(4)
    expect(wrapper.find('.kt-feedback-option--student').text()).toContain('我的作答')
    expect(wrapper.find('.kt-feedback-option--correct').text()).toContain('正确答案')
    expect(wrapper.findAll('.kt-feedback-answer-panel')).toHaveLength(3)
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

    expect(wrapper.text()).toContain('学生作答')
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
