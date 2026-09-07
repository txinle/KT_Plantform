import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  api: { learning: vi.fn() },
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { courseId: '7' } }),
  RouterLink: { template: '<a><slot /></a>' },
}))
vi.mock('../student-courses/api', () => ({ getStudentCourseApi: () => mocks.api }))

import StudentLearning from './StudentLearning.vue'

describe('StudentLearning', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.api.learning.mockResolvedValue({
      generated_at: '2026-08-30T00:00:00Z',
      course_id: 7,
      course_name: '知识追踪研究方法',
      course_code: 'KT101',
      course_term: '2026春',
      class_group_name: '实验一班',
      completion: { completed: 1, eligible: 2, rate: { percent_display: '50.0%' } },
      correct_rate: { percent_display: '75.0%' },
      score_rate: { percent_display: '80.0%' },
      score_grading_coverage: { graded: 3, submitted: 4, is_partial: true },
      comparisons: { correct_rate: { class_average: { percent_display: '70.0%' } } },
      kc_rows: [{ name: '数与运算', student_value: { percent_display: '75.0%' }, class_average: { percent_display: '70.0%' } }],
      trend_rows: [{ experiment_id: 11, name: '反馈策略实验', student_value: { percent_display: '75.0%' }, class_average: { percent_display: '70.0%' } }],
      experiments: [{
        experiment_id: 11,
        name: '反馈策略实验',
        status_label: '已完成',
        results_available: true,
        correct_rate: { percent_display: '75.0%' },
      }],
    })
  })

  it('uses the class-center dashboard and does not expose course-level ranking', async () => {
    const wrapper = mount(StudentLearning)
    await flushPromises()

    expect(wrapper.get('h1').text()).toBe('班级中心')
    expect(wrapper.text()).toContain('实验完成')
    expect(wrapper.text()).toContain('正确率')
    expect(wrapper.text()).toContain('已评分 3 / 4 题')
    expect(wrapper.text()).toContain('班级平均 70.0%')
    expect(wrapper.text()).toContain('知识点作答表现')
    expect(wrapper.text()).toContain('学习趋势')
    expect(wrapper.text()).toContain('反馈策略实验')
    expect(wrapper.find('table').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('排名')
    expect(wrapper.text()).not.toContain('位次')
  })

  it('does not render withheld comparison, KC, or trend panels', async () => {
    mocks.api.learning.mockResolvedValue({
      generated_at: '2026-08-30T00:00:00Z',
      course_id: 7,
      course_name: '知识追踪研究方法',
      course_code: 'KT101',
      course_term: '2026春',
      class_group_name: '实验一班',
      completion: { completed: 0, eligible: 1 },
      experiments: [],
    })

    const wrapper = mount(StudentLearning)
    await flushPromises()

    expect(wrapper.text()).not.toContain('班级平均')
    expect(wrapper.text()).not.toContain('知识点作答表现')
    expect(wrapper.text()).not.toContain('学习趋势')
  })

  it('does not style an unparticipated experiment as successful just because results are allowed', async () => {
    mocks.api.learning.mockResolvedValue({
      generated_at: '2026-08-30T00:00:00Z',
      course_id: 7,
      course_name: '知识追踪研究方法',
      course_code: 'KT101',
      course_term: '2026春',
      class_group_name: '实验一班',
      completion: { completed: 0, eligible: 1 },
      experiments: [{
        experiment_id: 11,
        name: '反馈策略实验',
        status: 'not_participated',
        status_label: '未参与',
        completed: false,
        participated: false,
        results_available: true,
      }],
    })

    const wrapper = mount(StudentLearning)
    await flushPromises()

    expect(wrapper.get('.kt-status').classes()).toContain('kt-status--neutral')
    expect(wrapper.get('.kt-status').classes()).not.toContain('kt-status--success')
  })
})
