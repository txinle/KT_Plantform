import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  api: {
    grading: vi.fn(),
    gradingQuestion: vi.fn(),
    gradeSubmission: vi.fn(),
  },
  route: { params: { courseId: '7', experimentId: '11', taskId: '17' } },
}))

vi.mock('vue-router', () => ({ useRoute: () => mocks.route }))
vi.mock('./api', () => ({ getTeacherExperimentApi: () => mocks.api }))

import TeacherShortAnswerGrading from './TeacherShortAnswerGrading.vue'

describe('TeacherShortAnswerGrading', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.api.grading.mockResolvedValue({
      task: { id: 17, experiment_id: 11, experiment_name: '实验', class_id: 5, class_name: '一班' },
      totals: { enabled: true, short_answer_questions: 1, submitted: 1, pending: 1, graded: 0 },
      questions: [{
        question_snapshot_id: 41, code: 'SA-1', text_summary: '说明变量关系',
        submitted_count: 1, pending_count: 1, graded_count: 0,
      }],
    })
    mocks.api.gradingQuestion.mockResolvedValue({
      question: {
        question_snapshot_id: 41, code: 'SA-1', text: '说明变量关系',
        correct_answer: '参考答案', accepted_answers: [], explanation: '参考解析',
      },
      submissions: {
        items: [{
          submission_id: 83, student_id: 9, student_number: 'S-001', display_name: '学生甲',
          final_answer: '学生回答', submitted_at: '2026-09-13T10:00:00Z', grading_status: 'pending',
          latest_grade_version: 1, current_is_correct: null, current_comment: '', graded_at: null,
          grader_name: '',
        }],
        page: { number: 1, size: 50, total_items: 1, total_pages: 1, start: 1, end: 1 },
      },
    })
    mocks.api.gradeSubmission.mockResolvedValue({
      submission: {
        submission_id: 83, student_id: 9, student_number: 'S-001', display_name: '学生甲',
        final_answer: '学生回答', submitted_at: '2026-09-13T10:00:00Z', grading_status: 'completed',
        latest_grade_version: 2, current_is_correct: true, current_comment: '',
        graded_at: '2026-09-13T11:00:00Z', grader_name: '教师',
      },
      question_progress: {
        question_snapshot_id: 41, code: 'SA-1', text_summary: '说明变量关系',
        submitted_count: 1, pending_count: 0, graded_count: 1,
      },
      task_progress: { enabled: true, short_answer_questions: 1, submitted: 1, pending: 0, graded: 1 },
    })
  })

  it('shows frozen references and submits a binary decision', async () => {
    const wrapper = mount(TeacherShortAnswerGrading)
    await flushPromises()

    expect(wrapper.text()).toContain('学生甲')
    expect(wrapper.text()).toContain('S-001')
    expect(wrapper.text()).toContain('参考答案')
    expect(wrapper.text()).toContain('学生回答')

    await wrapper.get('[data-grade="correct"]').trigger('click')
    await flushPromises()

    expect(mocks.api.gradeSubmission).toHaveBeenCalledWith(17, 41, 83, {
      expected_grade_version: 1,
      is_correct: true,
      comment: '',
    })
  })

  it('keeps question navigation compact and displays server pending counts', async () => {
    const overview = await mocks.api.grading()
    overview.questions.push({
      question_snapshot_id: 42, code: 'SA-2', text_summary: '另一道题的完整摘要',
      submitted_count: 4, pending_count: 0, graded_count: 4,
    })
    const wrapper = mount(TeacherShortAnswerGrading)
    await flushPromises()

    const navigation = wrapper.get('[aria-label="简答题列表"]')
    const questions = navigation.findAll('button')
    expect(questions.map(button => button.text())).toEqual([
      'SA-1待批 1', 'SA-2待批 0',
    ])
    expect(navigation.text()).not.toContain('说明变量关系')
    expect(navigation.text()).not.toContain('已批改')
    expect(questions[0]!.attributes('aria-pressed')).toBe('true')

    await questions[1]!.trigger('click')
    await flushPromises()
    expect(mocks.api.gradingQuestion).toHaveBeenLastCalledWith(17, 42, 'pending', 1)
    expect(questions[1]!.attributes('aria-pressed')).toBe('true')
  })

  it('separates references from the submission list and its filters', async () => {
    const wrapper = mount(TeacherShortAnswerGrading)
    await flushPromises()

    const reference = wrapper.get('[aria-label="题目与参考"]')
    const submissions = wrapper.get('[aria-label="学生作答与批改"]')
    expect(reference.text()).toContain('说明变量关系')
    expect(reference.text()).toContain('参考解析')
    expect(reference.find('textarea').exists()).toBe(false)
    expect(submissions.text()).toContain('学生回答')
    expect(submissions.find('[data-grade="correct"]').exists()).toBe(true)
    expect(submissions.find('[aria-label="筛选作答"]').exists()).toBe(true)
  })

  it('shows the existing note but requires a fresh reason before regrading', async () => {
    mocks.api.gradingQuestion.mockResolvedValueOnce({
      question: {
        question_snapshot_id: 41, code: 'SA-1', text: '说明变量关系',
        correct_answer: '参考答案', accepted_answers: [], explanation: '参考解析',
      },
      submissions: {
        items: [{
          submission_id: 83, student_id: 9, student_number: 'S-001', display_name: '学生甲',
          final_answer: '学生回答', submitted_at: '2026-09-13T10:00:00Z', grading_status: 'completed',
          latest_grade_version: 2, current_is_correct: true, current_comment: '首次批改备注',
          graded_at: '2026-09-13T11:00:00Z', grader_name: '教师',
        }],
        page: { number: 1, size: 50, total_items: 1, total_pages: 1, start: 1, end: 1 },
      },
    })
    const wrapper = mount(TeacherShortAnswerGrading)
    await flushPromises()

    expect(wrapper.text()).toContain('当前备注：首次批改备注')
    expect(wrapper.get('textarea').element.value).toBe('')
    await wrapper.get('[data-grade="incorrect"]').trigger('click')
    expect(wrapper.get('[role="alert"]').text()).toContain('修改已生效判定时必须填写原因')
    expect(mocks.api.gradeSubmission).not.toHaveBeenCalled()
  })
})
