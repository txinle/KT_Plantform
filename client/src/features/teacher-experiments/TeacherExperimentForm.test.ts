import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  api: {
    list: vi.fn(),
    concepts: vi.fn(),
    detail: vi.fn(),
    samplingInventory: vi.fn(),
    readiness: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    startPlan: vi.fn(),
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
    mocks.api.startPlan.mockResolvedValue({ id: 23, current_step: 1 })
    mocks.api.samplingInventory.mockResolvedValue({
      candidate_count: 12,
      kc_counts: [
        { kc_code: 'KC000001', primary_available: 0, any_level_available: 0 },
        { kc_code: 'KC000002', primary_available: 1, any_level_available: 3 },
      ],
      question_types: [
        { question_type: 'single_choice', label: '单选题', available: 8 },
        { question_type: 'multiple_choice', label: '多选题', available: 4 },
        { question_type: 'numeric', label: '数值题', available: 0 },
        { question_type: 'short_answer', label: '简答题', available: 0 },
        { question_type: 'fill_blank', label: '填空题', available: 0 },
        { question_type: 'true_false', label: '判断题', available: 0 },
      ],
    })
    mocks.api.readiness.mockResolvedValue({
      ready: true,
      issues: [],
      candidate_count: 12,
      inventory_buckets: [],
    })
    mocks.api.concepts.mockResolvedValue({
      items: [
        { id: 10, kc_code: 'KC000001', name: '数学', description: '', status: 'active', role_counts: { level_1: 0, level_2: 0, level_3: 0 }, question_count: 0, bank_count: 0, distribution: [], can_rename: true, can_delete: true, rename_url: null, delete_url: null },
        { id: 11, kc_code: 'KC000002', name: '分数', description: '', status: 'active', role_counts: { level_1: 12, level_2: 0, level_3: 0 }, question_count: 12, bank_count: 1, distribution: [], can_rename: true, can_delete: true, rename_url: null, delete_url: null },
      ],
      page: { number: 1, size: 500, total_items: 2, total_pages: 1, start: 1, end: 2 },
      course: { id: 7, name: '课程', code: 'C', term: '2026', status: 'active' },
    })
  })

  it('uses bank inventory for KC counts and initializes the quota match scope', async () => {
    const wrapper = mount(TeacherExperimentForm)
    await flushPromises()
    expect(mocks.api.samplingInventory).toHaveBeenCalledWith(7, {
      question_bank_ids: [3], kc_codes: [], match_scope: 'any_level',
    })
    expect(wrapper.get('.kt-kc-config-panel .kt-kc-tree-selector').text()).toContain('3 道可用')
    expect(wrapper.get('.kt-kc-config-panel .kt-kc-tree-selector').text()).not.toContain('12 道可用')
    await wrapper.get('input[name="sampling_mode"][value="kc_quota"]').setValue(true)
    await wrapper.get('input[type="checkbox"][value="11"]').setValue(true)
    await flushPromises()
    expect((wrapper.get('select[aria-label="分数匹配层级"]').element as HTMLSelectElement).value).toBe('primary')
    expect(wrapper.get('.kt-quota-table tbody tr').findAll('td')[2]?.text()).toBe('1')
    await wrapper.get('select[aria-label="分数匹配层级"]').setValue('any_level')
    expect(wrapper.get('.kt-quota-table tbody tr').findAll('td')[2]?.text()).toBe('3')
  })

  it('searches and selects multiple banks without losing hidden selections', async () => {
    mocks.api.list.mockResolvedValue({ summary: { question_banks: [{ id: 3, name: '第一章' }, { id: 4, name: '第二章' }, { id: 5, name: '第三章' }] } })
    mocks.api.samplingInventory.mockResolvedValue({ candidate_count: 8, kc_counts: [], question_types: [], bank_counts: [
      { question_bank_id: 3, name: '第一章', candidate_count: 4 },
      { question_bank_id: 4, name: '第二章', candidate_count: 4 },
    ] })
    const wrapper = mount(TeacherExperimentForm)
    await flushPromises()
    await wrapper.get('input[name="question_bank_choice"][value="3"]').setValue(true)
    await wrapper.get('#experiment-bank-search').setValue('第二')
    await wrapper.get('input[name="question_bank_choice"][value="4"]').setValue(true)
    await flushPromises()
    expect(wrapper.text()).toContain('已选择 2 个题库')
    expect(wrapper.get('[aria-label="各题库候选库存"]').text()).toContain('第一章')
    await wrapper.get('#experiment-name').setValue('跨章节实验')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(mocks.api.create).toHaveBeenCalledWith(7, expect.objectContaining({ question_bank_ids: [3, 4] }))
    await wrapper.get('#experiment-bank-search').setValue('')
    await wrapper.get('input[name="question_bank_choice"][value="3"]').setValue(false)
    await flushPromises()
    expect(mocks.api.samplingInventory).toHaveBeenLastCalledWith(7, expect.objectContaining({ question_bank_ids: [4] }))
  })

  it('keeps selected banks in a compact dropdown that closes outside or with Escape', async () => {
    const wrapper = mount(TeacherExperimentForm, { attachTo: document.body })
    await flushPromises()
    const picker = wrapper.get('.kt-experiment-bank-picker').element as HTMLDetailsElement
    expect(wrapper.find('.kt-experiment-selected-banks').exists()).toBe(false)
    expect(wrapper.get('#experiment-bank').text()).toContain('认知诊断题库')
    picker.open = true
    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }))
    expect(picker.open).toBe(false)
    picker.open = true
    await wrapper.get('#experiment-bank-search').trigger('keydown', { key: 'Escape' })
    expect(picker.open).toBe(false)
    expect(document.activeElement).toBe(wrapper.get('#experiment-bank').element)
    wrapper.unmount()
  })

  it('keeps bank choices open when WebKit moves pointer focus to their ancestor', async () => {
    const wrapper = mount(TeacherExperimentForm, { attachTo: document.body })
    await flushPromises()
    const picker = wrapper.get('.kt-experiment-bank-picker').element as HTMLDetailsElement
    picker.open = true
    await wrapper.get('#experiment-bank').trigger('focusout', { relatedTarget: wrapper.element })
    expect(picker.open).toBe(true)
    await wrapper.get('#experiment-bank').trigger('focusout', { relatedTarget: wrapper.get('#experiment-name').element })
    expect(picker.open).toBe(false)
    wrapper.unmount()
  })

  it('clears stale bank inventory while loading and on failure', async () => {
    mocks.api.list.mockResolvedValue({ summary: { question_banks: [{ id: 3, name: '题库一' }, { id: 4, name: '题库二' }] } })
    const wrapper = mount(TeacherExperimentForm)
    await flushPromises()
    await wrapper.get('input[name="question_bank_choice"][value="3"]').setValue(true)
    await flushPromises()
    let rejectInventory!: (error: Error) => void
    mocks.api.samplingInventory.mockReturnValueOnce(new Promise((_, reject) => { rejectInventory = reject }))
    await wrapper.get('input[name="question_bank_choice"][value="4"]').setValue(true)
    expect(wrapper.get('.kt-kc-config-panel').text()).not.toContain('3 道可用')
    expect(wrapper.text()).toContain('正在加载所选题库库存')
    rejectInventory(new Error('库存查询失败'))
    await flushPromises()
    expect(wrapper.text()).toContain('库存查询失败')
    expect(wrapper.get('.kt-kc-config-panel').text()).not.toContain('3 道可用')
  })

  it('creates a complete teacher-owned experiment definition and opens its detail page', async () => {
    const wrapper = mount(TeacherExperimentForm)
    await flushPromises()

    const sections = wrapper.findAll('.kt-numbered-form-section')
    expect(sections).toHaveLength(5)
    expect(sections.map((section) => section.text())).toEqual(expect.arrayContaining([
      expect.stringContaining('基本信息'),
      expect.stringContaining('抽题设置'),
      expect.stringContaining('跨实验题目控制'),
      expect.stringContaining('作答与反馈'),
      expect.stringContaining('研究设置'),
    ]))
    expect(wrapper.find('input[name="sampling_mode"][value="total_count"]').exists()).toBe(true)
    expect(wrapper.findAll('.kt-numbered-form-section .kt-compact-form-row').length).toBeGreaterThanOrEqual(4)
    expect(wrapper.find('#experiment-total-time').exists()).toBe(true)
    expect(wrapper.find('#experiment-question-time').exists()).toBe(true)
    expect(wrapper.find('.kt-runtime-settings-grid').exists()).toBe(true)
    expect(wrapper.findAll('.kt-runtime-settings-card')).toHaveLength(2)
    expect(wrapper.find('[aria-labelledby="definition-runtime"] details').exists()).toBe(false)
    expect(wrapper.get('.kt-runtime-summary').text()).toContain('当前配置：')

    await wrapper.get('#experiment-name').setValue('分数比较实验')
    await wrapper.get('input[name="question_bank_choice"][value="3"]').setValue(true)
    await wrapper.get('input[type="checkbox"][value="11"]').setValue(true)
    await wrapper.get('input[name="time_mode"][value="total"]').setValue(true)
    await wrapper.get('#experiment-total-time').setValue('25')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(mocks.api.create).toHaveBeenCalledWith(
      7,
      expect.objectContaining({
        name: '分数比较实验',
        question_bank_ids: [3],
        sampling: {
          mode: 'total_count',
          total_count: 1,
          match_scope: 'any_level',
          nodes: [{ kc_code: 'KC000002', quota: null, match_scope: 'any_level' }],
          type_quotas: [],
        },
        ordering_mode: 'fixed',
        feedback_mode: 'full',
        feedback_release_policy: 'after_personal_submission',
        time_mode: 'total',
        total_time_limit_minutes: 25,
        per_question_time_limit_seconds: null,
        confidence_mode: 'off',
        cross_experiment_item_policy: 'exclude_previous',
        review_count: 0,
      }),
    )
    expect(mocks.push).toHaveBeenCalledWith('/teacher/courses/7/experiments/19/')
  })

  it('creates the experiment and enters publishing from the primary action', async () => {
    const wrapper = mount(TeacherExperimentForm)
    await flushPromises()

    await wrapper.get('#experiment-name').setValue('立即发布实验')
    await wrapper.get('input[type="checkbox"][value="11"]').setValue(true)
    await wrapper.get('.kt-experiment-form-actions button:last-child').trigger('click')
    await flushPromises()

    expect(mocks.api.create).toHaveBeenCalled()
    expect(mocks.api.startPlan).toHaveBeenCalledWith(7, 19)
    expect(mocks.push).toHaveBeenCalledWith('/teacher/courses/7/experiments/19/publish-plan/23/step/1/')
  })

  it('preserves existing feedback settings when editing an experiment', async () => {
    mocks.route.name = 'teacher-experiment-edit'
    mocks.route.params = { courseId: '7', experimentId: '19' }
    mocks.api.detail.mockResolvedValue({
      id: 19,
      name: '已有反馈设置的实验',
      description: '',
      question_bank: { id: 3, name: '认知诊断题库' },
      sampling_mode: 'total_count',
      sampling_total_count: 1,
      sampling_match_scope: 'any_level',
      sampling_kcs: [{ kc_code: 'KC000002', quota: null, match_scope: 'any_level' }],
      sampling: { mode: 'total_count', total_count: 1, match_scope: 'any_level', nodes: [], type_quotas: [] },
      ordering_mode: 'fixed',
      feedback_mode: 'none',
      feedback_release_policy: 'manual_release',
      time_mode: 'none',
      resume_mode: 'resumable',
      confidence_mode: 'off',
      confidence_sampling_unit: 'attempt',
    })
    mocks.api.update.mockResolvedValue({ id: 19 })

    const wrapper = mount(TeacherExperimentForm)
    await flushPromises()

    expect((wrapper.get('input[name="feedback_mode"][value="none"]').element as HTMLInputElement).checked).toBe(true)
    expect((wrapper.get('input[name="feedback_release_policy"][value="manual_release"]').element as HTMLInputElement).checked).toBe(true)
    expect(wrapper.find('[aria-label="已保存抽题配置库存预检"]').exists()).toBe(true)
    await wrapper.get('#sampling-total-count').setValue('2')
    expect(wrapper.find('[aria-label="已保存抽题配置库存预检"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('抽题配置尚未保存')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(mocks.api.update).toHaveBeenCalledWith(
      7,
      19,
      expect.objectContaining({
        feedback_mode: 'none',
        feedback_release_policy: 'manual_release',
      }),
    )
  })

  it('allows an editable draft whose deleted bank must be replaced', async () => {
    mocks.route.name = 'teacher-experiment-edit'
    mocks.route.params = { courseId: '7', experimentId: '19' }
    mocks.api.detail.mockResolvedValue({
      id: 19,
      name: '待修复实验',
      description: '',
      question_bank: null,
      sampling_mode: 'total_count',
      sampling_total_count: 1,
      sampling_match_scope: 'any_level',
      sampling_kcs: [],
      sampling: { mode: 'total_count', total_count: 1, match_scope: 'any_level', nodes: [], type_quotas: [] },
      question_quotas: [], ordering_mode: 'fixed', feedback_mode: 'none',
      feedback_release_policy: 'after_personal_submission', time_mode: 'none', resume_mode: 'resumable',
      confidence_mode: 'off', confidence_sampling_unit: 'attempt',
    })
    mocks.api.update.mockResolvedValue({ id: 19 })
    const wrapper = mount(TeacherExperimentForm)
    await flushPromises()

    expect((wrapper.get('#experiment-name').element as HTMLInputElement).value).toBe('待修复实验')
    expect(wrapper.get('#experiment-bank').text()).toContain('请选择题库')
    expect(wrapper.text()).toContain('请选择题库')
    await wrapper.get('input[name="question_bank_choice"][value="3"]').setValue(true)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(mocks.api.update).toHaveBeenCalledWith(7, 19, expect.objectContaining({ question_bank_ids: [3] }))
  })

  it('configures an exact mixed-review count and submits the policy', async () => {
    const wrapper = mount(TeacherExperimentForm)
    await flushPromises()

    expect(wrapper.findAll('[data-history-policy]').length).toBe(3)
    expect((wrapper.get('input[name="cross_experiment_item_policy"][value="exclude_previous"]').element as HTMLInputElement).checked).toBe(true)
    await wrapper.get('input[name="cross_experiment_item_policy"][value="mixed_review"]').setValue(true)
    await wrapper.get('#experiment-review-count').setValue('1')
    await wrapper.get('#experiment-name').setValue('混合复测实验')
    await wrapper.get('input[type="checkbox"][value="11"]').setValue(true)
    await wrapper.get('#sampling-total-count').setValue('3')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(wrapper.get('[data-review-summary]').text()).toContain('复测 1 题')
    expect(wrapper.get('[data-review-summary]').text()).toContain('新题 2 题')
    expect(mocks.api.create).toHaveBeenCalledWith(7, expect.objectContaining({
      cross_experiment_item_policy: 'mixed_review',
      review_count: 1,
    }))
  })

  it('selects an exact question-type composition from server inventory', async () => {
    const wrapper = mount(TeacherExperimentForm)
    await flushPromises()

    expect(wrapper.text()).toContain('题型构成')
    expect(wrapper.text()).toContain('不限题型')
    await wrapper.get('#experiment-name').setValue('题型构成实验')
    await wrapper.get('input[type="checkbox"][value="11"]').setValue(true)
    await flushPromises()

    expect(mocks.api.samplingInventory).toHaveBeenCalledWith(7, {
      question_bank_ids: [3],
      kc_codes: ['KC000002'],
      match_scope: 'any_level',
    })
    await wrapper.get('summary[data-type-picker]').trigger('click')
    await wrapper.get('input[name="question_type_choice"][value="single_choice"]').setValue(true)
    await wrapper.get('input[name="question_type_choice"][value="multiple_choice"]').setValue(true)
    await wrapper.get('#sampling-total-count').setValue('10')
    await wrapper.get('input[aria-label="单选题数量"]').setValue('6')
    await wrapper.get('input[aria-label="多选题数量"]').setValue('4')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(mocks.api.create).toHaveBeenCalledWith(
      7,
      expect.objectContaining({
        sampling: expect.objectContaining({
          mode: 'total_count',
          total_count: 10,
          type_quotas: [
            { question_type: 'single_choice', amount: 6 },
            { question_type: 'multiple_choice', amount: 4 },
          ],
        }),
      }),
    )
  })

  it('uses the whole bank when total-count mode has no selected knowledge points', async () => {
    const wrapper = mount(TeacherExperimentForm)
    await flushPromises()

    expect(wrapper.text()).toContain('知识点范围（可选）')
    expect(wrapper.text()).toContain('当前使用所选题库')
    expect(wrapper.find('.kt-match-scope').exists()).toBe(false)

    await wrapper.get('#experiment-name').setValue('全题库题型构成实验')
    await wrapper.get('#sampling-total-count').setValue('10')
    await wrapper.get('summary[data-type-picker]').trigger('click')
    await wrapper.get('input[name="question_type_choice"][value="single_choice"]').setValue(true)
    await wrapper.get('input[name="question_type_choice"][value="multiple_choice"]').setValue(true)
    await wrapper.get('input[aria-label="单选题数量"]').setValue('6')
    await wrapper.get('input[aria-label="多选题数量"]').setValue('4')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(mocks.api.create).toHaveBeenCalledWith(7, expect.objectContaining({
      sampling: expect.objectContaining({
        mode: 'total_count',
        total_count: 10,
        nodes: [],
        type_quotas: [
          { question_type: 'single_choice', amount: 6 },
          { question_type: 'multiple_choice', amount: 4 },
        ],
      }),
    }))
  })

  it('does not show zero type inventory while the whole-bank request is loading', async () => {
    let resolveInventory!: (value: unknown) => void
    mocks.api.samplingInventory.mockReturnValueOnce(new Promise((resolve) => { resolveInventory = resolve }))
    const wrapper = mount(TeacherExperimentForm)
    await flushPromises()

    await wrapper.get('summary[data-type-picker]').trigger('click')
    expect(wrapper.get('.kt-type-tree-selector').text()).toContain('正在读取')
    expect(wrapper.get('.kt-type-tree-selector').text()).not.toContain('0 道可用')

    resolveInventory({
      candidate_count: 12,
      kc_counts: [],
      question_types: [
        { question_type: 'single_choice', label: '单选题', available: 8 },
        { question_type: 'multiple_choice', label: '多选题', available: 4 },
        { question_type: 'numeric', label: '数值题', available: 0 },
        { question_type: 'short_answer', label: '简答题', available: 0 },
        { question_type: 'fill_blank', label: '填空题', available: 0 },
        { question_type: 'true_false', label: '判断题', available: 0 },
      ],
    })
    await flushPromises()
  })

  it('blocks saving when selected type amounts do not equal the total', async () => {
    const wrapper = mount(TeacherExperimentForm)
    await flushPromises()
    await wrapper.get('input[type="checkbox"][value="11"]').setValue(true)
    await flushPromises()
    await wrapper.get('summary[data-type-picker]').trigger('click')
    await wrapper.get('input[name="question_type_choice"][value="single_choice"]').setValue(true)

    expect(wrapper.text()).toContain('已分配 1 / 总题数 1')
    await wrapper.get('#sampling-total-count').setValue('2')
    await flushPromises()

    expect(wrapper.text()).toContain('题型数量合计必须等于总题数')
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeDefined()
  })

  it('hides and omits question-type quotas in knowledge-point quota mode', async () => {
    const wrapper = mount(TeacherExperimentForm)
    await flushPromises()
    await wrapper.get('#experiment-name').setValue('KC 配额实验')
    await wrapper.get('input[type="checkbox"][value="11"]').setValue(true)
    await flushPromises()
    await wrapper.get('input[name="question_type_choice"][value="single_choice"]').setValue(true)
    await wrapper.get('input[name="sampling_mode"][value="kc_quota"]').setValue(true)

    expect(wrapper.text()).not.toContain('不限题型')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    const sampling = mocks.api.create.mock.calls.at(-1)?.[1]?.sampling
    expect(sampling?.mode).toBe('kc_quota')
    expect(sampling).not.toHaveProperty('type_quotas')
  })
})
