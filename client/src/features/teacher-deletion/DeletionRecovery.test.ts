import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import App from '@/app/App.vue'
import { acceptBootstrapContext, clearBootstrapContext } from '@/app/bootstrap'
import { ApiError } from '@/api/client'
import TeacherExperimentList from '@/features/teacher-experiments/TeacherExperimentList.vue'

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  deletion: {
    previewExperiment: vi.fn(), deleteExperiment: vi.fn(), registerOperation: vi.fn(),
    operation: vi.fn(), cleanup: vi.fn(),
  },
}))
vi.mock('@/features/teacher-experiments/api', () => ({ getTeacherExperimentApi: () => ({ list: mocks.list }) }))
vi.mock('./api', () => ({ getTeacherDeletionApi: () => mocks.deletion }))

enableAutoUnmount(afterEach)
afterEach(() => { clearBootstrapContext(); vi.restoreAllMocks() })

const operationId = '00000000-0000-4000-8000-000000000021'
const storageKey = 'kt-teacher-deletion:v1:71:7:experiment:13'
const input = { operation_id: operationId, action: 'delete_experiment', object_id: 13, class_id: null, confirmation_token: 'token-13' }
const pending = { operation_id: operationId, state: 'pending', result: null, error_code: '', message: '' }
const deleted = { operation_id: operationId, deleted: true, cleanup_pending: true, cleanup_id: 'cleanup-1' }
const experiment = { id: 13, name: '待删除实验', description: '', question_bank: null,
  status: 'draft', status_label: '草稿', updated_at: '2026-10-18T00:00:00Z' }
function collection(items: object[]) {
  return { capabilities: { can_manage: true }, items, summary: { question_banks: [] },
    page: { number: 1, size: 20, total_items: items.length, total_pages: 1, start: 0, end: items.length } }
}
function authenticate(actor = 71, role = 'teacher') {
  acceptBootstrapContext({ csrf_token: 'csrf-token', user: { id: actor, role, display_name: '教师', username: `teacher-${actor}`,
    student_number: null, home_url: '/teacher/courses/', uses_initial_password: false,
    initial_password_reminder_pending: false, research_consent_required: false, research_consent_version: '' } })
}
async function openList(course = 7) {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/teacher/courses/:courseId/experiments/', component: TeacherExperimentList },
    { path: '/:pathMatch(.*)*', component: { template: '<div />' } },
  ] })
  await router.push(`/teacher/courses/${course}/experiments/`)
  await router.isReady()
  const wrapper = mount(App, { global: { plugins: [router] } })
  await flushPromises()
  return { wrapper, router }
}

