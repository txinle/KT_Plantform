import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const route = { params: { courseId: '7' }, query: { page: '2' } as Record<string, string> }
const mocks = vi.hoisted(() => ({
  operationId: '',
  api: { list: vi.fn(), archive: vi.fn() },
  deletion: {
    previewExperiment: vi.fn(), deleteExperiment: vi.fn(),
    previewClassExperiment: vi.fn(), removeClassExperiment: vi.fn(),
    previewClass: vi.fn(), deleteClass: vi.fn(), cleanup: vi.fn(),
    registerOperation: vi.fn(), operation: vi.fn(),
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
vi.mock('@/app/bootstrap', () => ({
  getBootstrapContext: () => ({ csrf_token: 'csrf-token', user: { id: 71, role: 'teacher' } }),
  bootstrapContext: { value: { user: { id: 71, role: 'teacher' } } },
  getAuthenticationGeneration: () => 0,
}))

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
    sessionStorage.clear()
    mocks.operationId = ''
    mocks.deletion.registerOperation.mockImplementation(async (...args: [number, { operation_id: string }]) => {
      mocks.operationId = args[1].operation_id
      return { operation_id: mocks.operationId, state: 'pending', result: null }
    })
    mocks.deletion.operation.mockImplementation(async () => ({ operation_id: mocks.operationId, state: 'pending', result: null }))
    route.query = { page: '2' }
    mocks.api.list.mockResolvedValue(collection([experiment]))
    mocks.deletion.previewExperiment.mockResolvedValue({
      action: 'delete_experiment', object_id: 13, object_name: '待删除实验', allowed: true, blocked_reason: '',
      experiments: [], counts: { class_count: 0, session_count: 0, export_count: 0, member_count: 0 }, confirmation_token: 'token-13',
    })
    mocks.deletion.deleteExperiment.mockImplementation(async () => ({ deleted: true, cleanup_pending: false, cleanup_id: null, operation_id: mocks.operationId }))
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
    expect(mocks.deletion.previewExperiment).toHaveBeenCalledWith(7, 13, expect.any(AbortSignal))
    await wrapper.get('[data-deletion-confirm]').trigger('click')
    await flushPromises()

    expect(mocks.deletion.deleteExperiment).toHaveBeenCalledWith(7, 13, 'token-13', mocks.operationId)
    expect(mocks.api.list).toHaveBeenNthCalledWith(2, 7, 2)
    expect(mocks.push).toHaveBeenCalledWith({ query: {} })
    expect(mocks.api.list).toHaveBeenNthCalledWith(3, 7, 1)
  })

  it('archives with confirmation while preserving access to the archived list', async () => {
    mocks.api.list.mockResolvedValueOnce(collection([{ ...experiment, can_archive: true }]))
      .mockResolvedValueOnce(collection([]))
    mocks.api.archive.mockResolvedValue({ ...experiment, status: 'archived', can_archive: false })
    const wrapper = mount(TeacherExperimentList)
    await flushPromises()
    await wrapper.findAll('[data-archive-experiment="13"]')[0]!.trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="dialog"]').text()).toContain('保留')
    expect(mocks.api.archive).not.toHaveBeenCalled()
    await wrapper.get('[data-archive-confirm]').trigger('click')
    await flushPromises()
    expect(mocks.api.archive).toHaveBeenCalledWith(7, 13)
    expect(mocks.deletion.deleteExperiment).not.toHaveBeenCalled()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    await wrapper.get('[data-show-archived]').trigger('click')
    await flushPromises()
    expect(mocks.push).toHaveBeenLastCalledWith({ query: { archived: '1' } })
    expect(mocks.api.list).toHaveBeenLastCalledWith(7, 1, true)
  })

  it('does not offer archiving without the server capability and keeps archive failures visible', async () => {
    const wrapper = mount(TeacherExperimentList)
    await flushPromises()
    expect(wrapper.find('[data-archive-experiment]').exists()).toBe(false)
    wrapper.unmount()
    mocks.api.list.mockResolvedValue(collection([{ ...experiment, can_archive: true }]))
    mocks.api.archive.mockRejectedValue(new Error('归档失败，请重试'))
    const authorized = mount(TeacherExperimentList)
    await flushPromises()
    await authorized.findAll('[data-archive-experiment="13"]')[0]!.trigger('click')
    await authorized.get('[data-archive-confirm]').trigger('click')
    await flushPromises()
    expect(authorized.get('[role="alert"]').text()).toContain('归档失败')
    expect(authorized.find('[role="dialog"]').exists()).toBe(true)
  })
})
