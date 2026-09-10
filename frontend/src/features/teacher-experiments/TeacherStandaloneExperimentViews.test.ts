import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/client'

const mocks = vi.hoisted(() => ({
  api: {
    collection: vi.fn(),
    detail: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    assignStudents: vi.fn(),
    publish: vi.fn(),
    syncQuestions: vi.fn(),
    remove: vi.fn(),
    results: vi.fn(),
  },
  route: {
    name: 'teacher-standalone-experiment-create',
    params: {} as Record<string, string>,
    query: {} as Record<string, string>,
  },
  push: vi.fn(),
  replace: vi.fn(),
}))

vi.mock('vue-router', () => ({
  RouterLink: { template: '<a><slot /></a>' },
  RouterView: { template: '<div><slot /></div>' },
  useRoute: () => mocks.route,
  useRouter: () => ({ push: mocks.push, replace: mocks.replace }),
}))

vi.mock('@/app/bootstrap', () => ({
  getBootstrapContext: () => ({
    csrf_token: 'csrf-token',
    user: { display_name: '李老师', username: 'teacher' },
  }),
}))

vi.mock('./standalone-api', async () => {
  const actual = await vi.importActual<typeof import('./standalone-api')>('./standalone-api')
  return { ...actual, getStandaloneExperimentApi: () => mocks.api }
})

import TeacherStandaloneExperimentDetail from './TeacherStandaloneExperimentDetail.vue'
import TeacherStandaloneExperimentForm from './TeacherStandaloneExperimentForm.vue'
import TeacherStandaloneExperimentResults from './TeacherStandaloneExperimentResults.vue'

const choices = {
  selection_modes: [{ value: 'all', label: '全量题池' }, { value: 'quota', label: '按题型配额抽题' }],
  ordering_modes: [{ value: 'fixed', label: '固定顺序' }],
  feedback_modes: [{ value: 'none', label: '仅显示已提交' }],
  feedback_release_policies: [{ value: 'after_personal_submission', label: '学生完成后' }],
  time_modes: [{ value: 'none', label: '无时限' }, { value: 'total', label: '实验总时限' }],
  resume_modes: [{ value: 'resumable', label: '允许断点续答' }],
  confidence_modes: [{ value: 'off', label: '不采集' }],
  confidence_sampling_units: [{ value: 'attempt', label: '作答' }],
  question_types: [{ value: 'short_answer', label: '简答题' }],
}

function experiment(overrides = {}) {
  return {
    id: 17,
    name: '独立诊断实验',
    description: '保留独立实验流程',
    question_bank: { id: 3, name: '独立题库' },
    status: 'draft',
    status_label: '草稿',
    selection_mode: 'quota',
    selection_mode_label: '按题型配额抽题',
    ordering_mode: 'fixed',
    ordering_mode_label: '固定顺序',
    feedback_mode: 'none',
    feedback_mode_label: '仅显示已提交',
    feedback_release_policy: 'after_personal_submission',
    feedback_release_policy_label: '学生完成后',
    time_mode: 'none',
    time_mode_label: '无时限',
    total_time_limit_minutes: null,
    per_question_time_limit_seconds: null,
    resume_mode: 'resumable',
    resume_mode_label: '允许断点续答',
    confidence_mode: 'off',
    confidence_mode_label: '不采集',
    confidence_sampling_unit: 'attempt',
    confidence_sampling_unit_label: '作答',
    confidence_probability: null,
    confidence_randomization_seed: '',
    confidence_assignment_version: 'v1',
    confidence_scale_version: 'v2',
    confidence_prompt_version: 'v1',
    heartbeat_interval_seconds: 30,
    question_count: 1,
    student_count: 1,
    created_at: '2026-09-01T00:00:00Z',
    updated_at: '2026-09-01T00:00:00Z',
    ...overrides,
  }
}

function detailPayload(overrides = {}) {
  return {
    kind: 'standalone' as const,
    experiment: experiment(),
    selection: {
      quota_rows: [{ question_type: 'short_answer', label: '简答题', amount: 1 }],
      current_candidate_count: 2,
      current_inventory_rows: [{ label: '简答题', need: 1, available: 2 }],
      version_rows: [],
    },
    students: [{ id: 9, student_number: 'S-9', display_name: '学生甲', class_name: '实验班', selected: true }],
    available_students: [{ id: 9, student_number: 'S-9', display_name: '学生甲', class_name: '实验班', selected: true }],
    actions: {
      detail_url: '/teacher/experiments/17/',
      edit_url: '/teacher/experiments/17/edit/',
      results_url: '/teacher/experiments/17/results/',
      assign_url: '/api/v1/teacher/standalone-experiments/17/students/',
      publish_url: '/api/v1/teacher/standalone-experiments/17/publish/',
      sync_questions_url: '/api/v1/teacher/standalone-experiments/17/sync-questions/',
      delete_url: '/api/v1/teacher/standalone-experiments/17/',
      can_edit: true,
      can_assign: true,
      can_publish: true,
      can_sync_questions: true,
    },
    question_banks: [{ id: 3, name: '独立题库' }],
    choices,
    ...overrides,
  }
}

