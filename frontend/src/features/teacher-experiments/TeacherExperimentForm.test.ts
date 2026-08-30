import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  api: {
    list: vi.fn(),
    detail: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
  },
  push: vi.fn(),
  route: {
    name: 'teacher-experiment-create',
    params: { courseId: '7' } as Record<string, string>,
  },
}))

vi.mock('vue-router', () => ({
  useRoute: () => mocks.route,
  useRouter: () => ({ push: mocks.push }),
}))

vi.mock('./api', () => ({
  getTeacherExperimentApi: () => mocks.api,
}))

import TeacherExperimentForm from './TeacherExperimentForm.vue'

describe('TeacherExperimentForm', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.route.name = 'teacher-experiment-create'
    mocks.route.params = { courseId: '7' }
    mocks.api.list.mockResolvedValue({
      items: [],
      page: { number: 1, size: 20, total_items: 0, total_pages: 1, start: 0, end: 0 },
      summary: { question_banks: [{ id: 3, name: '认知诊断题库' }], classes: [] },
    })
    mocks.api.create.mockResolvedValue({ id: 19 })
  })

  it('creates a complete teacher-owned experiment definition and opens its detail page', async () => {
    const wrapper = mount(TeacherExperimentForm)
    await flushPromises()

    const sections = wrapper.findAll('.kt-numbered-form-section')
    expect(sections).toHaveLength(4)
    expect(sections.map((section) => section.text())).toEqual(expect.arrayContaining([
      expect.stringContaining('基本信息'),
      expect.stringContaining('题目与呈现'),
      expect.stringContaining('作答规则'),
      expect.stringContaining('研究采集'),
    ]))
    expect(wrapper.find('input[name="selection_mode"][value="all"]').exists()).toBe(true)
    expect(wrapper.findAll('.kt-numbered-form-section .kt-compact-form-row').length).toBeGreaterThanOrEqual(4)
    expect(wrapper.find('#experiment-total-time').exists()).toBe(true)
    expect(wrapper.find('#experiment-question-time').exists()).toBe(true)

    await wrapper.get('#experiment-name').setValue('分数比较实验')
    await wrapper.get('#experiment-bank').setValue('3')
    await wrapper.get('input[name="time_mode"][value="total"]').setValue(true)
    await wrapper.get('#experiment-total-time').setValue('25')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(mocks.api.create).toHaveBeenCalledWith(
      7,
      expect.objectContaining({
        name: '分数比较实验',
        question_bank_id: 3,
        selection_mode: 'all',
        ordering_mode: 'fixed',
        feedback_mode: 'none',
        time_mode: 'total',
        total_time_limit_minutes: 25,
        per_question_time_limit_seconds: null,
        confidence_mode: 'off',
      }),
    )
    expect(mocks.push).toHaveBeenCalledWith('/teacher/courses/7/experiments/19/')
  })
})
