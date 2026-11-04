import { flushPromises, mount } from '@vue/test-utils'
import { beforeAll, describe, expect, it, vi } from 'vitest'
import SamplingRangePicker from './SamplingRangePicker.vue'

vi.mock('./ExperimentQuestionPreview.vue', () => ({ default: { props: ['questionId'], template: '<div data-preview>{{ questionId }}</div>' } }))
const questions = Array.from({ length: 14 }, (_, index) => ({ question_id: index + 1, question_type: 'reading' as const, code: `R${index + 1}`, title: `Article ${index + 1}`, text: 'Text', item_count: 3, question_bank_id: 3 }))
const props = { questionType: 'reading' as const, label: '阅读理解', courseId: 7, quota: 2, selected: [] as number[] | null, questions, banks: [{ id: 3, name: 'Reading bank' }], loading: false, error: '' }

describe('SamplingRangePicker', () => {
  beforeAll(() => {
    Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value() { this.setAttribute('open', '') } })
    Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true, value() { this.removeAttribute('open') } })
  })
  it('retains selected IDs across search and pages and previews complete questions lazily', async () => {
    const wrapper = mount(SamplingRangePicker, { props })
    await flushPromises()
    expect(wrapper.findAll('[data-range-question]')).toHaveLength(12)
    expect(wrapper.find('[data-preview]').exists()).toBe(false)
    const details = wrapper.get<HTMLDetailsElement>('details')
    details.element.open = true
    await details.trigger('toggle')
    expect(wrapper.get('[data-preview]').text()).toBe('1')
    await wrapper.get('[data-range-question="1"]').setValue(true)
    await wrapper.get('[data-next]').trigger('click')
    expect(wrapper.findAll('[data-range-question]')).toHaveLength(2)
    await wrapper.get('[data-range-select-page]').trigger('click')
    await wrapper.get('[data-range-selected-only]').setValue(true)
    expect(wrapper.findAll('[data-range-question]')).toHaveLength(3)
    await wrapper.get('input[type="search"]').setValue('Article 14')
    expect(wrapper.findAll('[data-range-question]')).toHaveLength(1)
    await wrapper.get('[data-apply-range]').trigger('click')
    expect(wrapper.emitted('apply')).toEqual([[[1, 13, 14]]])
    wrapper.unmount()
  })
  it('requires an explicit all-mode choice to remove a saved restriction', async () => {
    const wrapper = mount(SamplingRangePicker, { props: { ...props, selected: [1] } })
    await flushPromises()
    await wrapper.get('[data-apply-range]').trigger('click')
    expect(wrapper.emitted('apply')).toBeUndefined()
    expect(wrapper.text()).toContain('至少选择 2 道')
    await wrapper.get('[data-range-all]').setValue(true)
    await wrapper.get('[data-apply-range]').trigger('click')
    expect(wrapper.emitted('apply')).toEqual([[null]])
    wrapper.unmount()
  })
})