describe('standalone experiment compatibility views', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.route.name = 'teacher-standalone-experiment-create'
    mocks.route.params = {}
    mocks.route.query = {}
    mocks.api.collection.mockResolvedValue({
      kind: 'standalone',
      items: [],
      question_banks: [{ id: 3, name: '独立题库' }],
      choices,
    })
    mocks.api.detail.mockResolvedValue(detailPayload())
    mocks.api.create.mockResolvedValue(detailPayload())
    mocks.api.update.mockResolvedValue(detailPayload())
    mocks.api.assignStudents.mockResolvedValue(detailPayload())
    mocks.api.publish.mockResolvedValue(detailPayload({ experiment: experiment({ status: 'published', status_label: '已发布' }) }))
    mocks.api.syncQuestions.mockResolvedValue(detailPayload())
    mocks.api.results.mockResolvedValue({
      kind: 'standalone',
      experiment: experiment({ status: 'published', status_label: '已发布' }),
      sessions: [],
      session_status_choices: [{ value: 'completed', label: '已完成' }],
      selected_status: '',
      export_field_groups: [{ label: '基本字段', fields: [{ key: 'answer', label: '作答' }] }],
      actions: {
        identified_export_url: '/analytics/experiments/17/identified.xlsx',
        identified_navigation_export_url: '/analytics/experiments/17/navigation-identified.csv',
        kt_export_url: '/analytics/experiments/17/kt.csv',
        navigation_export_url: '/analytics/experiments/17/navigation.csv',
        identity_export_url: '/analytics/experiments/17/identity.csv',
        detail_url: '/teacher/experiments/17/',
      },
    })
  })

  it('creates a legacy-compatible definition including type quotas and collection settings', async () => {
    const wrapper = mount(TeacherStandaloneExperimentForm)
    await flushPromises()

    expect(wrapper.text()).toContain('研究过程采集')
    await wrapper.get('#standalone-experiment-name').setValue('新建独立实验')
    await wrapper.get('#standalone-experiment-bank').setValue('3')
    await wrapper.get('input[name="selection_mode"][value="quota"]').setValue(true)
    await wrapper.get('input[aria-label="简答题数量"]').setValue('2')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(mocks.api.create).toHaveBeenCalledWith(expect.objectContaining({
      name: '新建独立实验',
      question_bank_id: 3,
      selection_mode: 'quota',
      question_quotas: [{ question_type: 'short_answer', amount: 2 }],
      heartbeat_interval_seconds: 30,
    }))
    expect(mocks.push).toHaveBeenCalledWith('/teacher/experiments/17/')
  })

  it('keeps the form editable after a server validation error', async () => {
    mocks.api.create.mockRejectedValueOnce(new ApiError(400, {
      error: {
        code: 'VALIDATION_ERROR',
        message: '请修正实验名称。',
        fields: { name: ['实验名称不能为空。'] },
      },
    }))
    const wrapper = mount(TeacherStandaloneExperimentForm)
    await flushPromises()
    await wrapper.get('#standalone-experiment-name').setValue('')
    await wrapper.get('#standalone-experiment-bank').setValue('3')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(wrapper.find('form.kt-form-stack').exists()).toBe(true)
    expect(wrapper.find('#standalone-experiment-name').exists()).toBe(true)
    expect(wrapper.text()).toContain('实验名称不能为空。')
    expect(wrapper.text()).toContain('请修正实验名称。')
  })

  it('resolves a course-backed legacy edit URL to that experiment’s canonical edit page', async () => {
    mocks.route.name = 'teacher-standalone-experiment-edit'
    mocks.route.params = { experimentId: '17' }
    mocks.api.detail.mockResolvedValueOnce({
      kind: 'canonical',
      canonical_url: '/teacher/courses/7/experiments/17/edit/',
    })

    mount(TeacherStandaloneExperimentForm)
    await flushPromises()

    expect(mocks.api.detail).toHaveBeenCalledWith(17, 'edit')
    expect(mocks.replace).toHaveBeenCalledWith('/teacher/courses/7/experiments/17/edit/')
    expect(mocks.api.collection).not.toHaveBeenCalled()
  })

  it('renders legacy detail fields and retains student assignment and publication actions', async () => {
    mocks.route.name = 'teacher-standalone-experiment-detail'
    mocks.route.params = { experimentId: '17' }
    const wrapper = mount(TeacherStandaloneExperimentDetail)
    await flushPromises()

    expect(wrapper.text()).toContain('候选池库存')
    expect(wrapper.text()).toContain('研究过程采集')
    expect(wrapper.text()).toContain('学生甲')
    await wrapper.get('input[type="checkbox"]').setValue(false)
    await wrapper.get('.kt-standalone-student-picker').trigger('submit')
    await flushPromises()
    expect(mocks.api.assignStudents).toHaveBeenCalledWith(17, [])
    await wrapper.get('[data-action="publish"]').trigger('click')
    await flushPromises()
    expect(mocks.api.publish).toHaveBeenCalledWith(17)
  })

  it('keeps direct export forms and server-authorized result filtering', async () => {
    mocks.route.name = 'teacher-standalone-experiment-results'
    mocks.route.params = { experimentId: '17' }
    const wrapper = mount(TeacherStandaloneExperimentResults)
    await flushPromises()

    expect(wrapper.find('form[action="/analytics/experiments/17/identified.xlsx"]').exists()).toBe(true)
    expect(wrapper.get('input[name="csrfmiddlewaretoken"]').element.getAttribute('value')).toBe('csrf-token')
    await wrapper.get('#standalone-session-status').setValue('completed')
    await wrapper.get('.kt-inline-panel').trigger('submit')
    await flushPromises()
    expect(mocks.api.results).toHaveBeenLastCalledWith(17, 'completed')
  })
})
