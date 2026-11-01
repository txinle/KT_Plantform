import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ExperimentQuestionPreview from './ExperimentQuestionPreview.vue'
const mocks = vi.hoisted(() => ({ get: vi.fn(), groupGet: vi.fn() }))
vi.mock('@/api/client', () => ({ createApiClient: () => ({ get: mocks.get }) }))
vi.mock('@/app/bootstrap', () => ({ getBootstrapContext: () => ({ csrf_token: 'token' }) }))
vi.mock('@/features/teacher-bank-management/group-api', () => ({ getQuestionGroupApi: () => ({ get: mocks.groupGet }) }))
describe('experiment inline question preview', () => {
  beforeEach(() => vi.clearAllMocks())
  it('loads the complete reading group readonly rather than only its representative item', async () => {
    mocks.get.mockResolvedValue({ question: { group_id: 9, text: 'First item only' } })
    mocks.groupGet.mockResolvedValue({ id: 9, kind: 'reading', title: 'Full reading', material: 'Shared article', instructions: '', members: [
      { item_key: 'a', group_order: 1, text: 'First question', options: [{ key: 'A', text: 'Option one' }], answer: 'SECRET_ANSWER', explanation: 'SECRET_EXPLANATION' },
      { item_key: 'b', group_order: 2, text: 'Second question', options: [{ key: 'B', text: 'Option two' }] },
    ] })
    const wrapper = mount(ExperimentQuestionPreview, { props: { courseId: 7, bankId: 3, questionId: 41 } })
    await flushPromises()
    expect(mocks.get).toHaveBeenCalledWith('/api/v1/teacher/courses/7/banks/3/questions/41/editor/?readonly=1')
    expect(mocks.groupGet).toHaveBeenCalledWith(9)
    expect(wrapper.text()).toContain('Shared article')
    expect(wrapper.text()).toContain('Second question')
    expect(wrapper.text()).not.toContain('SECRET_')
    expect(wrapper.findAll('fieldset').every(item => item.attributes('disabled') !== undefined)).toBe(true)
    wrapper.unmount()
  })
  it('shows an API failure explicitly', async () => {
    mocks.get.mockRejectedValue(new Error('无权读取题目'))
    const wrapper = mount(ExperimentQuestionPreview, { props: { courseId: 7, bankId: 3, questionId: 41 } })
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toBe('无权读取题目')
    wrapper.unmount()
  })
})
