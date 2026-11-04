import { flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  api: {
    classTasks: vi.fn(),
    grading: vi.fn(),
    gradingQuestion: vi.fn(),
    gradeSubmission: vi.fn(),
  },
  push: vi.fn(),
  route: { params: { courseId: '7', experimentId: '11', taskId: '17' } },
}))

vi.mock('vue-router', () => ({ useRoute: () => mocks.route, useRouter: () => ({ push: mocks.push }) }))
vi.mock('./api', () => ({ getTeacherExperimentApi: () => mocks.api }))

import TeacherShortAnswerGrading from './TeacherShortAnswerGrading.vue'

describe('TeacherShortAnswerGrading', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.route.params = reactive({ courseId: '7', experimentId: '11', taskId: '17' })
    mocks.api.classTasks.mockResolvedValue({ items: [
      { id: 17, class_name: '一班', available_actions: ['grade_short_answers'], grading: { pending: 1 } },
      { id: 18, class_name: '二班', available_actions: ['grade_short_answers'], grading: { pending: 0 } },
      { id: 19, class_name: '三班', available_actions: [], grading: { pending: 0 } },
    ] })
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

  it('switches authorized classes and reloads task-scoped grading after navigation', async () => {
    const wrapper = mount(TeacherShortAnswerGrading)
    await flushPromises()
    const select = wrapper.get('select[aria-label="切换班级"]')
    expect(select.findAll('option').map(option => option.text())).toEqual(['一班 · 待批改：1', '二班 · 待批改：0'])
    await select.setValue('18')
    expect(mocks.push).toHaveBeenCalledWith('/teacher/courses/7/experiments/11/class-tasks/18/grading/')
    const second = structuredClone(await mocks.api.grading())
    second.task.id = 18
    second.task.class_name = '二班'
    second.totals.pending = 0
    mocks.api.grading.mockResolvedValue(second)
    mocks.route.params.taskId = '18'
    await flushPromises()
    expect(mocks.api.grading).toHaveBeenLastCalledWith(18)
    expect(mocks.api.gradingQuestion).toHaveBeenLastCalledWith(18, 41, 'pending', 1)
    expect(wrapper.text()).toContain('二班')
    await wrapper.get('[data-grade="correct"]').trigger('click')
    await flushPromises()
    expect(mocks.api.gradeSubmission.mock.calls[0]![0]).toBe(18)
  })

  it('shows frozen references and submits a binary decision', async () => {
    const wrapper = mount(TeacherShortAnswerGrading)
    await flushPromises()

    expect(wrapper.get('h1').text()).toBe('人工批改')
    expect(wrapper.get('[aria-current="page"]').text()).toBe('人工批改')
    expect(wrapper.get('[aria-label="批改进度"]').text()).toContain('人工批改题目')
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

    const navigation = wrapper.get('[aria-label="人工批改题目列表"]')
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

  it('preserves other students drafts when one grade is saved', async () => {
    const detail = structuredClone(await mocks.api.gradingQuestion())
    detail.submissions.items.push({ ...detail.submissions.items[0], submission_id: 84, display_name: '学生乙' })
    mocks.api.gradingQuestion.mockResolvedValue(detail)
    const wrapper = mount(TeacherShortAnswerGrading)
    await flushPromises()
    await wrapper.findAll('textarea')[0]!.setValue('甲的评语')
    await wrapper.findAll('textarea')[1]!.setValue('乙的草稿')
    await wrapper.findAll('[data-grade="correct"]')[0]!.trigger('click')
    await flushPromises()
    expect(wrapper.findAll('textarea')[0]!.element.value).toBe('')
    expect(wrapper.findAll('textarea')[1]!.element.value).toBe('乙的草稿')
  })

  it('keeps the previous question selected until a new detail loads and reports failure', async () => {
    const overview = await mocks.api.grading()
    overview.questions.push({ ...overview.questions[0], question_snapshot_id: 42, code: 'SA-2' })
    const wrapper = mount(TeacherShortAnswerGrading)
    await flushPromises()
    let rejectRequest!: (error: Error) => void
    mocks.api.gradingQuestion.mockImplementationOnce(() => new Promise((_, reject) => { rejectRequest = reject }))
    const questions = wrapper.findAll('[aria-label="人工批改题目列表"] button')
    await questions[1]!.trigger('click')
    const selectedWhileLoading = questions[0]!.attributes('aria-pressed')
    const gradeDisabled = wrapper.get('[data-grade="correct"]').attributes('disabled')
    rejectRequest(new Error('题目读取失败'))
    await flushPromises()
    expect(selectedWhileLoading).toBe('true')
    expect(gradeDisabled).toBeDefined()
    expect(questions[0]!.attributes('aria-pressed')).toBe('true')
    expect(wrapper.get('[aria-label="题目与参考"]').text()).toContain('SA-1')
    expect(wrapper.get('[role="alert"]').text()).toContain('题目读取失败')
    mocks.api.gradingQuestion.mockResolvedValueOnce({ ...(await mocks.api.gradingQuestion()), question: { question_snapshot_id: 42, code: 'SA-2' } })
    await questions[1]!.trigger('click')
    await flushPromises()
    expect(questions[1]!.attributes('aria-pressed')).toBe('true')
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  })

  it('does not advance the page or filter when their requests fail', async () => {
    const detail = structuredClone(await mocks.api.gradingQuestion())
    detail.submissions.page.total_pages = 2
    mocks.api.gradingQuestion.mockResolvedValue(detail)
    const wrapper = mount(TeacherShortAnswerGrading)
    await flushPromises()
    mocks.api.gradingQuestion.mockRejectedValueOnce(new Error('分页读取失败'))
    await wrapper.findAll('[aria-label="批改分页"] button')[1]!.trigger('click')
    await flushPromises()
    expect(wrapper.get('[aria-label="批改分页"]').text()).toContain('第 1 / 2 页')
    expect(wrapper.get('[role="alert"]').text()).toContain('分页读取失败')
    mocks.api.gradingQuestion.mockRejectedValueOnce(new Error('筛选读取失败'))
    await wrapper.findAll('[aria-label="筛选作答"] button')[0]!.trigger('click')
    await flushPromises()
    expect(wrapper.findAll('[aria-label="筛选作答"] button')[1]!.attributes('aria-pressed')).toBe('true')
    expect(wrapper.get('[role="alert"]').text()).toContain('筛选读取失败')
  })

  it('releases the loading state when navigation changes to an empty class', async () => {
    const wrapper = mount(TeacherShortAnswerGrading)
    await flushPromises()
    let finish!: (result: unknown) => void
    const previous = await mocks.api.gradingQuestion()
    mocks.api.gradingQuestion.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    await wrapper.findAll('[aria-label="筛选作答"] button')[0]!.trigger('click')
    mocks.api.grading.mockResolvedValue({ ...(await mocks.api.grading()), questions: [] })
    mocks.route.params.taskId = '18'
    await flushPromises()
    finish(previous)
    await flushPromises()
    expect(wrapper.get('select[aria-label="切换班级"]').attributes('disabled')).toBeUndefined()
    expect(wrapper.find('[data-grading-row]').exists()).toBe(false)
  })
})
