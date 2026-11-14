import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ api: { studentPreview: vi.fn() } }))
vi.mock('vue-router', () => ({ useRoute: () => ({ params: { courseId: '7', experimentId: '13' } }) }))
vi.mock('./api', () => ({ getTeacherExperimentApi: () => mocks.api }))
vi.mock('@/app/bootstrap', () => ({ getBootstrapContext: () => ({ csrf_token: 'csrf', user: { display_name: '老师', username: 'teacher' } }) }))

import TeacherStudentPreview from './TeacherStudentPreview.vue'

const choice = { key: 'item-1', kind: 'single_choice', maximum_score: '2.00', group: null,
  question: { item_key: '1', question_type: 'single_choice', educational_type: '', title: '', instructions: '', text: 'Select one', options: [{ key: 'A', text: 'First' }, { key: 'B', text: 'Second' }], max_score: '2.00' } }
const essay = { key: 'item-2', kind: 'essay', maximum_score: '10.00', group: null,
  question: { item_key: '2', question_type: 'short_answer', educational_type: 'essay', title: 'Writing', instructions: 'Write a story', text: 'A garden', options: [], max_score: '10.00' } }

describe('TeacherStudentPreview', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.api.studentPreview.mockResolvedValue({ experiment_id: 13, experiment_name: '模拟实验', source: 'frozen', units: [choice, essay] })
  })

  it('uses separate legacy blank inputs and restores or clears their local answers', async () => {
    const blank = { key: 'item-3', kind: 'fill_blank', maximum_score: '1.00', group: null,
      question: { item_key: '3', question_type: 'fill_blank', text: 'Io=____ mA；量纲____。', options: [], answer_blank_count: 2 } }
    mocks.api.studentPreview.mockResolvedValue({ experiment_id: 13, experiment_name: '多空模拟', source: 'frozen', units: [blank, essay] })
    const wrapper = mount(TeacherStudentPreview)
    try {
      await flushPromises()
      expect(wrapper.findAll('[data-legacy-answer-area] input')).toHaveLength(2)
      await wrapper.get('input[aria-label="第 1 空"]').setValue('-8')
      await wrapper.get('input[aria-label="第 2 空"]').setValue('无量纲')
      await wrapper.get('[data-next-question]').trigger('click')
      await wrapper.get('[data-previous-question]').trigger('click')
      expect(wrapper.get<HTMLInputElement>('input[aria-label="第 1 空"]').element.value).toBe('-8')
      expect(wrapper.get<HTMLInputElement>('input[aria-label="第 2 空"]').element.value).toBe('无量纲')
      await wrapper.get('.kt-preview-clear').trigger('click')
      expect(wrapper.findAll<HTMLInputElement>('[data-legacy-answer-area] input').map(input => input.element.value)).toEqual(['', ''])
      expect(mocks.api.studentPreview).toHaveBeenCalledTimes(1)
    } finally { wrapper.unmount() }
  })

  it('keeps simulated answers in memory and ends with explicit return and restart actions', async () => {
    const wrapper = mount(TeacherStudentPreview, { attachTo: document.body })
    await flushPromises()
    expect(mocks.api.studentPreview).toHaveBeenCalledWith(7, 13)
    expect(wrapper.text()).toContain('不生成正式提交')
    expect(wrapper.get('.kt-assessment-layout').attributes('data-question-layout')).toBe('compact')
    await wrapper.get('input[value="A"]').setValue(true)
    await wrapper.get('[data-next-question]').trigger('click')
    expect(wrapper.get('.kt-assessment-layout').attributes('data-question-layout')).toBe('writing')
    expect(wrapper.get('textarea').attributes('rows')).toBe('12')
    expect(wrapper.find('.kt-assessment-question-body textarea').exists()).toBe(true)
    expect(wrapper.find('.kt-assessment-question-body .kt-assessment-paper-actions').exists()).toBe(false)
    wrapper.get<HTMLElement>('.kt-assessment-question-body').element.scrollTop = 80
    await wrapper.get('textarea').setValue('My own story')
    await wrapper.get('[data-previous-question]').trigger('click')
    expect(wrapper.get<HTMLInputElement>('input[value="A"]').element.checked).toBe(true)
    expect(wrapper.get<HTMLElement>('.kt-assessment-question-body').element.scrollTop).toBe(0)
    await wrapper.get('[data-finish-preview]').trigger('click')
    expect(wrapper.text()).toContain('模拟已结束')
    expect(wrapper.find('.kt-assessment-layout').exists()).toBe(false)
    const completion = wrapper.get('[data-preview-complete]')
    expect(completion.text()).toContain('本次作答未提交，也不会计入学生或研究数据')
    expect(completion.get('[data-preview-return]').attributes('href')).toBe('/teacher/courses/7/experiments/')
    expect(document.activeElement).toBe(completion.get('h1').element)
    await wrapper.get('[data-reset-preview]').trigger('click')
    expect(wrapper.find('[data-preview-complete]').exists()).toBe(false)
    expect(wrapper.get<HTMLInputElement>('input[value="A"]').element.checked).toBe(false)
    expect(wrapper.get<HTMLInputElement>('input[value="A"]').element.disabled).toBe(false)
    expect(document.activeElement).toBe(wrapper.get('#preview-question-title').element)
    expect(mocks.api.studentPreview).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('replaces the answer screen with a focused completion state even with no answers', async () => {
    const wrapper = mount(TeacherStudentPreview, { attachTo: document.body })
    try {
      await flushPromises()
      await wrapper.get('[data-finish-preview]').trigger('click')
      await flushPromises()
      expect(wrapper.find('.kt-assessment-layout').exists()).toBe(false)
      expect(wrapper.get('[data-preview-complete]').text()).toContain('模拟已结束')
      expect(document.activeElement).toBe(wrapper.get('[data-preview-complete] h1').element)
      expect(mocks.api.studentPreview).toHaveBeenCalledTimes(1)
    } finally { wrapper.unmount() }
  })

  it('reveals the current tab without scrolling the document', async () => {
    const bounds = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      const left = this.matches('.kt-group-tabs button[aria-current]') ? 200 : 0
      return { x: left, y: 0, left, right: left + 100, top: 0, bottom: 50, width: 100, height: 50, toJSON: () => ({}) }
    })
    const wrapper = mount(TeacherStudentPreview)
    try {
      await flushPromises()
      await wrapper.get('[data-next-question]').trigger('click')
      expect(wrapper.get<HTMLElement>('.kt-group-tabs').element.scrollLeft).toBe(200)
    } finally { wrapper.unmount(); bounds.mockRestore() }
  })

  it('renders a complete reading group and keeps sibling responses during navigation', async () => {
    const group = { group_id: '4', kind: 'reading', title: 'Garden', instructions: '', material: 'Shared material', content_language: 'en', material_segments: [], members: [1, 2].map(id => ({ item_key: String(id + 10), question_type: 'single_choice', text: `Why ${id}?`, options: [{ key: 'A', text: 'Reason' }], group_order: id, blank_id: '', max_score: '1.00' })) }
    mocks.api.studentPreview.mockResolvedValue({ experiment_id: 13, experiment_name: '阅读模拟', source: 'draft', units: [{ key: 'group-4', kind: 'reading', maximum_score: '2.00', question: null, group }, essay] })
    const wrapper = mount(TeacherStudentPreview)
    await flushPromises()
    expect(wrapper.get('.kt-assessment-layout').attributes('data-question-layout')).toBe('reading')
    await wrapper.get('[data-group-question="12"] input').setValue(true)
    await wrapper.get('[data-next-question]').trigger('click')
    await wrapper.get('[data-previous-question]').trigger('click')
    expect(wrapper.get<HTMLInputElement>('[data-group-question="12"] input').element.checked).toBe(true)
    wrapper.unmount()
  })

  it('shows server errors and a safe return destination rather than fabricated questions', async () => {
    mocks.api.studentPreview.mockRejectedValue(new Error('无法生成模拟题目：尚未配置题库'))
    const wrapper = mount(TeacherStudentPreview)
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('尚未配置题库')
    expect(wrapper.find('.kt-assessment-question').exists()).toBe(false)
    expect(wrapper.get('a[href="/teacher/courses/7/experiments/"]').text()).toContain('返回实验')
    wrapper.unmount()
  })
})
