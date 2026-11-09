import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  api: { detail: vi.fn(), previewQuestionImport: vi.fn(), confirmQuestionImport: vi.fn() },
  push: vi.fn(),
}))
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { bankId: '11' } }),
  useRouter: () => ({ push: mocks.push }),
  RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' },
}))
vi.mock('./standalone-api', () => ({ getTeacherStandaloneQuestionBankApi: () => mocks.api, isStandaloneBankRedirect: (response: object) => 'redirect_url' in response }))

import TeacherStandaloneQuestionImport from './TeacherStandaloneQuestionImport.vue'

describe('TeacherStandaloneQuestionImport', () => {
  it('requires exactly one source document and clears a previous preview for an invalid new selection', async () => {
    const wrapper = mount(TeacherStandaloneQuestionImport)
    const input = wrapper.get('input[type="file"]')
    const file = new File(['rows'], 'questions.csv')
    Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
    await input.trigger('change')
    await flushPromises()
    expect(wrapper.find('[data-confirm-standalone-import]').exists()).toBe(true)
    Object.defineProperty(input.element, 'files', { value: [file, new File(['rows'], 'other.xlsx')], configurable: true })
    await input.trigger('change')
    await flushPromises()
    expect(mocks.api.previewQuestionImport).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('请选择一个 Word、Excel 或 CSV 题库文件')
    expect(wrapper.find('[data-confirm-standalone-import]').exists()).toBe(false)
    wrapper.unmount()
  })
  it('keeps the existing format selector and uploads one source document with companion images', async () => {
    const wrapper = mount(TeacherStandaloneQuestionImport)
    const input = wrapper.get('input[type="file"]')
    const file = new File(['rows'], 'questions.csv')
    const image = new File(['image'], 'figure.png', { type: 'image/png' })
    expect(input.attributes('multiple')).toBeDefined()
    expect(input.attributes('accept')).toContain('.png')
    expect(wrapper.get('a[href="/api/v1/teacher/import-templates/question-images.zip"]').text()).toContain('图片')
    Object.defineProperty(input.element, 'files', { value: [image, file], configurable: true })
    await input.trigger('change')
    await flushPromises()
    expect(mocks.api.previewQuestionImport).toHaveBeenCalledWith(11, file, [image])
    wrapper.unmount()
  })
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.api.detail.mockResolvedValue({ bank: { name: '独立题库' } })
    mocks.api.previewQuestionImport.mockResolvedValue({
      preview_id: 'preview-1',
      preview: { confirmable: true, summary: { ready: 1, draft: 1, duplicate: 0, invalid: 0 }, rows: [
        { row_id: 'r1', errors: [], options: [], accepted_answers: [], sequence: 1, text: '完整题目', question_type: 'short_answer', question_type_label: '简答题', option_count: 0, teacher_difficulty: null, classification: 'ready', draft_reasons: [] },
        { row_id: 'r2', errors: ['题干为空。'], options: [], accepted_answers: [], sequence: 2, text: '', question_type: 'short_answer', question_type_label: '简答题', option_count: 0, teacher_difficulty: null, classification: 'draft', draft_reasons: ['题干为空。'] },
      ] },
    })
    mocks.api.confirmQuestionImport.mockResolvedValue({ created: 2, active: 1, draft: 1, rows: [], detail_url: '/teacher/banks/11/' })
  })

  it('labels complete and incomplete rows as server-classified valid imports', async () => {
    const wrapper = mount(TeacherStandaloneQuestionImport)
    const input = wrapper.get('input[type="file"]')
    Object.defineProperty(input.element, 'files', { value: [new File(['rows'], 'questions.csv')], configurable: true })
    await input.trigger('change')
    await flushPromises()

    expect(wrapper.text()).toContain('导入后可用')
    expect(wrapper.text()).toContain('保存为草稿')
    expect(wrapper.text()).toContain('题干为空。')
    await wrapper.get('[data-confirm-standalone-import]').trigger('click')
    await flushPromises()
    expect(mocks.push).toHaveBeenCalledWith('/teacher/banks/11/')
  })

  it('confirms groups explicitly and never selects their child rows individually', async () => {
    mocks.api.previewQuestionImport.mockResolvedValue({ preview_id: 'group-preview', preview: { confirmable: true, summary: { ready: 1, draft: 0, duplicate: 0, invalid: 0 }, rows: [{ row_id: 'r1', group_id: 'g1', sequence: 1, text: 'Why?', question_type: 'single_choice', options: [], classification: 'ready' }], groups: [{ group_id: 'g1', source_group_code: 'R001', kind: 'reading', title: 'Garden', material: 'Garden.', material_segments: [], instructions: '', content_language: 'en', member_row_ids: ['r1'], classification: 'ready', errors: [], item_count: 1, total_max_score: '2.00' }] } })
    const wrapper = mount(TeacherStandaloneQuestionImport)
    const input = wrapper.get('input[type="file"]')
    Object.defineProperty(input.element, 'files', { value: [new File(['rows'], 'group.docx')], configurable: true })
    await input.trigger('change')
    await flushPromises()
    expect(wrapper.get('[data-import-row="group:g1"]').text()).toContain('Garden')
    await wrapper.get('[data-confirm-standalone-import]').trigger('click')
    await flushPromises()
    expect(mocks.api.confirmQuestionImport).toHaveBeenCalledWith(11, 'group-preview', [], ['g1'])
  })
})
