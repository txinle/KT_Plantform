import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  api: { tasks: vi.fn(), result: vi.fn() },
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { courseId: '7', experimentId: '11' } }),
  RouterLink: { template: '<a><slot /></a>' },
}))
vi.mock('../student-courses/api', () => ({ getStudentCourseApi: () => mocks.api }))

import StudentResult from './StudentResult.vue'

describe('StudentResult', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.api.tasks.mockResolvedValue({
      course: { course_id: 7, course_name: '课程' },
      items: [{ task_id: 19, experiment_id: 11, experiment_name: '反馈策略实验' }],
    })
  })

  it('renders none feedback as completion metadata only', async () => {
    mocks.api.result.mockResolvedValue({
      feedback_mode: 'none',
      task_id: 19,
      completion_status: 'completed',
      submission_status: 'complete',
      completed_at: '2026-08-30T00:00:00Z',
      grading_status: 'completed',
    })

    const wrapper = mount(StudentResult)
    await flushPromises()

    expect(wrapper.text()).toContain('已完成')
    expect(wrapper.text()).toContain('本实验未开放作答反馈')
    expect(wrapper.text()).not.toContain('得分')
    expect(wrapper.text()).not.toContain('正确答案')
  })

  it('renders full feedback fields that the server included', async () => {
    mocks.api.result.mockResolvedValue({
      feedback_mode: 'full',
      task_id: 19,
      completion_status: 'completed',
      submission_status: 'complete',
      completed_at: '2026-08-30T00:00:00Z',
      grading_status: 'completed',
      score: { earned: '1.00', maximum: '1.00', percent: '100.0' },
      items: [{
        question_id: 13,
        question_code: 'Q1',
        submitted_answer: 'B：4',
        is_correct: true,
        score: '1.00',
        max_score: '1.00',
        correct_answer: 'B：4',
        explanation: '2 + 2 = 4。',
      }],
    })

    const wrapper = mount(StudentResult)
    await flushPromises()

    expect(wrapper.text()).toContain('得分')
    expect(wrapper.text()).toContain('B：4')
    expect(wrapper.text()).toContain('正确答案')
    expect(wrapper.text()).toContain('2 + 2 = 4。')
  })
})
