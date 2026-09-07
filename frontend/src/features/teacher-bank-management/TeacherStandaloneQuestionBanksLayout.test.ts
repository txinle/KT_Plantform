import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  api: { detail: vi.fn() },
  replace: vi.fn(),
  route: { name: '', params: {} as Record<string, string> },
}))

vi.mock('vue-router', () => ({
  useRoute: () => mocks.route,
  useRouter: () => ({ replace: mocks.replace }),
  RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' },
  RouterView: { template: '<div />' },
}))
vi.mock('@/app/bootstrap', () => ({
  getBootstrapContext: () => ({
    csrf_token: 'csrf-token',
    user: { display_name: '教师', username: 'teacher' },
  }),
}))
vi.mock('./standalone-api', () => ({
  getTeacherStandaloneQuestionBankApi: () => mocks.api,
  isStandaloneBankRedirect: (response: Record<string, unknown>) => 'redirect_url' in response,
}))

import TeacherStandaloneQuestionBanksLayout from './TeacherStandaloneQuestionBanksLayout.vue'

describe('TeacherStandaloneQuestionBanksLayout', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.route.params = { bankId: '11' }
  })

  it.each([
    ['teacher-standalone-question-bank-detail', {}, { destination: 'detail' }],
    ['teacher-standalone-question-import', {}, { destination: 'question_import' }],
    ['teacher-standalone-kc-import', {}, { destination: 'kc_import' }],
    ['teacher-standalone-question-editor', { questionId: '31' }, { destination: 'question_editor', questionId: 31 }],
  ])('asks the server to resolve %s before mounting a legacy child route', async (name, params, destination) => {
    mocks.route.name = name
    mocks.route.params = { bankId: '11', ...params }
    mocks.api.detail.mockResolvedValue({ redirect_url: '/teacher/courses/7/banks/11/' })

    mount(TeacherStandaloneQuestionBanksLayout)
    await flushPromises()

    expect(mocks.api.detail).toHaveBeenCalledWith(11, destination)
    expect(mocks.replace).toHaveBeenCalledWith('/teacher/courses/7/banks/11/')
  })
})
