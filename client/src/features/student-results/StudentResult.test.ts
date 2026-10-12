import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  api: { learningExperiment: vi.fn(), learningExperimentStatus: vi.fn() },
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { courseId: '7', experimentId: '11' } }),
  RouterLink: {
    props: ['to'],
    template: '<a :href="to"><slot /></a>',
  },
}))
vi.mock('../student-courses/api', () => ({ getStudentCourseApi: () => mocks.api }))

import StudentResult from './StudentResult.vue'

const projection = {
  generated_at: '2026-08-31T11:11:56+08:00',
  course_id: 7,
  course_name: '认知追踪实验班',
  class_group_name: '一班',
  experiment_id: 11,
  experiment_name: '演示交互数据采集',
  status: 'completed',
  status_label: '已完成',
  completed: true,
  participated: true,
  correct_rate: { percent_display: '33.3%' },
  score_rate: { percent_display: '33.3%' },
  grading_coverage: { graded: 3, submitted: 3, is_partial: false },
  item_results: [
    {
      generated_at: '2026-08-31T11:11:56+08:00',
      question_id: 73,
      code: 'Q0073',
      kcs: ['数学基础', '数与运算', '基础运算', '加法运算'],
      result_label: '错误',
      result_state: 'incorrect',
      final_answer: '15',
      score: { generated_at: '2026-08-31T11:11:56+08:00', value: '0.00', maximum: '1.00' },
    },
    {
      generated_at: '2026-08-31T11:11:56+08:00',
      question_id: 90,
      code: 'Q0090',
      kcs: ['数学基础', '数与运算', '质数'],
      result_label: '正确',
      result_state: 'correct',
      final_answer: 'A：正确项；C：另一正确项',
      score: { generated_at: '2026-08-31T11:11:56+08:00', value: '1.00', maximum: '1.00' },
    },
  ],
}

describe('StudentResult', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(() => vi.useRealTimers())

  it('checks pending metadata then loads ready results once', async () => {
    vi.useFakeTimers()
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
    mocks.api.learningExperiment.mockResolvedValueOnce({ ...projection, comparison_status: 'pending' })
      .mockResolvedValue({ ...projection, comparison_status: 'ready' })
    mocks.api.learningExperimentStatus.mockResolvedValue({ version: 'ready', comparison_status: 'ready' })
    const wrapper = mount(StudentResult)
    await flushPromises()
    expect(wrapper.text()).toContain('班级比较正在生成')
    await vi.advanceTimersByTimeAsync(5_000)
    await flushPromises()
    expect(mocks.api.learningExperiment).toHaveBeenCalledTimes(2)
    expect(mocks.api.learningExperimentStatus).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(10_000)
    expect(mocks.api.learningExperiment).toHaveBeenCalledTimes(2)
    wrapper.unmount()
    await vi.advanceTimersByTimeAsync(10_000)
    expect(mocks.api.learningExperiment).toHaveBeenCalledTimes(2)
  })

  it('renders the authoritative learning projection as the P4 result page', async () => {
    mocks.api.learningExperiment.mockResolvedValue({ ...projection, rank: { position: 2, sample_size: 8 } })

    const wrapper = mount(StudentResult)
    await flushPromises()

    expect(mocks.api.learningExperiment).toHaveBeenCalledWith(7, 11)
    expect(wrapper.text()).toContain('演示交互数据采集')
    expect(wrapper.text()).toContain('只显示你的提交与教师已开放的结果反馈。')
    expect(wrapper.text()).toContain('统计生成时间：2026-08-31 11:11:56')
    expect(wrapper.findAll('.kt-feedback-metric')).toHaveLength(3)
    expect(wrapper.text()).toContain('33.3%')
    expect(wrapper.text()).toContain('本次作业排名')
    expect(wrapper.text()).toContain('第 2 / 8 名')
    expect(wrapper.find('.kt-result-layout').exists()).toBe(true)
    expect(wrapper.findAll('.kt-result-question-directory a')).toHaveLength(2)
    expect(wrapper.findAll('.kt-feedback-table tbody tr')).toHaveLength(2)
    expect(wrapper.find('.kt-result-state--incorrect').text()).toBe('错误')
    expect(wrapper.find('.kt-result-state--correct').text()).toBe('正确')
    expect(wrapper.find('a[href="/student/courses/7/learning/experiments/11/questions/73/"]').text()).toContain('查看题目反馈')
  })

  it('renders an explicit empty state when the server returns no item results', async () => {
    mocks.api.learningExperiment.mockResolvedValue({ ...projection, item_results: undefined })

    const wrapper = mount(StudentResult)
    await flushPromises()

    expect(wrapper.text()).toContain('当前没有可显示的逐题结果。')
  })

  it('renders the API error without inventing result data', async () => {
    mocks.api.learningExperiment.mockRejectedValue(new Error('教师未开放逐题结果。'))

    const wrapper = mount(StudentResult)
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('教师未开放逐题结果。')
    expect(wrapper.find('.kt-feedback-metrics').exists()).toBe(false)
  })
})
