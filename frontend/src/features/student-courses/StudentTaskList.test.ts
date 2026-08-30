import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  api: { tasks: vi.fn(), start: vi.fn(), resume: vi.fn() },
  push: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { courseId: '7' } }),
  useRouter: () => ({ push: mocks.push }),
}))
vi.mock('./api', () => ({ getStudentCourseApi: () => mocks.api }))

import StudentTaskList from './StudentTaskList.vue'

describe('StudentTaskList', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.api.tasks.mockResolvedValue({
      course: {
        course_id: 7,
        course_name: '知识追踪研究方法',
        course_code: 'KT101',
        course_term: '2026春',
        course_status: 'active',
        class_name: '实验一班',
      },
      items: [{
        task_id: 19,
        experiment_id: 11,
        experiment_name: '反馈策略实验',
        class_name: '2026 实验一班',
        business_status: 'open',
        opens_at: null,
        closes_at: null,
        progress: { completed: 0, total: 8 },
        primary_action: 'start',
        available_actions: ['start'],
        feedback_mode: 'correctness',
      }],
    })
  })

  it('renders authoritative task progress without correctness state', async () => {
    const wrapper = mount(StudentTaskList)
    await flushPromises()

    expect(wrapper.get('h1').text()).toBe('实验任务')
    expect(wrapper.text()).toContain('反馈策略实验')
    expect(wrapper.text()).toContain('0 / 8')
    expect(wrapper.get('[data-task-action="start"]').text()).toBe('开始实验')
    expect(wrapper.text()).not.toContain('正确')
    expect(wrapper.text()).not.toContain('错误')
  })
})
