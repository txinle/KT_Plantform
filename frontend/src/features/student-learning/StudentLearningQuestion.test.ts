import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ api: { learningQuestion: vi.fn() } }))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { courseId: '7', experimentId: '11', questionId: '13' } }),
  RouterLink: { template: '<a><slot /></a>' },
}))
vi.mock('../student-courses/api', () => ({ getStudentCourseApi: () => mocks.api }))

import StudentLearningQuestion from './StudentLearningQuestion.vue'

describe('StudentLearningQuestion', () => {
  it('renders only the answer fields included by the server', async () => {
    mocks.api.learningQuestion.mockResolvedValue({
      code: 'Q1',
      experiment_name: '反馈策略实验',
      text: '2 + 2 等于多少？',
      result_label: '正确',
      final_answer: 'B：4',
    })

    const wrapper = mount(StudentLearningQuestion)
    await flushPromises()

    expect(wrapper.text()).toContain('B：4')
    expect(wrapper.text()).not.toContain('正确答案')
    expect(wrapper.text()).not.toContain('解析')
  })
})
