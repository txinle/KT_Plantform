import { mount, flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
const mocks = vi.hoisted(() => ({ detail: vi.fn(), replace: vi.fn(), route: { path: '/manage/resources/banks/2/questions/3/edit/', params: { bankId: '2', questionId: '3' } as Record<string, string> } }))
vi.mock('vue-router', () => ({ useRoute: () => mocks.route, useRouter: () => ({ replace: mocks.replace }) }))
vi.mock('@/features/teacher-bank-management/standalone-api', () => ({ getTeacherStandaloneQuestionBankApi: () => mocks, isStandaloneBankRedirect: (value: object) => 'redirect_url' in value }))
import AdminBankWorkspace from './AdminBankWorkspace.vue'
const global = { stubs: { AdminDomainShell: { template: '<main><slot /></main>' }, RouterView: { template: '<div data-editor />' } } }
describe('administrator bank routing', () => {
  beforeEach(() => vi.resetAllMocks())
  it('keeps the canonical course-bank redirect inside administrator pages', async () => {
    mocks.detail.mockResolvedValue({ redirect_url: '/teacher/courses/7/banks/2/questions/3/edit/' })
    const wrapper = mount(AdminBankWorkspace, { props: { active: 'bank' }, global })
    await flushPromises()
    expect(mocks.detail).toHaveBeenCalledWith(2, { destination: 'question_editor', questionId: 3 })
    expect(mocks.replace).toHaveBeenCalledWith('/manage/resources/courses/7/banks/2/questions/3/edit/')
    expect(wrapper.find('[data-editor]').exists()).toBe(false)
  })
  it('does not mount an editor after the bank lookup fails', async () => {
    mocks.detail.mockRejectedValue(new Error('题库不可访问'))
    const wrapper = mount(AdminBankWorkspace, { props: { active: 'bank' }, global })
    await flushPromises()
    expect(wrapper.find('[data-editor]').exists()).toBe(false)
    expect(wrapper.get('[role="alert"]').text()).toBe('题库不可访问')
  })
})
