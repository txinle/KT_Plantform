import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const route = { params: { courseId: '7', classId: '11' }, query: {} as Record<string, string> }
const mocks = vi.hoisted(() => ({
  api: { center: vi.fn() },
  push: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => route,
  useRouter: () => ({ push: mocks.push }),
}))
vi.mock('./api', () => ({ getTeacherClassApi: () => mocks.api }))

import TeacherClassCenter from './TeacherClassCenter.vue'

function metric(percentDisplay = '—', state = 'no_data') {
  return {
    numerator: '0.00000000',
    denominator: '0.00000000',
    ratio: null,
    state,
    sample_size: null,
    percent_display: percentDisplay,
  }
}

function centerPayload() {
  return {
    course: { id: 7, name: '研究方法', code: 'KT101', term: '2026 秋', status: 'active' },
    class_group: {
      id: 11,
      name: '实验一班',
      academic_year: '2026',
      is_active: true,
      active_student_count: 1,
    },
    generated_at: '2026-08-30T00:00:00Z',
    scope: { course_id: 7, class_group_id: 11, experiment_id: null, task_count: 0 },
    has_live_experiments: false,
    rank_sample_size: 0,
    overview: {
      roster_count: 1,
      participated_count: 0,
      correct_rate: metric(),
      completion_rate: metric(),
    },
    class_average_correct_rate: metric(),
    correct_rate_distribution: [{ key: 'no_data', label: '无数据', count: 1 }],
    participation: { completed: 0, in_progress: 0, not_participated: 1 },
    kc_rows: [],
    experiment_options: [],
    experiment_rows: [],
    experiment_participants: [],
    students: {
      items: [{
        student_id: 23,
        student_number: 'S001',
        display_name: '测试学生',
        completed_experiments: 0,
        eligible_experiments: 0,
        correct_rate: metric(),
        score_rate: metric(),
        average_answer_time_ms: null,
        average_answer_time_display: '—',
        status: 'not_participated',
        rank: null,
        student_url: '/teacher/courses/7/classes/11/students/23/analysis/',
      }],
      page: { number: 1, size: 20, total_items: 1, total_pages: 1, start: 1, end: 1 },
    },
    question_rows: [],
    filters: { query: '', sort: 'student_number', status: null, experiment_id: null },
    actions: {
      class_list_url: '/teacher/courses/7/classes/',
      visibility_url: '/teacher/courses/7/classes/11/student-visibility/',
      manage_url: '/teacher/courses/7/classes/11/?section=manage',
      import_students_url: '/teacher/courses/7/classes/11/students/import/',
    },
  }
}

describe('TeacherClassCenter', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    route.query = {}
    mocks.api.center.mockResolvedValue(centerPayload())
  })

  it('renders authoritative empty metrics and keeps management as document navigation', async () => {
    const wrapper = mount(TeacherClassCenter)
    await flushPromises()

    expect(wrapper.get('h1').text()).toBe('实验一班')
    expect(wrapper.get('[aria-label="班级中心"]').classes()).toContain('kt-sidebar-secondary-nav')
    expect(wrapper.text()).toContain('总体正确率')
    expect(wrapper.text()).toContain('暂无可计算的正确率')
    const manage = wrapper.get('a[href*="section=manage"]')
    expect(manage.text()).toContain('班级管理')
  })

  it('renders server-projected student facts without calculating them in Vue', async () => {
    route.query = { section: 'students' }
    const wrapper = mount(TeacherClassCenter)
    await flushPromises()

    expect(wrapper.text()).toContain('测试学生')
    expect(wrapper.text()).toContain('未参与')
    expect(wrapper.get('a[href$="/analysis/"]').attributes('href')).toContain('/students/23/')
  })

  it('shows no-data distribution separately from zero accuracy', async () => {
    const wrapper = mount(TeacherClassCenter)
    await flushPromises()
    expect(wrapper.find('[data-accuracy-ring] [data-ring-value]').exists()).toBe(false)
    expect(wrapper.get('[data-distribution-total]').text()).toContain('1')
    expect(wrapper.get('[data-bin="no_data"]').text()).toContain('100.0%')
  })

  it('uses distribution counts and experiment-unit denominators rather than the student page size', async () => {
    const payload = centerPayload()
    payload.correct_rate_distribution = [{ key: 'lt_60', label: '<60%', count: 2 }, { key: 'no_data', label: '无数据', count: 3 }]
    Object.assign(payload.overview.completion_rate, { numerator: '4', denominator: '10', ratio: '0.4', state: 'available', percent_display: '40.0%' })
    Object.assign(payload.overview.correct_rate, { numerator: '0', denominator: '4', ratio: '0', state: 'available', percent_display: '0.0%' })
    mocks.api.center.mockResolvedValue(payload)
    const wrapper = mount(TeacherClassCenter)
    await flushPromises()
    expect(wrapper.get('[data-distribution-total]').text()).toContain('5')
    expect(wrapper.get('[data-bin="lt_60"]').text()).toContain('40.0%')
    expect(wrapper.get('[data-completion-units]').text()).toContain('4 / 10')
    expect(wrapper.get('[data-accuracy-ring] [data-ring-value]').attributes('stroke-dasharray')).toBe('0 100')
  })

  it('does not invent sectors or percentages for an empty cohort', async () => {
    const payload = centerPayload()
    payload.correct_rate_distribution = [{ key: 'no_data', label: '无数据', count: 0 }]
    payload.participation.not_participated = 0
    mocks.api.center.mockResolvedValue(payload)
    const wrapper = mount(TeacherClassCenter)
    await flushPromises()
    expect(wrapper.findAll('[data-distribution-ring] [data-sector]')).toHaveLength(0)
    expect(wrapper.get('[data-bin="no_data"]').text()).toContain('—')
  })
})
