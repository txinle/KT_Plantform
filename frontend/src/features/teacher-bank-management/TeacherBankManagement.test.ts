import { flushPromises, mount } from '@vue/test-utils'
import { ApiError } from '@/api/client'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  route: { params: { courseId: '7', bankId: '11', questionId: '31', sourceCourseId: '8', creatorId: '9' }, query: {} as Record<string, string> },
  push: vi.fn(),
  replace: vi.fn(),
  api: {
    bankEditor: vi.fn(), updateBank: vi.fn(), previewQuestionImport: vi.fn(), confirmQuestionImport: vi.fn(),
    sharedBanks: vi.fn(), copySharedBank: vi.fn(),
  },
}))

vi.mock('vue-router', () => ({
  useRoute: () => mocks.route,
  useRouter: () => ({ push: mocks.push, replace: mocks.replace }),
}))
vi.mock('./api', () => ({ getTeacherBankManagementApi: () => mocks.api }))

import TeacherBankEditor from './TeacherBankEditor.vue'
import TeacherKCImport from './TeacherKCImport.vue'
import TeacherQuestionImport from './TeacherQuestionImport.vue'
import TeacherSharedBanks from './TeacherSharedBanks.vue'

const routerLink = { props: ['to'], template: '<a :href="to"><slot /></a>' }

