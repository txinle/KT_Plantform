import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/client'

const mocks = vi.hoisted(() => ({
  api: {
    previewExperiment: vi.fn(), deleteExperiment: vi.fn(),
    previewClassExperiment: vi.fn(), removeClassExperiment: vi.fn(),
    previewClass: vi.fn(), deleteClass: vi.fn(), cleanup: vi.fn(),
  },
}))

vi.mock('./api', () => ({ getTeacherDeletionApi: () => mocks.api }))

import DeletionDialog from './DeletionDialog.vue'

enableAutoUnmount(afterEach)

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
    mocks.api.previewExperiment.mockResolvedValue(experimentPreview)
    mocks.api.deleteExperiment.mockResolvedValue({ deleted: true, cleanup_pending: false, cleanup_id: null })
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
    let resolveDelete!: (value: { deleted: boolean; cleanup_pending: boolean; cleanup_id: null }) => void
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

    resolveDelete({ deleted: true, cleanup_pending: false, cleanup_id: null })
    await flushPromises()
    expect(wrapper.emitted('completed')).toHaveLength(1)
  })

  it('requires a refreshed preview after a stale confirmation conflict', async () => {
    mocks.api.deleteExperiment.mockRejectedValueOnce(new ApiError(409, {
      error: { code: 'STALE_CONFIRMATION', message: '确认状态已过期，请重试。' },
    }))
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
    mocks.api.deleteExperiment.mockResolvedValue({ deleted: true, cleanup_pending: true, cleanup_id: 'cleanup-1' })
    mocks.api.cleanup.mockResolvedValue({ cleanup_pending: false, status: 'completed', error: '' })
    const wrapper = mount(DeletionDialog, {
      props: { courseId: 7, target: { kind: 'experiment', experimentId: 13 } },
    })
    await flushPromises()

    await wrapper.get('[data-deletion-confirm]').trigger('click')
    await flushPromises()
    expect(mocks.api.cleanup).toHaveBeenCalledWith(7, 'cleanup-1')
    expect(wrapper.emitted('completed')).toHaveLength(1)
  })

  it('retries cleanup status without resubmitting an accepted deletion', async () => {
    mocks.api.deleteExperiment.mockResolvedValue({ deleted: true, cleanup_pending: true, cleanup_id: 'cleanup-1' })
    mocks.api.cleanup.mockRejectedValueOnce(new Error('状态服务暂时不可用'))
      .mockResolvedValueOnce({ cleanup_pending: false, status: 'completed', error: '' })
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
    mocks.api.deleteExperiment.mockResolvedValue({ deleted: true, cleanup_pending: false, cleanup_id: null })

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

    expect(mocks.api.deleteExperiment).toHaveBeenNthCalledWith(1, 7, 13, 'delete-13')
    expect(mocks.api.deleteExperiment).toHaveBeenNthCalledWith(2, 7, 14, 'delete-14')
    expect(mocks.api.removeClassExperiment).not.toHaveBeenCalled()
    expect(mocks.api.deleteClass).not.toHaveBeenCalled()
    expect(wrapper.emitted('batch-completed')).toHaveLength(1)
  })
})
