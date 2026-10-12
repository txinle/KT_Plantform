import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  api: { concepts: vi.fn() },
  replace: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ replace: mocks.replace }),
  RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' },
}))
vi.mock('./standalone-api', () => ({
  getTeacherStandaloneQuestionBankApi: () => mocks.api,
  isStandaloneCanonicalRedirect: (response: Record<string, unknown>) => 'redirect_url' in response,
}))

import TeacherStandaloneConcepts from './TeacherStandaloneConcepts.vue'

describe('TeacherStandaloneConcepts', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('uses the server canonical redirect when knowledge concepts belong to a course', async () => {
    mocks.api.concepts.mockResolvedValue({ redirect_url: '/teacher/courses/' })

    mount(TeacherStandaloneConcepts)
    await flushPromises()

    expect(mocks.replace).toHaveBeenCalledWith('/teacher/courses/')
  })
})