describe('Teacher bank-management Vue views', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.api.bankEditor.mockResolvedValue({
      id: 11, name: '原题库', description: '原说明', visibility: 'private',
      visibility_choices: [{ value: 'private', label: '私有' }, { value: 'shared', label: '共享' }],
      course: { id: 7, name: '课程', code: 'C101', term: '2026 秋', status: 'active' },
      detail_url: '/teacher/courses/7/banks/11/',
    })
    mocks.api.updateBank.mockResolvedValue({ detail_url: '/teacher/courses/7/banks/11/' })
    mocks.api.previewQuestionImport.mockResolvedValue({
      preview_id: 'preview-1',
      preview: {
        rows: [
          {
            row_id: 'row-ready', sequence: 1, source_label: '第 2 行', text: '导入题目\n第二行',
            question_type: 'true_false', question_type_label: '判断题', option_count: 0,
            teacher_difficulty: null, options: [], answer: true, accepted_answers: ['正确'],
            explanation: '这是解析说明。\n第二行。',
            kc_mapping: {
              root_kc_code: 'KC1',
              nodes: [
                { kc_code: 'KC1', name: '数学基础', parent_kc_code: null, order: 1 },
                { kc_code: 'KC2', name: '数与运算', parent_kc_code: 'KC1', order: 1 },
                { kc_code: 'KC5', name: '知识追踪', parent_kc_code: 'KC1', order: 2 },
                { kc_code: 'KC3', name: '加法运算', parent_kc_code: 'KC2', order: 1 },
                { kc_code: 'KC6', name: '核心概念', parent_kc_code: 'KC5', order: 1 },
              ],
            },
            classification: 'ready', errors: [], existing_question_id: null,
          },
          {
            row_id: 'row-duplicate', sequence: 2, source_label: '第 3 行', text: '重复题目',
            question_type: 'single_choice', question_type_label: '单选题', option_count: 2,
            teacher_difficulty: '0.25', options: [{ key: 'A', text: '甲\n第一行' }, { key: 'B', text: '乙' }],
            answer: 'A', accepted_answers: [], explanation: '',
            kc_mapping: null, classification: 'duplicate', errors: ['预览发现重复。'], existing_question_id: 31,
          },
          {
            row_id: 'row-invalid', sequence: 3, source_label: '第 4 行', text: '待修正题目',
            question_type: 'short_answer', question_type_label: '简答题', option_count: 0,
            teacher_difficulty: null, options: [], answer: '', accepted_answers: [], explanation: '',
            kc_mapping: null, classification: 'invalid', errors: ['答案不能为空。'], existing_question_id: null,
          },
        ],
        summary: { ready: 1, duplicate: 1, invalid: 1 },
        confirmable: true,
      },
    })
    mocks.api.confirmQuestionImport.mockResolvedValue({
      created: 1, skipped: 1, failed: 1, detail_url: '/teacher/courses/7/banks/11/',
      rows: [
        { row_id: 'row-ready', status: 'created', question_id: 41, errors: [] },
        { row_id: 'row-duplicate', status: 'duplicate', question_id: 31, errors: [] },
        { row_id: 'row-invalid', status: 'invalid', question_id: null, errors: ['答案不能为空。'] },
      ],
    })
    mocks.api.sharedBanks.mockResolvedValue({
      course: { id: 7, name: '课程', code: 'C101', term: '2026 秋', status: 'active' },
      source_course: { id: 8, name: '来源', code: 'S101', term: '2026 秋', status: 'active' },
      creator: { id: 9, name: '来源教师', username: 'source' },
      items: [{ id: 21, name: '共享题库', description: '说明', question_count: 3 }],
    })
    mocks.api.copySharedBank.mockResolvedValue({ id: 31, name: '副本', detail_url: '/teacher/courses/7/banks/31/' })
  })

  it('saves bank metadata through the JSON editor endpoint', async () => {
    const wrapper = mount(TeacherBankEditor, { global: { stubs: { RouterLink: routerLink } } })
    await flushPromises()
    await wrapper.get('#bank-name').setValue('更新题库')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(mocks.api.updateBank).toHaveBeenCalledWith(7, 11, {
      name: '更新题库', description: '原说明', visibility: 'private',
    })
    expect(mocks.push).toHaveBeenCalledWith('/teacher/courses/7/banks/11/')
  })

  it('shows format-specific templates and server-classified preview rows with inspectable content', async () => {
    const wrapper = mount(TeacherQuestionImport, { global: { stubs: { RouterLink: routerLink } } })
    const input = wrapper.get('input[type="file"]')
    expect(wrapper.get('[data-question-template="xlsx"]').attributes('href')).toBe('/teacher/import-templates/questions.xlsx')
    expect(wrapper.get('[data-question-template="docx"]').attributes('href')).toBe('/teacher/import-templates/questions.docx')
    expect(input.attributes('accept')).toBe('.csv,.xlsx')

    await wrapper.get('#question-import-format-docx').setValue()
    expect(input.attributes('accept')).toBe('.docx')

    const file = new File(['导入题目'], 'questions.docx')
    Object.defineProperty(input.element, 'files', { value: [file], configurable: true })

    await input.trigger('change')
    await flushPromises()
    expect(mocks.api.previewQuestionImport).toHaveBeenCalledWith(7, 11, file)
    expect(wrapper.text()).toContain('导入题目')
    expect(mocks.api.confirmQuestionImport).not.toHaveBeenCalled()
    expect(wrapper.get('[data-import-filter="ready"]').text()).toContain('可导入 1')
    expect(wrapper.get('[data-import-filter="duplicate"]').text()).toContain('重复 1')
    expect(wrapper.get('[data-import-filter="invalid"]').text()).toContain('需修正 1')
    const readyDesktop = wrapper.get('[data-import-row="row-ready"]')
    const readyMobile = wrapper.get('[data-import-mobile-row="row-ready"]')
    expect(readyDesktop.text()).toContain('数学基础（KC1）')
    expect(readyDesktop.text()).toContain('共 5 个 KC · 4 条关系')
    expect(readyMobile.text()).toContain('共 5 个 KC · 4 条关系')
    expect(readyDesktop.get('.kt-question-import__multiline').text()).toContain('导入题目\n第二行')
    const detail = wrapper.get('[data-question-detail]')
    expect(detail.text()).toContain('题目 1 详细信息')
    expect(detail.text()).toContain('这是解析说明。\n第二行。')
    expect(detail.get('[aria-label="KC 对应关系"]').text()).toContain('数学基础（KC1） → 数与运算（KC2）')
    expect(detail.get('[aria-label="KC 对应关系"]').text()).toContain('知识追踪（KC5） → 核心概念（KC6）')
    expect(readyDesktop.get('[data-answer]').text()).toBe('正确')
    expect(readyMobile.text()).toContain('答案正确')
    expect(readyMobile.text()).not.toContain('true')
    expect(wrapper.get('[data-import-row="row-invalid"]').text()).toContain('需修正')
    expect(wrapper.get('[data-import-row="row-invalid"]').text()).toContain('答案不能为空。')

    await wrapper.get('#question-import-filter-invalid').trigger('click')
    expect(wrapper.find('[data-import-row="row-ready"]').exists()).toBe(false)
    expect(wrapper.find('[data-import-row="row-invalid"]').exists()).toBe(true)
  })

  it('clears a previous preview before a replacement upload fails', async () => {
    const wrapper = mount(TeacherQuestionImport, { global: { stubs: { RouterLink: routerLink } } })
    const input = wrapper.get('input[type="file"]')
    const firstFile = new File(['序号,题目\n1,导入题目'], 'questions.csv')
    Object.defineProperty(input.element, 'files', { value: [firstFile], configurable: true })
    await input.trigger('change')
    await flushPromises()
    expect(wrapper.find('[data-import-preview]').exists()).toBe(true)

    mocks.api.previewQuestionImport.mockRejectedValueOnce(new ApiError(400, {
      error: { code: 'VALIDATION_ERROR', message: '请求数据无效。', fields: { file: '文件无法解析。' } },
    }))
    const replacementFile = new File(['损坏'], 'questions.csv')
    Object.defineProperty(input.element, 'files', { value: [replacementFile], configurable: true })
    await input.trigger('change')
    await flushPromises()

    expect(wrapper.find('[data-import-preview]').exists()).toBe(false)
    expect(wrapper.text()).toContain('文件无法解析。')
  })

  it('preserves invalid rows and disables re-confirmation after the server consumes the preview', async () => {
    const wrapper = mount(TeacherQuestionImport, { global: { stubs: { RouterLink: routerLink } } })
    const input = wrapper.get('input[type="file"]')
    const file = new File(['序号,题目\n2,导入题目'], 'questions.csv')
    Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
    await input.trigger('change')
    await flushPromises()

    await wrapper.get('[data-confirm-import]').trigger('click')
    await flushPromises()
    expect(mocks.api.confirmQuestionImport).toHaveBeenCalledWith(7, 11, 'preview-1', ['row-ready'])
    expect(mocks.push).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('新增 1')
    expect(wrapper.text()).toContain('未完成 1')
    expect(wrapper.get('[data-import-row="row-ready"]').text()).toContain('已创建')
    expect(wrapper.get('[data-import-row="row-duplicate"]').text()).not.toContain('预览发现重复。')
    expect(wrapper.get('[data-import-row="row-invalid"]').text()).toContain('需修正')
    expect(wrapper.get('[data-confirm-import]').attributes('disabled')).toBeDefined()

    await wrapper.get('[data-return-detail]').trigger('click')
    expect(mocks.push).toHaveBeenCalledWith('/teacher/courses/7/banks/11/')
  })

  it('uses confirmation outcomes for the summary and invalid-row filter', async () => {
    mocks.api.confirmQuestionImport.mockResolvedValueOnce({
      created: 0, skipped: 1, failed: 2, detail_url: '/teacher/courses/7/banks/11/',
      rows: [
        { row_id: 'row-ready', status: 'invalid', question_id: null, errors: ['KC001 已归档，无法创建。'] },
        { row_id: 'row-duplicate', status: 'duplicate', question_id: 31, errors: [] },
        { row_id: 'row-invalid', status: 'invalid', question_id: null, errors: ['答案不能为空。'] },
      ],
    })
    const wrapper = mount(TeacherQuestionImport, { global: { stubs: { RouterLink: routerLink } } })
    const input = wrapper.get('input[type="file"]')
    const file = new File(['序号,题目\n3,导入题目'], 'questions.csv')
    Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
    await input.trigger('change')
    await flushPromises()
    await wrapper.get('[data-confirm-import]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[aria-label="预览状态汇总"]').text()).toContain('已导入 0')
    expect(wrapper.get('[aria-label="预览状态汇总"]').text()).toContain('重复 1')
    expect(wrapper.get('[aria-label="预览状态汇总"]').text()).toContain('需修正 2')
    await wrapper.get('#question-import-filter-invalid').trigger('click')
    expect(wrapper.find('[data-import-row="row-ready"]').exists()).toBe(true)
    expect(wrapper.get('[data-import-row="row-ready"]').text()).toContain('KC001 已归档，无法创建。')
    expect(wrapper.find('[data-import-row="row-duplicate"]').exists()).toBe(false)
  })

  it('uses only the server confirmable flag to enable confirmation', async () => {
    mocks.api.previewQuestionImport.mockResolvedValueOnce({
      preview_id: 'preview-blocked',
      preview: {
        rows: [],
        summary: { ready: 0, duplicate: 0, invalid: 1 },
        confirmable: false,
      },
    })
    const wrapper = mount(TeacherQuestionImport, { global: { stubs: { RouterLink: routerLink } } })
    const input = wrapper.get('input[type="file"]')
    const file = new File(['损坏'], 'questions.csv')
    Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
    await input.trigger('change')
    await flushPromises()

    expect(wrapper.get('[data-confirm-import]').attributes('disabled')).toBeDefined()
  })

  it('searches the preview, inspects adjacent records and submits only checked importable rows', async () => {
    const initial = await mocks.api.previewQuestionImport()
    initial.preview.rows.push({ ...initial.preview.rows[0], row_id: 'row-second', sequence: 4, text: '另一道题目' })
    initial.preview.summary.ready = 2
    mocks.api.previewQuestionImport.mockResolvedValue(initial)
    const wrapper = mount(TeacherQuestionImport, { global: { stubs: { RouterLink: routerLink } } })
    const input = wrapper.get('input[type="file"]')
    Object.defineProperty(input.element, 'files', { value: [new File(['test'], 'questions.xlsx')], configurable: true })
    await input.trigger('change')
    await flushPromises()
    expect(wrapper.get('[data-file-summary]').text()).toContain('questions.xlsx')
    expect(wrapper.get('[data-confirm-import]').text()).toContain('确认导入 2 道题')
    await wrapper.get('[data-import-row="row-second"] input[type="checkbox"]').setValue(false)
    await wrapper.get('[data-next-question]').trigger('click')
    expect(wrapper.get('[data-question-detail]').text()).toContain('题目 2 详细信息')
    expect(wrapper.get('[data-question-detail] [data-option="A"]').text()).toContain('甲\n第一行')
    await wrapper.get('input[type="search"]').setValue('另一道')
    expect(wrapper.findAll('[data-import-row]')).toHaveLength(1)
    expect(wrapper.get('[data-import-row]').attributes('data-import-row')).toBe('row-second')
    await wrapper.get('[data-confirm-import]').trigger('click')
    await flushPromises()
    expect(mocks.api.confirmQuestionImport).toHaveBeenCalledWith(7, 11, 'preview-1', ['row-ready'])
  })

  it('redirects the retired course KC import route to unified import', async () => {
    mount(TeacherKCImport, { global: { stubs: { RouterLink: routerLink } } })
    await flushPromises()
    expect(mocks.replace).toHaveBeenCalledWith('/teacher/courses/7/banks/11/import/')
  })

  it('copies a selected shared bank through the JSON action before navigating to its private copy', async () => {
    const wrapper = mount(TeacherSharedBanks, { global: { stubs: { RouterLink: routerLink } } })
    await flushPromises()
    await wrapper.get('button[type="button"]').trigger('click')
    await flushPromises()

    expect(mocks.api.copySharedBank).toHaveBeenCalledWith(7, 8, 9, 21)
    expect(mocks.push).toHaveBeenCalledWith('/teacher/courses/7/banks/31/')
  })
})
