import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const route = { params: { courseId: '7' }, query: { page: '2' } as Record<string, string> }
const mocks = vi.hoisted(() => ({
  api: { list: vi.fn() },
  deletion: {
    previewExperiment: vi.fn(), deleteExperiment: vi.fn(),
    previewClassExperiment: vi.fn(), removeClassExperiment: vi.fn(),
    previewClass: vi.fn(), deleteClass: vi.fn(), cleanup: vi.fn(),
  },
  push: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => route,
  useRouter: () => ({ push: mocks.push }),
  RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' },
}))
vi.mock('./api', () => ({ getTeacherExperimentApi: () => mocks.api }))
vi.mock('@/features/teacher-deletion/api', () => ({ getTeacherDeletionApi: () => mocks.deletion }))

import TeacherExperimentList from './TeacherExperimentList.vue'

function collection(items: object[], page = 2, totalPages = 2) {
  return {
    capabilities: { can_manage: true },
    items,
    summary: { question_banks: [] },
    page: { number: page, size: 20, total_items: items.length, total_pages: totalPages, start: items.length ? 21 : 0, end: items.length ? 21 : 0 },
  }
}

const experiment = {
  id: 13, name: '待删除实验', description: '', question_bank: null,
  status: 'draft', status_label: '草稿', selection_mode_label: '固定题目', sampling_mode_label: null,
  updated_at: '2026-09-09T00:00:00Z',
}

describe('TeacherExperimentList deletion', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    route.query = { page: '2' }
    mocks.api.list.mockResolvedValue(collection([experiment]))
    mocks.deletion.previewExperiment.mockResolvedValue({
      action: 'delete_experiment', object_id: 13, object_name: '待删除实验', allowed: true, blocked_reason: '',
      experiments: [], counts: { class_count: 0, session_count: 0, export_count: 0, member_count: 0 }, confirmation_token: 'token-13',
    })
    mocks.deletion.deleteExperiment.mockResolvedValue({ deleted: true, cleanup_pending: false, cleanup_id: null })
  })

  it('renders a missing draft bank and deletes through the server preview', async () => {
    mocks.api.list
      .mockResolvedValueOnce(collection([experiment]))
      .mockResolvedValueOnce(collection([], 2, 1))
      .mockResolvedValueOnce(collection([], 1, 1))
    const wrapper = mount(TeacherExperimentList)
    await flushPromises()

    expect(wrapper.text()).toContain('未选择题库')
    await wrapper.findAll('[data-delete-experiment="13"]')[0]!.trigger('click')
    await flushPromises()
    expect(mocks.deletion.previewExperiment).toHaveBeenCalledWith(7, 13)
    await wrapper.get('[data-deletion-confirm]').trigger('click')
    await flushPromises()

    expect(mocks.deletion.deleteExperiment).toHaveBeenCalledWith(7, 13, 'token-13')
    expect(mocks.api.list).toHaveBeenNthCalledWith(2, 7, 2)
    expect(mocks.push).toHaveBeenCalledWith({ query: {} })
    expect(mocks.api.list).toHaveBeenNthCalledWith(3, 7, 1)
  })
})
