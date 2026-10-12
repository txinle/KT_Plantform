import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  api: { banks: vi.fn(), createBank: vi.fn(), copySharedBank: vi.fn() },
  replace: vi.fn(),
  push: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ replace: mocks.replace, push: mocks.push }),
  RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' },
}))
vi.mock('./standalone-api', () => ({
  getTeacherStandaloneQuestionBankApi: () => mocks.api,
  isStandaloneCanonicalRedirect: (response: Record<string, unknown>) => 'redirect_url' in response,
}))

import TeacherStandaloneBanks from './TeacherStandaloneBanks.vue'

describe('TeacherStandaloneBanks', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('uses the server canonical redirect when the legacy bank list has a course destination', async () => {
    mocks.api.banks.mockResolvedValue({ redirect_url: '/teacher/courses/' })

    mount(TeacherStandaloneBanks)
    await flushPromises()

    expect(mocks.replace).toHaveBeenCalledWith('/teacher/courses/')
  })
})
