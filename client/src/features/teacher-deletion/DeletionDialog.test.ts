import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/client'

const mocks = vi.hoisted(() => ({
  api: {
    previewExperiment: vi.fn(), deleteExperiment: vi.fn(),
    previewClassExperiment: vi.fn(), removeClassExperiment: vi.fn(),
    previewClass: vi.fn(), deleteClass: vi.fn(), cleanup: vi.fn(),
    registerOperation: vi.fn(), operation: vi.fn(),
  },
  identity: { actor: 71, generation: 0 },
  setIdentity: vi.fn(),
}))

vi.mock('./api', () => ({ getTeacherDeletionApi: () => mocks.api }))
vi.mock('@/app/bootstrap', async () => {
  const { reactive, computed } = await import('vue')
  const identity = reactive(mocks.identity)
  mocks.setIdentity.mockImplementation((actor: number) => { identity.actor = actor; identity.generation += 1 })
  return { bootstrapContext: computed(() => ({ user: { id: identity.actor } })), getAuthenticationGeneration: () => identity.generation }
})

import DeletionDialog from './DeletionDialog.vue'

enableAutoUnmount(afterEach)
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })

const operationId = '00000000-0000-4000-8000-000000000001'
const deletionResult = { deleted: true, cleanup_pending: false, cleanup_id: null, operation_id: operationId }
const pendingOperation = { operation_id: operationId, state: 'pending', result: null, error_code: '', message: '', execute_before: '2026-10-18T00:00:30Z' }

const counts = { class_count: 2, session_count: 8, export_count: 3, member_count: 0 }
const experimentPreview = {
  action: 'delete_experiment' as const,
  object_id: 13,
  object_name: '认知负荷实验',
  allowed: true,
  blocked_reason: '',
  experiments: [{
    id: 13,
    name: '认知负荷实验',
    class_count: 2,
    session_count: 8,
    class_session_count: 0,
    export_count: 3,
    classes: [{ id: 11, name: '实验一班', session_count: 5 }, { id: 12, name: '实验二班', session_count: 3 }],
    detail_url: '/teacher/courses/7/experiments/13/',
  }],
  counts,
  confirmation_token: 'experiment-token',
}

