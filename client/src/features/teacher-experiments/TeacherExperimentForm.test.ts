import { flushPromises, mount } from '@vue/test-utils'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

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

async function chooseBank(wrapper: ReturnType<typeof mount>, id: number, checked: boolean) {
  await wrapper.get('#experiment-bank').trigger('click')
  await flushPromises()
  await wrapper.get(`input[name="question_bank_choice"][value="${id}"]`).setValue(checked)
  await wrapper.get('dialog[aria-label="选择题库"] [data-apply-selection]').trigger('click')
  await flushPromises()
}
async function chooseType(wrapper: ReturnType<typeof mount>, type: string, checked: boolean) {
  await wrapper.get('[data-select-types]').trigger('click')
  await wrapper.get(`input[name="question_type_choice"][value="${type}"]`).setValue(checked)
  await wrapper.get('dialog[aria-label="选择题型"] [data-apply-selection]').trigger('click')
}

describe('TeacherExperimentForm', () => {
  beforeAll(() => {
    Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value() { this.setAttribute('open', '') } })
    Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true, value() { this.removeAttribute('open') } })
  })

  it('stages bank selections in a centered dialog and applies them only on confirmation', async () => {
    mocks.api.list.mockResolvedValue({ summary: { question_banks: [{ id: 3, name: '第一章' }, { id: 4, name: '第二章' }] } })
    const wrapper = mount(TeacherExperimentForm)
    await flushPromises()
    await wrapper.get('#experiment-bank').trigger('click')
    await flushPromises()
    const dialog = wrapper.get('dialog[aria-label="选择题库"]')
    expect(dialog.attributes()).toHaveProperty('open')
    await dialog.get('input[name="question_bank_choice"][value="3"]').setValue(true)
    expect(wrapper.findAll('[data-selected-bank]')).toHaveLength(0)
    await dialog.get('[data-cancel-selection]').trigger('click')
    await wrapper.get('#experiment-bank').trigger('click')
    await flushPromises()
    expect(dialog.get<HTMLInputElement>('input[value="3"]').element.checked).toBe(false)
    await dialog.get('input[value="4"]').setValue(true)
    await dialog.get('[data-apply-selection]').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('[data-selected-bank]').map(node => node.text())).toEqual(['第二章'])
    expect(mocks.api.create).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('stages available type choices and leaves quotas unchanged on cancellation', async () => {
    const wrapper = mount(TeacherExperimentForm)
    await flushPromises()
    await wrapper.get('[data-select-types]').trigger('click')
    const dialog = wrapper.get('dialog[aria-label="选择题型"]')
    expect(dialog.find('input[value="numeric"]').exists()).toBe(false)
    await dialog.get('input[value="single_choice"]').setValue(true)
    expect(wrapper.findAll('.kt-sitev2-type-row')).toHaveLength(0)
    await dialog.get('[data-cancel-selection]').trigger('click')
    await wrapper.get('[data-select-types]').trigger('click')
    expect(dialog.get<HTMLInputElement>('input[value="single_choice"]').element.checked).toBe(false)
    await dialog.get('input[value="multiple_choice"]').setValue(true)
    await dialog.get('[data-apply-selection]').trigger('click')
    expect(wrapper.get('.kt-sitev2-type-row').text()).toContain('多选题')
    expect(wrapper.get<HTMLInputElement>('input[aria-label="多选题数量"]').element.value).toBe('1')
    wrapper.unmount()
  })

  it('confirms per-type pins atomically, enforces the quota, and keeps other types when choosing all random', async () => {
    mocks.api.samplingInventory.mockResolvedValue({ candidate_count: 3, kc_counts: [], question_types: [
      { question_type: 'reading', label: '阅读理解', available: 2 },
      { question_type: 'single_choice', label: '单选题', available: 1 },
    ], questions: [
      { question_id: 41, question_type: 'reading', code: 'R1', title: 'Garden', text: 'Article one', item_count: 3, question_bank_id: 3 },
      { question_id: 56, question_type: 'reading', code: 'R2', title: 'Library', text: 'Article two', item_count: 4, question_bank_id: 3 },
      { question_id: 8, question_type: 'single_choice', code: 'C1', title: 'Choice', text: 'Question', item_count: 1, question_bank_id: 3 },
    ] })
    const wrapper = mount(TeacherExperimentForm)
    await flushPromises()
    await wrapper.get('#experiment-name').setValue('弹窗选择测试')
    await wrapper.get('#sampling-total-count').setValue(2)
    await wrapper.get('[data-select-types]').trigger('click')
    const typeDialog = wrapper.get('dialog[aria-label="选择题型"]')
    await typeDialog.get('input[value="reading"]').setValue(true)
    await typeDialog.get('input[value="single_choice"]').setValue(true)
    await typeDialog.get('[data-apply-selection]').trigger('click')
    await wrapper.get('[data-select-question-type="reading"]').trigger('click')
    const reading = wrapper.get('dialog[aria-label="选择具体题目 · 阅读理解"]')
    expect(reading.find('input[data-pin-question="8"]').exists()).toBe(false)
    await reading.get('input[data-pin-question="41"]').setValue(true)
    await reading.get('input[data-pin-question="56"]').setValue(true)
    await reading.get('[data-apply-selection]').trigger('click')
    expect(reading.text()).toContain('指定题目数超过该题型数量')
    expect(reading.attributes()).toHaveProperty('open')
    await reading.get('input[data-pin-question="56"]').setValue(false)
    await reading.get('[data-apply-selection]').trigger('click')
    await wrapper.get('[data-select-question-type="single_choice"]').trigger('click')
    const single = wrapper.get('dialog[aria-label="选择具体题目 · 单选题"]')
    await single.get('input[data-pin-question="8"]').setValue(true)
    await single.get('[data-apply-selection]').trigger('click')
    await wrapper.get('[data-select-question-type="reading"]').trigger('click')
    await reading.get('[data-all-random]').trigger('click')
    await reading.get('[data-cancel-selection]').trigger('click')
    expect(wrapper.get('[data-select-question-type="reading"]').text()).toContain('已指定 1 道')
    await wrapper.get('[data-select-question-type="reading"]').trigger('click')
    await reading.get('[data-all-random]').trigger('click')
    await reading.get('[data-apply-selection]').trigger('click')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(mocks.api.create).toHaveBeenCalledWith(7, expect.objectContaining({ sampling: expect.objectContaining({ pinned_question_ids: [8] }) }))
    wrapper.unmount()
  })

  it('presents total and composition first, with advanced quotas and a live summary', async () => {
    const wrapper = mount(TeacherExperimentForm)
    await flushPromises()
    expect(wrapper.get('.kt-experiment-breadcrumb').text()).toContain('课程')
    expect(wrapper.get('.kt-experiment-breadcrumb a[href="/teacher/courses/7/experiments/"]').text()).toBe('实验')
    expect(wrapper.find('.kt-experiment-back-button').exists()).toBe(false)
    expect(wrapper.findAll('.kt-numbered-form-section__heading > span').map(item => item.text())).toEqual(['01', '02', '03', '04', '05'])
    expect(wrapper.get('.kt-experiment-page-header__actions').text()).toContain('保存草稿')
    expect(wrapper.get('.kt-experiment-page-header__actions').text()).toContain('返回实验')
    expect(wrapper.get('input[name="confidence_mode"]').element.closest('details')).toBeNull()
    expect(wrapper.get('.kt-experiment-advanced').element.closest('[aria-labelledby="definition-selection"]')).toBeNull()
    expect(wrapper.get('.kt-experiment-form-actions button').text()).toBe('保存并进入班级排期 →')
    expect(wrapper.get('.kt-sitev2-summary-bottom button').text()).toBe('保存并进入班级排期 →')
    expect(wrapper.find('input[name="sampling_mode"]').exists()).toBe(false)
    expect(wrapper.find('input[name="time_mode"][value="per_question"]').exists()).toBe(false)
    expect(wrapper.find('#experiment-question-time').exists()).toBe(false)
    expect(wrapper.get('[aria-label="当前实验配置"]').text()).toContain('1 道')
    await wrapper.get('#sampling-total-count').setValue(4)
    expect(wrapper.get('[aria-label="当前实验配置"]').text()).toContain('4 道')
    wrapper.unmount()
  })
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

  it('offers available complete English question types from the selected banks', async () => {
    mocks.api.samplingInventory.mockResolvedValue({ candidate_count: 3, kc_counts: [], question_types: [
      { question_type: 'reading', label: '阅读理解', available: 1 },
      { question_type: 'words', label: '选词填空', available: 1 },
      { question_type: 'essay', label: '作文题', available: 1 },
    ] })
    const wrapper = mount(TeacherExperimentForm)
    await flushPromises()
    expect(wrapper.get('dialog[aria-label="选择题型"]').text()).toContain('阅读理解')
    expect(wrapper.get('dialog[aria-label="选择题型"]').text()).toContain('选词填空')
    expect(wrapper.get('dialog[aria-label="选择题型"]').text()).toContain('作文题')
    await wrapper.get('#experiment-name').setValue('英语完整题抽取')
    await chooseType(wrapper, 'reading', true)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(mocks.api.create).toHaveBeenCalledWith(7, expect.objectContaining({
      sampling: expect.objectContaining({ type_quotas: [{ question_type: 'reading', amount: 1 }] }),
    }))
    wrapper.unmount()
  })

  it('keeps a retired timer draft unselected until the teacher explicitly chooses a current mode', async () => {
    mocks.route.name = 'teacher-experiment-edit'
    mocks.route.params = { courseId: '7', experimentId: '19' }
    mocks.api.detail.mockResolvedValue({ id: 19, name: '旧单题计时', question_bank: { id: 3, name: '题库' }, sampling_mode: 'total_count', sampling_total_count: 1, sampling_kcs: [], time_mode: 'per_question', per_question_time_limit_seconds: 30 })
    mocks.api.update.mockResolvedValue({ id: 19 })
    const wrapper = mount(TeacherExperimentForm)
    await flushPromises()
    expect(wrapper.find('input[name="time_mode"]:checked').exists()).toBe(false)
    await wrapper.get('form').trigger('submit')
    expect(mocks.api.update).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('请明确选择无时限或实验总时限')
    await wrapper.get('input[name="time_mode"][value="none"]').setValue(true)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(mocks.api.update).toHaveBeenCalledWith(7, 19, expect.objectContaining({ time_mode: 'none', per_question_time_limit_seconds: null }))
    wrapper.unmount()
  })

  it('retains a selected type and its quota when a new bank has insufficient inventory', async () => {
    mocks.api.list.mockResolvedValue({ summary: { question_banks: [{ id: 3, name: '第一题库' }, { id: 4, name: '第二题库' }] } })
    const wrapper = mount(TeacherExperimentForm)
    await flushPromises()
    await chooseBank(wrapper, 3, true)
    await flushPromises()
    await chooseType(wrapper, 'single_choice', true)
    mocks.api.samplingInventory.mockResolvedValue({ candidate_count: 0, kc_counts: [], question_types: [] })
    await chooseBank(wrapper, 3, false)
    await chooseBank(wrapper, 4, true)
    await flushPromises()
    expect(wrapper.get('[data-select-question-type="single_choice"]').text()).toContain('单选题')
    await wrapper.get('[data-select-types]').trigger('click')
    await wrapper.get('dialog[aria-label="选择题型"] [data-apply-selection]').trigger('click')
    expect(wrapper.get<HTMLInputElement>('input[aria-label="单选题数量"]').element.value).toBe('1')
    expect(wrapper.text()).toContain('单选题库存不足：需要 1，可用 0')
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('creates a full question-group pool without per-item quotas', async () => {
    const wrapper = mount(TeacherExperimentForm)
    await flushPromises()
    await wrapper.get('#experiment-name').setValue('英语题组')
    await wrapper.get('#sampling-advanced-mode').setValue('all')
    expect(wrapper.find('#sampling-total-count').exists()).toBe(false)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(mocks.api.create).toHaveBeenCalledWith(7, expect.objectContaining({
      sampling: { mode: null, total_count: null, match_scope: 'any_level', nodes: [], type_quotas: [] },
    }))
    wrapper.unmount()
  })

  it('pins a complete question by its server-projected representative id', async () => {
    mocks.api.samplingInventory.mockResolvedValue({ candidate_count: 2, kc_counts: [], question_types: [
      { question_type: 'reading', label: '阅读理解', available: 2 },
    ], questions: [
      { question_id: 41, question_type: 'reading', code: 'R001', title: 'A community garden', text: 'Shared article', item_count: 3, question_bank_id: 3 },
      { question_id: 56, question_type: 'reading', code: 'R002', title: 'A library', text: 'Another article', item_count: 4, question_bank_id: 3 },
    ] })
    const wrapper = mount(TeacherExperimentForm, { global: { stubs: { Drawer: { template: '<section><slot /></section>' } } } })
    await flushPromises()
    await wrapper.get('#experiment-name').setValue('指定一篇阅读')
    await wrapper.get('button[data-select-questions]').trigger('click')
    await wrapper.get('input[data-pin-question="41"]').setValue(true)
    await wrapper.get('dialog[aria-label="选择具体题目"] [data-apply-selection]').trigger('click')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(mocks.api.create).toHaveBeenCalledWith(7, expect.objectContaining({
      sampling: expect.objectContaining({ pinned_question_ids: [41], total_count: 1 }),
    }))
    wrapper.unmount()
  })

  it('requires explicit removal of pinned questions before saving incompatible KC quotas', async () => {
    mocks.route.name = 'teacher-experiment-edit'
    mocks.route.params = { courseId: '7', experimentId: '19' }
    mocks.api.detail.mockResolvedValue({ id: 19, name: '指定题实验', question_bank: { id: 3, name: '题库' }, sampling_mode: 'total_count', sampling_total_count: 1, sampling_kcs: [], sampling: { pinned_question_ids: [41] } })
    mocks.api.update.mockResolvedValue({ id: 19 })
    const wrapper = mount(TeacherExperimentForm)
    await flushPromises()
    await wrapper.get('#sampling-advanced-mode').setValue('kc_quota')
    await wrapper.get('input[type="checkbox"][value="11"]').setValue(true)
    await wrapper.get('form').trigger('submit')
    expect(mocks.api.update).not.toHaveBeenCalled()
    await wrapper.get('[data-clear-incompatible-pins]').trigger('click')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(mocks.api.update).toHaveBeenCalledWith(7, 19, expect.objectContaining({ sampling: expect.objectContaining({ mode: 'kc_quota', pinned_question_ids: [] }) }))
    wrapper.unmount()
  })

  it('keeps a server-identified group pool editable instead of classifying it as a legacy draft', async () => {
    mocks.route.name = 'teacher-experiment-edit'
    mocks.route.params = { courseId: '7', experimentId: '19' }
    mocks.api.detail.mockResolvedValue({ id: 19, name: '英语题组', selection_mode: 'all', question_bank: { id: 3, name: '题库' }, sampling_mode: null, sampling_kcs: [] })
    mocks.api.readiness.mockResolvedValue({ ready: true, issues: [], blocking_issues: [], candidate_count: 18, inventory_buckets: [], question_group_pool: true })
    mocks.api.update.mockResolvedValue({ id: 19 })
    const wrapper = mount(TeacherExperimentForm)
    await flushPromises()
    expect(wrapper.text()).not.toContain('只读旧版草稿')
    expect(wrapper.get<HTMLSelectElement>('#sampling-advanced-mode').element.value).toBe('all')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(mocks.api.update).toHaveBeenCalledWith(7, 19, expect.objectContaining({ sampling: expect.objectContaining({ mode: null, nodes: [] }) }))
    wrapper.unmount()
  })

  it('uses bank inventory for KC counts and initializes the quota match scope', async () => {
    const wrapper = mount(TeacherExperimentForm)
    await flushPromises()
    expect(mocks.api.samplingInventory).toHaveBeenCalledWith(7, {
      question_bank_ids: [3], kc_codes: [], match_scope: 'any_level',
    })
    expect(wrapper.get('.kt-kc-config-panel .kt-kc-tree-selector').text()).toContain('3 道可用')
    expect(wrapper.get('.kt-kc-config-panel .kt-kc-tree-selector').text()).not.toContain('12 道可用')
    await wrapper.get('#sampling-advanced-mode').setValue('kc_quota')
    await wrapper.get('input[type="checkbox"][value="11"]').setValue(true)
    await flushPromises()
    expect((wrapper.get('select[aria-label="分数匹配层级"]').element as HTMLSelectElement).value).toBe('primary')
    expect(wrapper.get('.kt-quota-table tbody tr').findAll('td')[2]?.text()).toBe('1')
    await wrapper.get('select[aria-label="分数匹配层级"]').setValue('any_level')
    expect(wrapper.get('.kt-quota-table tbody tr').findAll('td')[2]?.text()).toBe('3')
  })

  it('places the bank selector in the Site header and lists every selected bank below it', async () => {
    mocks.api.list.mockResolvedValue({ summary: { question_banks: [{ id: 3, name: '第一章' }, { id: 4, name: '第二章' }] } })
    const wrapper = mount(TeacherExperimentForm)
    await flushPromises()
    expect(wrapper.get('[aria-labelledby="definition-basic"] .kt-numbered-form-section__heading #experiment-bank').text()).toContain('选择题库')
    await chooseBank(wrapper, 3, true)
    await chooseBank(wrapper, 4, true)
    await flushPromises()
    expect(wrapper.get('[aria-label="已选题库"]').findAll('[data-selected-bank]').map(node => node.text())).toEqual(['第一章', '第二章'])
    await wrapper.get('#experiment-bank-search').setValue('第一')
    expect(wrapper.get('[aria-label="已选题库"]').text()).toContain('第二章')
    wrapper.unmount()
  })

  it('searches and selects multiple banks without losing hidden selections', async () => {
    mocks.api.list.mockResolvedValue({ summary: { question_banks: [{ id: 3, name: '第一章' }, { id: 4, name: '第二章' }, { id: 5, name: '第三章' }] } })
    mocks.api.samplingInventory.mockResolvedValue({ candidate_count: 8, kc_counts: [], question_types: [], bank_counts: [
      { question_bank_id: 3, name: '第一章', candidate_count: 4 },
      { question_bank_id: 4, name: '第二章', candidate_count: 4 },
    ] })
    const wrapper = mount(TeacherExperimentForm)
    await flushPromises()
    await wrapper.get('#experiment-bank').trigger('click')
    await wrapper.get('input[name="question_bank_choice"][value="3"]').setValue(true)
    await wrapper.get('#experiment-bank-search').setValue('第二')
    await wrapper.get('input[name="question_bank_choice"][value="4"]').setValue(true)
    await wrapper.get('dialog[aria-label="选择题库"] [data-apply-selection]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('已选择 2 个题库')
    expect(wrapper.get('[aria-label="各题库候选库存"]').text()).toContain('第一章')
    await wrapper.get('#experiment-name').setValue('跨章节实验')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(mocks.api.create).toHaveBeenCalledWith(7, expect.objectContaining({ question_bank_ids: [3, 4] }))
    await wrapper.get('#experiment-bank-search').setValue('')
    await chooseBank(wrapper, 3, false)
    await flushPromises()
    expect(mocks.api.samplingInventory).toHaveBeenLastCalledWith(7, expect.objectContaining({ question_bank_ids: [4] }))
  })

  it('cancels with Escape and restores focus to the bank trigger without applying pending choices', async () => {
    const wrapper = mount(TeacherExperimentForm, { attachTo: document.body })
    await flushPromises()
    const trigger = wrapper.get<HTMLButtonElement>('#experiment-bank')
    await trigger.trigger('click')
    await flushPromises()
    const dialog = wrapper.get('dialog[aria-label="选择题库"]')
    await dialog.get('input[value="3"]').setValue(false)
    await dialog.trigger('cancel')
    await flushPromises()
    expect(dialog.attributes()).not.toHaveProperty('open')
    expect(wrapper.get('[aria-label="已选题库"]').text()).toContain('认知诊断题库')
    expect(document.activeElement).toBe(trigger.element)
    wrapper.unmount()
  })

  it('clears stale bank inventory while loading and on failure', async () => {
    mocks.api.list.mockResolvedValue({ summary: { question_banks: [{ id: 3, name: '题库一' }, { id: 4, name: '题库二' }] } })
    const wrapper = mount(TeacherExperimentForm)
    await flushPromises()
    await chooseBank(wrapper, 3, true)
    await flushPromises()
    let rejectInventory!: (error: Error) => void
    await wrapper.get('#experiment-bank').trigger('click')
    await flushPromises()
    mocks.api.samplingInventory.mockReturnValueOnce(new Promise((_, reject) => { rejectInventory = reject }))
    await wrapper.get('input[name="question_bank_choice"][value="4"]').setValue(true)
    await wrapper.get('dialog[aria-label="选择题库"] [data-apply-selection]').trigger('click')
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
    expect(wrapper.get<HTMLSelectElement>('#sampling-advanced-mode').element.value).toBe('total_count')
    expect(wrapper.findAll('.kt-numbered-form-section .kt-compact-form-row').length).toBeGreaterThanOrEqual(4)
    expect(wrapper.find('#experiment-total-time').exists()).toBe(false)
    expect(wrapper.find('#experiment-question-time').exists()).toBe(false)
    expect(wrapper.find('.kt-runtime-settings-grid').exists()).toBe(true)
    expect(wrapper.findAll('.kt-runtime-settings-card')).toHaveLength(2)
    expect(wrapper.find('[aria-labelledby="definition-runtime"] details').exists()).toBe(false)
    expect(wrapper.get('.kt-runtime-summary').text()).toContain('当前配置：')

    await wrapper.get('#experiment-name').setValue('分数比较实验')
    await chooseBank(wrapper, 3, true)
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
    expect(wrapper.get('[aria-label="已选题库"]').text()).toContain('请选择题库')
    expect(wrapper.text()).toContain('请选择题库')
    await chooseBank(wrapper, 3, true)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(mocks.api.update).toHaveBeenCalledWith(7, 19, expect.objectContaining({ question_bank_ids: [3] }))
  })

  it('configures an exact mixed-review count and submits the policy', async () => {
    const wrapper = mount(TeacherExperimentForm)
    await flushPromises()

    expect(wrapper.findAll('[data-history-policy]').length).toBe(3)
    expect(wrapper.find('[data-history-policy] em').exists()).toBe(false)
    expect(wrapper.get('.kt-history-policy-note').text()).toContain('排除范围包括')
    expect((wrapper.get('input[name="cross_experiment_item_policy"][value="exclude_previous"]').element as HTMLInputElement).checked).toBe(true)
    await wrapper.get('input[name="cross_experiment_item_policy"][value="mixed_review"]').setValue(true)
    expect(wrapper.get('.kt-history-policy-note').text()).toContain('指定数量的历史正式作答题')
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
    await wrapper.get('[data-select-types]').trigger('click')
    await chooseType(wrapper, 'single_choice', true)
    await chooseType(wrapper, 'multiple_choice', true)
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
    await wrapper.get('[data-select-types]').trigger('click')
    await chooseType(wrapper, 'single_choice', true)
    await chooseType(wrapper, 'multiple_choice', true)
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

    await wrapper.get('[data-select-types]').trigger('click')
    expect(wrapper.get('dialog[aria-label="选择题型"]').text()).toContain('正在读取')
    expect(wrapper.get('dialog[aria-label="选择题型"]').text()).not.toContain('0 道可用')

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
    await wrapper.get('[data-select-types]').trigger('click')
    await chooseType(wrapper, 'single_choice', true)

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
    await chooseType(wrapper, 'single_choice', true)
    await wrapper.get('#sampling-advanced-mode').setValue('kc_quota')

    expect(wrapper.text()).not.toContain('不限题型')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    const sampling = mocks.api.create.mock.calls.at(-1)?.[1]?.sampling
    expect(sampling?.mode).toBe('kc_quota')
    expect(sampling).not.toHaveProperty('type_quotas')
  })
})
