import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  api: { learningExperiment: vi.fn() },
}))

vi.mock('../student-courses/api', () => ({ getStudentCourseApi: () => mocks.api }))

import StudentQuestionDirectory from './StudentQuestionDirectory.vue'

describe('StudentQuestionDirectory', () => {
  beforeEach(() => vi.clearAllMocks())

  it('uses the authorized experiment summary for normal document links without rendering answer content', async () => {
    mocks.api.learningExperiment.mockResolvedValue({
      item_results: [
        { question_id: 73, code: 'Q0073', final_answer: '不应出现在目录中的答案' },
        { question_id: 90, code: 'Q0090', final_answer: '也不应出现' },
      ],
    })

    const wrapper = mount(StudentQuestionDirectory, {
      props: { courseId: 7, experimentId: 11, questionId: 73 },
    })
    await flushPromises()

    expect(mocks.api.learningExperiment).toHaveBeenCalledWith(7, 11)
    expect(wrapper.findAll('a')).toHaveLength(2)
    expect(wrapper.get('a[aria-current="page"]').attributes('href')).toBe('/student/courses/7/learning/experiments/11/questions/73/')
    expect(wrapper.find('a[href="/student/courses/7/learning/experiments/11/questions/90/"]').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('不应出现在目录中的答案')
    expect(wrapper.text()).not.toContain('也不应出现')
  })
})
