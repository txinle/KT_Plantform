import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
const mocks = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@/api/client', () => ({ createApiClient: () => ({ get: mocks.get }) }))
vi.mock('vue-router', () => ({ useRoute: () => ({ params: { sessionId: '7' } }) }))
vi.mock('@/app/bootstrap', () => ({ getBootstrapContext: () => ({ user: { display_name: 'Student' }, csrf_token: 'token' }) }))
import StudentSessionComplete from './StudentSessionComplete.vue'
const summary = { session_id: 7, experiment_name: 'English assessment', status: 'completed', status_label: '已完成', submission_count: 3, attempt_count: 3, presentation_question_count: 2, return_url: '/student/' }
const options = { global: { stubs: { WorkspaceShell: { template: '<main><slot /></main>' } } } }
describe('session feedback', () => {
  it('renders pictures carried by an authorized submitted answer and explanation', async () => {
    const key = `${'a'.repeat(64)}.png`
    const marker = `{{image:${key}}}`
    mocks.get.mockResolvedValue({ ...summary, result: { feedback_phase: 'available', items: [{ question_id: 1, question_code: 'CHOICE', submitted_answer: `A ${marker}`, correct_answer: `B ${marker}`, explanation: marker, images: { [key]: 'data:image/png;base64,aGVsbG8=' } }] } })
    const wrapper = mount(StudentSessionComplete, options)
    await flushPromises()
    expect(wrapper.findAll('tbody img')).toHaveLength(3)
  })
  it('preserves every authorized response, score, correctness, explanation and comparison', async () => {
    mocks.get.mockResolvedValue({ ...summary, result: { feedback_phase: 'available', items: [
      { question_id: 1, question_code: 'READ-1', submitted_answer: 'B', is_correct: false, score: '0', max_score: '2', correct_answer: 'A', explanation: 'Frozen explanation' },
      { question_id: 2, question_code: 'ESSAY', submitted_answer: 'My complete essay.' },
    ], score: { earned: '2', maximum: '5', percent: '40' }, correctness: { correct: 1, graded: 3, percent: '33.3' }, comparison: { class_average: { status: 'available', percent: '62', sample_size: 8 }, rank: { status: 'available', position: 3, sample_size: 8 } } } })
    const wrapper = mount(StudentSessionComplete, options)
    await flushPromises()
    expect(wrapper.findAll('tbody tr')).toHaveLength(2)
    for (const text of ['My complete essay.', 'Frozen explanation', '33.3%', '62%', '第 3 / 8 名', '完整题目']) expect(wrapper.text()).toContain(text)
  })
  it('does not invent feedback columns when the server omits protected fields', async () => {
    mocks.get.mockResolvedValue({ ...summary, result: { feedback_phase: 'disabled' } })
    const wrapper = mount(StudentSessionComplete, options)
    await flushPromises()
    expect(wrapper.find('table').exists()).toBe(false)
    expect(wrapper.text()).toContain('仅显示提交状态')
    expect(wrapper.text()).not.toContain('最终得分')
    expect(wrapper.text()).not.toContain('班级比较')
  })
})