describe('DeletionDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    sessionStorage.clear()
    mocks.setIdentity(71)
    vi.spyOn(crypto, 'randomUUID').mockReturnValue(operationId)
    mocks.api.previewExperiment.mockResolvedValue(experimentPreview)
    mocks.api.deleteExperiment.mockResolvedValue(deletionResult)
    mocks.api.registerOperation.mockResolvedValue(pendingOperation)
    mocks.api.operation.mockResolvedValue(pendingOperation)
  })

  it('persists and registers the exact operation before sending deletion once', async () => {
    let resolve!: (value: typeof pendingOperation) => void
    mocks.api.registerOperation.mockReturnValue(new Promise(done => { resolve = done }))
    const wrapper = mount(DeletionDialog, { props: { courseId: 7, target: { kind: 'experiment', experimentId: 13 } } })
    await flushPromises()
    await wrapper.get('[data-deletion-confirm]').trigger('click')
    expect(mocks.api.registerOperation).toHaveBeenCalledWith(7, { operation_id: operationId, action: 'delete_experiment', object_id: 13, class_id: null, confirmation_token: 'experiment-token' })
    expect(sessionStorage.length).toBe(1)
    expect(sessionStorage.getItem(sessionStorage.key(0)!)).toContain(operationId)
    expect(mocks.api.deleteExperiment).not.toHaveBeenCalled()
    resolve(pendingOperation)
    await flushPromises()
    expect(mocks.api.deleteExperiment).toHaveBeenCalledWith(7, 13, 'experiment-token', operationId)
    expect(wrapper.emitted('completed')).toHaveLength(1)
    expect(sessionStorage.length).toBe(0)
  })

  it.each([new Error('bad JSON'), new ApiError(503, { error: { code: 'SERVER_ERROR' } })])('queries the persistent receipt after an uncertain deletion response %s', async (failure) => {
    mocks.api.deleteExperiment.mockRejectedValue(failure)
    mocks.api.operation.mockResolvedValue({ ...pendingOperation, state: 'succeeded', result: deletionResult })
    const wrapper = mount(DeletionDialog, { props: { courseId: 7, target: { kind: 'experiment', experimentId: 13 } } })
    await flushPromises()
    await wrapper.get('[data-deletion-confirm]').trigger('click')
    await flushPromises()
    expect(mocks.api.operation).toHaveBeenCalledWith(7, operationId, expect.any(AbortSignal))
    expect(mocks.api.deleteExperiment).toHaveBeenCalledTimes(1)
    expect(wrapper.emitted('completed')).toHaveLength(1)
  })

  it.each([{ deleted: false, cleanup_pending: false, cleanup_id: null }, { deleted: true, cleanup_pending: true, cleanup_id: null }])('does not accept an invalid deletion result as success: %j', async (invalid) => {
    mocks.api.deleteExperiment.mockResolvedValue({ ...invalid, operation_id: operationId })
    const wrapper = mount(DeletionDialog, { props: { courseId: 7, target: { kind: 'experiment', experimentId: 13 } } })
    await flushPromises()
    await wrapper.get('[data-deletion-confirm]').trigger('click')
    await flushPromises()
    expect(wrapper.emitted('completed')).toBeUndefined()
    expect(mocks.api.operation).toHaveBeenCalledTimes(1)
    expect(wrapper.get('[data-deletion-confirm]').attributes('disabled')).toBeDefined()
  })

  it('shows the exact busy reason without labelling the preview stale', async () => {
    mocks.api.deleteExperiment.mockRejectedValue(new ApiError(409, { error: { code: 'DELETION_BUSY', message: '正在提交作答，请稍后再试。' } }))
    mocks.api.operation.mockResolvedValue({ ...pendingOperation, state: 'rejected', error_code: 'DELETION_BUSY', message: '正在提交作答，请稍后再试。' })
    const wrapper = mount(DeletionDialog, { props: { courseId: 7, target: { kind: 'experiment', experimentId: 13 } } })
    await flushPromises()
    await wrapper.get('[data-deletion-confirm]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('正在提交作答，请稍后再试。')
    expect(wrapper.find('[data-deletion-refresh]').exists()).toBe(false)
  })

  it('preserves the submitted operation when POST busy reflects an in-flight operation rather than a terminal rejection', async () => {
    mocks.api.deleteExperiment.mockRejectedValue(new ApiError(409, { error: { code: 'DELETION_BUSY', message: '同一操作正在执行，请稍后核实。' } }))
    mocks.api.operation.mockResolvedValue(pendingOperation)
    const wrapper = mount(DeletionDialog, { props: { courseId: 7, target: { kind: 'experiment', experimentId: 13 } } })
    await flushPromises()
    await wrapper.get('[data-deletion-confirm]').trigger('click')
    await flushPromises()
    expect(mocks.api.operation).toHaveBeenCalledTimes(1)
    expect(wrapper.get('[data-deletion-confirm]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[role="alert"]').text()).toContain('同一操作正在执行，请稍后核实。')
    expect(sessionStorage.length).toBe(1)
    expect(wrapper.find('[data-deletion-refresh]').exists()).toBe(false)
  })

  it('treats POST 404 and receipt 404 as unknown and preserves the operation instead of allowing another deletion', async () => {
    mocks.api.deleteExperiment.mockRejectedValue(new ApiError(404, { error: { code: 'NOT_FOUND', message: '目标未找到' } }))
    mocks.api.operation.mockRejectedValue(new ApiError(404, { error: { code: 'NOT_FOUND' } }))
    const wrapper = mount(DeletionDialog, { props: { courseId: 7, target: { kind: 'experiment', experimentId: 13 } } })
    await flushPromises()
    await wrapper.get('[data-deletion-confirm]').trigger('click')
    await flushPromises()
    expect(mocks.api.operation).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('删除结果待核实')
    expect(wrapper.get('[data-deletion-confirm]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-operation-register]').exists()).toBe(false)
    expect(mocks.api.deleteExperiment).toHaveBeenCalledTimes(1)
    expect(sessionStorage.length).toBe(1)
  })

  it('stops unknown-result reads after three checks and never interprets 404 as success', async () => {
    vi.useFakeTimers()
    mocks.api.deleteExperiment.mockRejectedValue(new Error('lost response'))
    mocks.api.operation.mockRejectedValue(new ApiError(404, { error: { code: 'NOT_FOUND' } }))
    const wrapper = mount(DeletionDialog, { props: { courseId: 7, target: { kind: 'experiment', experimentId: 13 } } })
    await flushPromises()
    await wrapper.get('[data-deletion-confirm]').trigger('click')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(30000)
    expect(mocks.api.operation).toHaveBeenCalledTimes(3)
    expect(mocks.api.deleteExperiment).toHaveBeenCalledTimes(1)
    expect(wrapper.emitted('completed')).toBeUndefined()
    expect(wrapper.find('[data-operation-refresh]').exists()).toBe(true)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('keeps database success explicit when file cleanup failed', async () => {
    mocks.api.deleteExperiment.mockResolvedValue({ ...deletionResult, cleanup_pending: true, cleanup_id: 'cleanup-1' })
    mocks.api.cleanup.mockResolvedValue({ state: 'failed', status: 'pending', cleanup_pending: true, error: 'permission denied', attempts: 8, retained_count: 0 })
    const wrapper = mount(DeletionDialog, { props: { courseId: 7, target: { kind: 'experiment', experimentId: 13 } } })
    await flushPromises()
    await wrapper.get('[data-deletion-confirm]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('数据已删除，文件清理失败')
    expect(wrapper.get('[role="alert"]').text()).toContain('permission denied')
    expect(wrapper.emitted('completed')).toBeUndefined()
    expect(wrapper.get('[data-deletion-confirm]').attributes('disabled')).toBeDefined()
  })

  it('restores an uncertain operation after remount without looking up or deleting the gone target', async () => {
    mocks.api.deleteExperiment.mockRejectedValue(new Error('lost response'))
    const props = { courseId: 7, target: { kind: 'experiment' as const, experimentId: 13 } }
    const first = mount(DeletionDialog, { props })
    await flushPromises()
    await first.get('[data-deletion-confirm]').trigger('click')
    await flushPromises()
    first.unmount()
    mocks.api.operation.mockResolvedValue({ ...pendingOperation, state: 'succeeded', result: deletionResult })
    const restored = mount(DeletionDialog, { props })
    await flushPromises()
    expect(mocks.api.previewExperiment).toHaveBeenCalledTimes(1)
    expect(mocks.api.deleteExperiment).toHaveBeenCalledTimes(1)
    expect(restored.emitted('completed')).toHaveLength(1)
  })

  it('does not expose or reuse another account’s pending operation', async () => {
    mocks.api.deleteExperiment.mockRejectedValue(new Error('lost response'))
    const props = { courseId: 7, target: { kind: 'experiment' as const, experimentId: 13 } }
    const wrapper = mount(DeletionDialog, { props })
    await flushPromises()
    await wrapper.get('[data-deletion-confirm]').trigger('click')
    await flushPromises()
    mocks.setIdentity(72)
    await flushPromises()
    expect(mocks.api.previewExperiment).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).not.toContain('删除结果待核实')
    expect(wrapper.get('[data-deletion-confirm]').attributes('disabled')).toBeUndefined()
    expect(sessionStorage.getItem(sessionStorage.key(0)!)).toContain(operationId)
  })

  it('only retries admission when registration was not found and deletion has never been sent', async () => {
    mocks.api.registerOperation.mockRejectedValueOnce(new Error('lost admission response')).mockResolvedValueOnce(pendingOperation)
    mocks.api.operation.mockRejectedValue(new ApiError(404, { error: { code: 'NOT_FOUND' } }))
    const wrapper = mount(DeletionDialog, { props: { courseId: 7, target: { kind: 'experiment', experimentId: 13 } } })
    await flushPromises()
    await wrapper.get('[data-deletion-confirm]').trigger('click')
    await flushPromises()
    expect(mocks.api.deleteExperiment).not.toHaveBeenCalled()
    await wrapper.get('[data-operation-register]').trigger('click')
    await flushPromises()
    expect(mocks.api.registerOperation).toHaveBeenCalledTimes(2)
    expect(mocks.api.registerOperation.mock.calls[0]?.[1]).toEqual(mocks.api.registerOperation.mock.calls[1]?.[1])
    expect(mocks.api.deleteExperiment).toHaveBeenCalledTimes(1)
    expect(wrapper.emitted('completed')).toHaveLength(1)
  })

  it('resumes the single deletion after a lost admission response when the receipt proves registration', async () => {
    mocks.api.registerOperation.mockRejectedValueOnce(new Error('lost admission response'))
    mocks.api.operation.mockResolvedValue(pendingOperation)
    const wrapper = mount(DeletionDialog, { props: { courseId: 7, target: { kind: 'experiment', experimentId: 13 } } })
    await flushPromises()
    await wrapper.get('[data-deletion-confirm]').trigger('click')
    await flushPromises()
    expect(mocks.api.registerOperation).toHaveBeenCalledTimes(1)
    expect(mocks.api.operation).toHaveBeenCalledTimes(1)
    expect(mocks.api.deleteExperiment).toHaveBeenCalledTimes(1)
    expect(wrapper.emitted('completed')).toHaveLength(1)
  })

  it('bounds file cleanup checks at three and allows a later manual read', async () => {
    vi.useFakeTimers()
    mocks.api.deleteExperiment.mockResolvedValue({ ...deletionResult, cleanup_pending: true, cleanup_id: 'cleanup-1' })
    mocks.api.cleanup.mockResolvedValue({ state: 'running', status: 'pending', cleanup_pending: true, error: '' })
    const wrapper = mount(DeletionDialog, { props: { courseId: 7, target: { kind: 'experiment', experimentId: 13 } } })
    await flushPromises()
    await wrapper.get('[data-deletion-confirm]').trigger('click')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(30000)
    expect(mocks.api.cleanup).toHaveBeenCalledTimes(3)
    expect(vi.getTimerCount()).toBe(0)
    expect(wrapper.text()).toContain('数据已删除，文件清理中')
    await wrapper.get('[data-cleanup-refresh]').trigger('click')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(30000)
    expect(mocks.api.cleanup).toHaveBeenCalledTimes(4)
    expect(mocks.api.deleteExperiment).toHaveBeenCalledTimes(1)
  })

  it('cancels a status read when hidden and ignores a late success without restarting on foreground', async () => {
    vi.useFakeTimers()
    let hidden = false
    vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => hidden ? 'hidden' : 'visible')
    mocks.api.deleteExperiment.mockRejectedValue(new Error('lost response'))
    let resolve!: (value: unknown) => void
    mocks.api.operation.mockReturnValue(new Promise(done => { resolve = done }))
    const wrapper = mount(DeletionDialog, { props: { courseId: 7, target: { kind: 'experiment', experimentId: 13 } } })
    await flushPromises()
    await wrapper.get('[data-deletion-confirm]').trigger('click')
    await flushPromises()
    const signal = mocks.api.operation.mock.calls[0]?.[2] as AbortSignal
    hidden = true
    document.dispatchEvent(new Event('visibilitychange'))
    expect(signal.aborted).toBe(true)
    hidden = false
    document.dispatchEvent(new Event('visibilitychange'))
    resolve({ ...pendingOperation, state: 'succeeded', result: deletionResult })
    await flushPromises()
    await vi.advanceTimersByTimeAsync(30000)
    expect(wrapper.emitted('completed')).toBeUndefined()
    expect(mocks.api.operation).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('ignores stale preview and operation responses after the target changes', async () => {
    let resolvePreview!: (value: typeof experimentPreview) => void
    mocks.api.previewExperiment.mockReturnValueOnce(new Promise(done => { resolvePreview = done }))
      .mockResolvedValueOnce({ ...experimentPreview, object_id: 14, object_name: '另一个实验' })
    const wrapper = mount(DeletionDialog, { props: { courseId: 7, target: { kind: 'experiment', experimentId: 13 } } })
    await flushPromises()
    const signal = mocks.api.previewExperiment.mock.calls[0]?.[2] as AbortSignal
    await wrapper.setProps({ target: { kind: 'experiment', experimentId: 14 } })
    await flushPromises()
    resolvePreview(experimentPreview)
    await flushPromises()
    expect(signal.aborted).toBe(true)
    expect(wrapper.get('h2').text()).toContain('另一个实验')
    mocks.api.deleteExperiment.mockRejectedValue(new Error('lost response'))
    let resolveOperation!: (value: unknown) => void
    mocks.api.operation.mockReturnValueOnce(new Promise(done => { resolveOperation = done }))
    await wrapper.get('[data-deletion-confirm]').trigger('click')
    await flushPromises()
    await wrapper.setProps({ target: { kind: 'experiment', experimentId: 13 } })
    await flushPromises()
    resolveOperation({ ...pendingOperation, state: 'succeeded', result: deletionResult })
    await flushPromises()
    expect(wrapper.emitted('completed')).toBeUndefined()
    expect(wrapper.get('h2').text()).toContain('认知负荷实验')
  })

  it('shows whole-run Coverage invalidation before confirmation', async () => {
    mocks.api.previewExperiment.mockResolvedValue({ ...experimentPreview, counts: { ...counts, coverage_run_count: 1, coverage_session_count: 12 } })
    const wrapper = mount(DeletionDialog, { props: { courseId: 7, target: { kind: 'experiment', experimentId: 13 } } })
    await flushPromises()
    expect(wrapper.text()).toContain('整体失效的 Coverage 运行1')
    expect(wrapper.text()).toContain('涉及 Coverage 会话12')
  })

  it.each(['unknown', 'cleanup-failed'])('pauses a confirmed batch when the current operation is %s', async (failure) => {
    const second = { ...experimentPreview.experiments[0], id: 14, name: '第二个实验' }
    mocks.api.previewClass.mockResolvedValue({ ...experimentPreview, action: 'delete_class', object_id: 11,
      allowed: false, object_name: '实验一班', experiments: [experimentPreview.experiments[0], second] })
    if (failure === 'unknown') mocks.api.deleteExperiment.mockRejectedValueOnce(new Error('lost response'))
    else {
      mocks.api.deleteExperiment.mockResolvedValueOnce({ ...deletionResult, cleanup_pending: true, cleanup_id: 'cleanup-1' })
      mocks.api.cleanup.mockResolvedValueOnce({ state: 'failed', cleanup_pending: true, status: 'pending', error: 'cleanup failed' })
    }
    const wrapper = mount(DeletionDialog, { props: { courseId: 7, target: { kind: 'class', classId: 11 } } })
    await flushPromises()
    await wrapper.get('[data-remove-all-class-experiments]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-deletion-confirm]').trigger('click')
    await flushPromises()
    expect(mocks.api.previewExperiment).toHaveBeenCalledTimes(1)
    expect(mocks.api.deleteExperiment).toHaveBeenCalledTimes(1)
    expect(wrapper.emitted('batch-completed')).toBeUndefined()
  })

  it('ignores an operation response after unmount', async () => {
    mocks.api.deleteExperiment.mockRejectedValueOnce(new Error('lost response'))
    let resolve!: (value: unknown) => void
    mocks.api.operation.mockReturnValueOnce(new Promise(done => { resolve = done }))
    const wrapper = mount(DeletionDialog, { props: { courseId: 7, target: { kind: 'experiment', experimentId: 13 } } })
    await flushPromises()
    await wrapper.get('[data-deletion-confirm]').trigger('click')
    await flushPromises()
    const signal = mocks.api.operation.mock.calls[0]?.[2] as AbortSignal
    wrapper.unmount()
    resolve({ ...pendingOperation, state: 'succeeded', result: deletionResult })
    await flushPromises()
    expect(signal.aborted).toBe(true)
    expect(wrapper.emitted('completed')).toBeUndefined()
    expect(sessionStorage.length).toBe(1)
  })

  it('shows every affected class and count, then cancels without writing', async () => {
    const wrapper = mount(DeletionDialog, {
      props: { courseId: 7, target: { kind: 'experiment', experimentId: 13 } },
    })
    await flushPromises()

    const dialog = wrapper.get('[role="dialog"]')
    expect(dialog.attributes('open')).toBeDefined()
    expect(dialog.attributes('aria-labelledby')).toBe('teacher-deletion-title')
    expect(dialog.text()).toContain('认知负荷实验')
    expect(dialog.text()).toContain('所有班级')
    expect(dialog.text()).toContain('其他实验的原始数据保留')
    expect(dialog.text()).toContain('实验一班')
    expect(dialog.text()).toContain('5 次会话')
    expect(dialog.text()).toContain('实验二班')
    expect(dialog.text()).toContain('3 次会话')
    expect(dialog.text()).toContain('3 个导出记录')
    expect(dialog.text()).not.toContain('班级成员')

    await wrapper.get('[data-deletion-cancel]').trigger('click')
    expect(mocks.api.deleteExperiment).not.toHaveBeenCalled()
    expect(wrapper.emitted('cancelled')).toHaveLength(1)
  })

  it('prevents confirmation when the server omits the confirmation token', async () => {
    mocks.api.previewExperiment.mockResolvedValue({ ...experimentPreview, confirmation_token: '' })
    const wrapper = mount(DeletionDialog, {
      props: { courseId: 7, target: { kind: 'experiment', experimentId: 13 } },
    })
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('确认令牌')
    expect(wrapper.get('[data-deletion-confirm]').attributes('disabled')).toBeDefined()
    await wrapper.get('[data-deletion-confirm]').trigger('click')
    expect(mocks.api.deleteExperiment).not.toHaveBeenCalled()
  })

  it('disables duplicate confirmation clicks while the request is in flight', async () => {
    let resolveDelete!: (value: typeof deletionResult) => void
    mocks.api.deleteExperiment.mockReturnValue(new Promise(resolve => { resolveDelete = resolve }))
    const wrapper = mount(DeletionDialog, {
      props: { courseId: 7, target: { kind: 'experiment', experimentId: 13 } },
    })
    await flushPromises()

    const confirm = wrapper.get('[data-deletion-confirm]')
    void confirm.trigger('click')
    await confirm.trigger('click')
    expect(mocks.api.deleteExperiment).toHaveBeenCalledTimes(1)
    expect(confirm.attributes('disabled')).toBeDefined()

    resolveDelete(deletionResult)
    await flushPromises()
    expect(wrapper.emitted('completed')).toHaveLength(1)
  })

  it('requires a refreshed preview after a stale confirmation conflict', async () => {
    mocks.api.deleteExperiment.mockRejectedValueOnce(new ApiError(409, {
      error: { code: 'STALE_CONFIRMATION', message: '确认状态已过期，请重试。' },
    }))
    mocks.api.operation.mockResolvedValue({ ...pendingOperation, state: 'rejected', error_code: 'STALE_CONFIRMATION', message: '确认状态已过期，请重试。' })
    const wrapper = mount(DeletionDialog, {
      props: { courseId: 7, target: { kind: 'experiment', experimentId: 13 } },
    })
    await flushPromises()

    await wrapper.get('[data-deletion-confirm]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('删除预览已过期')
    expect(wrapper.get('[data-deletion-confirm]').attributes('disabled')).toBeDefined()
    await wrapper.get('[data-deletion-refresh]').trigger('click')
    await flushPromises()
    expect(mocks.api.previewExperiment).toHaveBeenCalledTimes(2)
    expect(wrapper.get('[data-deletion-confirm]').attributes('disabled')).toBeUndefined()
  })

  it('waits for server cleanup completion before reporting success', async () => {
    mocks.api.deleteExperiment.mockResolvedValue({ ...deletionResult, cleanup_pending: true, cleanup_id: 'cleanup-1' })
    mocks.api.cleanup.mockResolvedValue({ cleanup_pending: false, status: 'completed', state: 'completed', error: '' })
    const wrapper = mount(DeletionDialog, {
      props: { courseId: 7, target: { kind: 'experiment', experimentId: 13 } },
    })
    await flushPromises()

    await wrapper.get('[data-deletion-confirm]').trigger('click')
    await flushPromises()
    expect(mocks.api.cleanup).toHaveBeenCalledWith(7, 'cleanup-1', expect.any(AbortSignal))
    expect(wrapper.emitted('completed')).toHaveLength(1)
  })

  it('retries cleanup status without resubmitting an accepted deletion', async () => {
    mocks.api.deleteExperiment.mockResolvedValue({ ...deletionResult, cleanup_pending: true, cleanup_id: 'cleanup-1' })
    mocks.api.cleanup.mockRejectedValueOnce(new Error('状态服务暂时不可用'))
      .mockResolvedValueOnce({ cleanup_pending: false, status: 'completed', state: 'completed', error: '' })
    const wrapper = mount(DeletionDialog, {
      props: { courseId: 7, target: { kind: 'experiment', experimentId: 13 } },
    })
    await flushPromises()

    await wrapper.get('[data-deletion-confirm]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('状态服务暂时不可用')
    expect(wrapper.get('[data-deletion-confirm]').attributes('disabled')).toBeDefined()
    await wrapper.get('[data-cleanup-refresh]').trigger('click')
    await flushPromises()

    expect(mocks.api.deleteExperiment).toHaveBeenCalledTimes(1)
    expect(mocks.api.cleanup).toHaveBeenCalledTimes(2)
    expect(wrapper.emitted('completed')).toHaveLength(1)
  })

  it('shows the exact blocked class reason and offers per-experiment choices', async () => {
    mocks.api.previewClass.mockResolvedValue({
      action: 'delete_class', object_id: 11, object_name: '实验一班', allowed: false,
      blocked_reason: '该班级仍有关联实验及实验数据，请先删除相关实验数据后再删除班级。',
      experiments: experimentPreview.experiments,
      counts: { ...counts, member_count: 32 }, confirmation_token: 'class-token',
    })
    const wrapper = mount(DeletionDialog, {
      props: { courseId: 7, target: { kind: 'class', classId: 11 } },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('该班级仍有关联实验及实验数据，请先删除相关实验数据后再删除班级。')
    expect(wrapper.text()).toContain('认知负荷实验')
    expect(wrapper.text()).toContain('当前班级 0 次会话')
    expect(wrapper.text()).toContain('班级成员32')
    expect(wrapper.get('a[href="/teacher/courses/7/experiments/13/"]').text()).toContain('查看实验')
    expect(wrapper.find('[data-remove-class-experiment="13"]').exists()).toBe(true)
    expect(wrapper.find('[data-delete-global-experiment="13"]').exists()).toBe(true)
    expect(wrapper.get('[data-deletion-confirm]').attributes('disabled')).toBeDefined()
    expect(mocks.api.deleteClass).not.toHaveBeenCalled()
  })

  it('globally deletes all class experiments through separate sequential confirmations and leaves the class intact', async () => {
    const secondExperiment = {
      ...experimentPreview.experiments[0], id: 14, name: '延迟反馈实验', detail_url: '/teacher/courses/7/experiments/14/',
    }
    mocks.api.previewClass.mockResolvedValue({
      action: 'delete_class', object_id: 11, object_name: '实验一班', allowed: false,
      blocked_reason: '该班级仍有关联实验及实验数据，请先删除相关实验数据后再删除班级。',
      experiments: [experimentPreview.experiments[0], secondExperiment], counts,
      confirmation_token: 'class-token',
    })
    mocks.api.previewExperiment
      .mockResolvedValueOnce({ ...experimentPreview, confirmation_token: 'delete-13', experiments: [experimentPreview.experiments[0]] })
      .mockResolvedValueOnce({ ...experimentPreview, object_id: 14, object_name: '延迟反馈实验', confirmation_token: 'delete-14', experiments: [secondExperiment] })
    mocks.api.deleteExperiment.mockResolvedValue(deletionResult)

    const wrapper = mount(DeletionDialog, {
      props: { courseId: 7, target: { kind: 'class', classId: 11 } },
    })
    await flushPromises()
    await wrapper.get('[data-remove-all-class-experiments]').trigger('click')
    await flushPromises()
    expect(wrapper.get('#teacher-deletion-title').text()).toContain('认知负荷实验')
    expect(wrapper.text()).toContain('所有班级')
    await wrapper.get('[data-deletion-confirm]').trigger('click')
    await flushPromises()
    expect(wrapper.get('#teacher-deletion-title').text()).toContain('延迟反馈实验')
    await wrapper.get('[data-deletion-confirm]').trigger('click')
    await flushPromises()

    expect(mocks.api.deleteExperiment).toHaveBeenNthCalledWith(1, 7, 13, 'delete-13', operationId)
    expect(mocks.api.deleteExperiment).toHaveBeenNthCalledWith(2, 7, 14, 'delete-14', operationId)
    expect(mocks.api.removeClassExperiment).not.toHaveBeenCalled()
    expect(mocks.api.deleteClass).not.toHaveBeenCalled()
    expect(wrapper.emitted('batch-completed')).toHaveLength(1)
  })
})