describe('persisted deletion recovery from the application', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    sessionStorage.clear()
    authenticate()
    vi.spyOn(crypto, 'randomUUID').mockReturnValue(operationId)
    mocks.list.mockResolvedValue(collection([experiment]))
    mocks.deletion.previewExperiment.mockResolvedValue({ action: 'delete_experiment', object_id: 13,
      object_name: '待删除实验', allowed: true, blocked_reason: '', experiments: [],
      counts: { class_count: 0, session_count: 0, export_count: 0, member_count: 0 }, confirmation_token: 'token-13' })
    mocks.deletion.registerOperation.mockResolvedValue(pending)
    mocks.deletion.deleteExperiment.mockRejectedValue(new Error('response lost after commit'))
    mocks.deletion.operation.mockResolvedValue(pending)
    mocks.deletion.cleanup.mockResolvedValue({ state: 'running', status: 'pending', cleanup_pending: true, error: '' })
  })

  it.each(['pending', 'cleanup-pending', 'cleanup-failed'])('recovers %s after reloading the parent list without the deleted target', async state => {
    const first = await openList()
    await first.wrapper.findAll('[data-delete-experiment="13"]')[0]!.trigger('click')
    await flushPromises()
    await first.wrapper.get('[data-deletion-confirm]').trigger('click')
    await flushPromises()
    expect(sessionStorage.getItem(storageKey)).toContain('"submitted":true')
    first.wrapper.unmount()

    mocks.list.mockResolvedValue(collection([]))
    mocks.deletion.operation.mockResolvedValue(state === 'pending' ? pending : { ...pending, state: 'succeeded', result: deleted })
    if (state === 'cleanup-failed') mocks.deletion.cleanup.mockResolvedValue({ state: 'failed', status: 'pending', cleanup_pending: true, error: '文件清理失败' })
    const restored = await openList()
    expect(restored.wrapper.find('[data-delete-experiment="13"]').exists()).toBe(false)
    expect(restored.wrapper.get('[data-deletion-recover]').element.compareDocumentPosition(restored.wrapper.get('h1').element) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0)
    await restored.wrapper.get('[data-deletion-recover]').trigger('click')
    await flushPromises()
    expect(restored.wrapper.text()).toContain(state === 'pending' ? '删除结果待核实' : state === 'cleanup-failed' ? '数据已删除，文件清理失败' : '数据已删除，文件清理中')
    expect(mocks.deletion.operation).toHaveBeenCalledTimes(2)
    expect(mocks.deletion.previewExperiment).toHaveBeenCalledTimes(1)
    expect(mocks.deletion.registerOperation).toHaveBeenCalledTimes(1)
    expect(mocks.deletion.deleteExperiment).toHaveBeenCalledTimes(1)
  })

  it('offers recovery to an administrator who can use the same deletion APIs', async () => {
    authenticate(71, 'admin')
    sessionStorage.setItem(storageKey, JSON.stringify({ input, submitted: true }))
    mocks.list.mockResolvedValue(collection([]))
    const { wrapper } = await openList()
    await wrapper.get('[data-deletion-recover]').trigger('click')
    await flushPromises()
    expect(mocks.deletion.operation).toHaveBeenCalledOnce()
    expect(mocks.deletion.deleteExperiment).not.toHaveBeenCalled()
  })

  it('offers only the current actor and course and aborts recovery when that scope changes', async () => {
    sessionStorage.setItem(storageKey, JSON.stringify({ input, submitted: true }))
    sessionStorage.setItem('kt-teacher-deletion:v1:72:7:experiment:14', JSON.stringify({ input: { ...input, object_id: 14 }, submitted: true }))
    mocks.list.mockResolvedValue(collection([]))
    let resolve!: (value: unknown) => void
    mocks.deletion.operation.mockReturnValue(new Promise(done => { resolve = done }))
    const { wrapper, router } = await openList()
    expect(wrapper.findAll('[data-deletion-recover]')).toHaveLength(1)
    await wrapper.get('[data-deletion-recover]').trigger('click')
    await flushPromises()
    const signal = mocks.deletion.operation.mock.calls[0]![2] as AbortSignal
    await router.push('/teacher/courses/8/experiments/')
    await flushPromises()
    expect(signal.aborted).toBe(true)
    resolve({ ...pending, state: 'succeeded', result: { ...deleted, cleanup_pending: false } })
    await flushPromises()
    expect(wrapper.find('[data-deletion-recover]').exists()).toBe(false)
    expect(wrapper.find('[data-operation-refresh]').exists()).toBe(false)
    await router.push('/teacher/courses/7/experiments/')
    await flushPromises()
    authenticate(73)
    await flushPromises()
    expect(wrapper.find('[data-deletion-recover]').exists()).toBe(false)
    expect(sessionStorage.getItem(storageKey)).not.toBeNull()
  })

  it('recovers an admission-only saved operation by reads without automatically submitting deletion', async () => {
    sessionStorage.setItem(storageKey, JSON.stringify({ input, submitted: false }))
    mocks.list.mockResolvedValue(collection([]))
    const { wrapper } = await openList()
    await wrapper.get('[data-deletion-recover]').trigger('click')
    await flushPromises()
    expect(mocks.deletion.operation).toHaveBeenCalledOnce()
    expect(mocks.deletion.deleteExperiment).not.toHaveBeenCalled()
    expect(mocks.deletion.registerOperation).not.toHaveBeenCalled()
    expect(wrapper.get('[data-deletion-confirm]').attributes('disabled')).toBeDefined()
  })

  it('keeps a missing receipt unresolved without offering registration or deletion again', async () => {
    sessionStorage.setItem(storageKey, JSON.stringify({ input, submitted: false }))
    mocks.list.mockResolvedValue(collection([]))
    mocks.deletion.operation.mockRejectedValue(new ApiError(404, { error: { code: 'NOT_FOUND', message: '未找到操作回执' } }))
    const { wrapper } = await openList()
    await wrapper.get('[data-deletion-recover]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('删除结果待核实')
    expect(wrapper.text()).toContain('未找到操作回执')
    expect(wrapper.find('[data-operation-register]').exists()).toBe(false)
    expect(wrapper.get('[data-deletion-confirm]').attributes('disabled')).toBeDefined()
    expect(mocks.deletion.deleteExperiment).not.toHaveBeenCalled()
    expect(sessionStorage.getItem(storageKey)).not.toBeNull()
  })

  it('recovers the accepted result and removes the saved entry only after confirmed cleanup completion', async () => {
    sessionStorage.setItem(storageKey, JSON.stringify({ input, submitted: true, result: deleted }))
    mocks.list.mockResolvedValue(collection([]))
    mocks.deletion.cleanup.mockResolvedValue({ state: 'completed', status: 'completed', cleanup_pending: false, error: '' })
    const { wrapper } = await openList()
    await wrapper.get('[data-deletion-recover]').trigger('click')
    await flushPromises()
    expect(mocks.deletion.cleanup).toHaveBeenCalledWith(7, 'cleanup-1', expect.any(AbortSignal))
    expect(mocks.deletion.operation).not.toHaveBeenCalled()
    expect(mocks.deletion.deleteExperiment).not.toHaveBeenCalled()
    expect(sessionStorage.getItem(storageKey)).toBeNull()
    expect(wrapper.find('[data-deletion-recover]').exists()).toBe(false)
  })
})
