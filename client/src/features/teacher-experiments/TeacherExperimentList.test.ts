import { flushPromises, mount } from '@vue/test-utils'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const route = { params: { courseId: '7' }, query: { page: '2' } as Record<string, string> }
const mocks = vi.hoisted(() => ({
  operationId: '',
  api: { list: vi.fn(), archive: vi.fn(), restore: vi.fn(), startPlan: vi.fn(), classTasks: vi.fn(), progress: vi.fn(), reviseSchedule: vi.fn(), cancel: vi.fn(), manualClose: vi.fn(), releaseFeedback: vi.fn() },
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

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
})

describe('TeacherExperimentList deletion', () => {
  it('renders one compact card per experiment with accessible filters and authorized actions', async () => {
    mocks.api.list.mockResolvedValue(collection([{ ...experiment, description: 'Do not expand the card with this description', activity: { pending_grading_count: 8, grading_url: '/grade/', schedules: [] } }]))
    const wrapper = mount(TeacherExperimentList, { global: { stubs: { Teleport: true } } })
    await flushPromises()
    expect(wrapper.get('select[data-show-archived]').attributes('aria-label')).toBe('按归档状态筛选')
    expect(wrapper.findAll('.kt-experiment-card')).toHaveLength(1)
    const card = wrapper.get('.kt-experiment-card')
    expect(card.get('h2 a').attributes('title')).toBe(experiment.name)
    expect(card.text()).toContain('0 个班级')
    expect(card.text()).not.toContain('Do not expand the card')
    expect(card.get('[data-pending-grading]').attributes('href')).toBe('/grade/')
    expect(wrapper.find('table').exists()).toBe(false)
    wrapper.unmount()
  })

  it('opens class operations from a card without navigating and opens the real schedule editor', async () => {
    mocks.api.list.mockResolvedValue(collection([{ ...experiment, activity: { schedules: [{ task_id: 25, class_name: '一班', opens_at: null, closes_at: null }] } }]))
    mocks.api.classTasks.mockResolvedValue({ items: [{ id: 25, class_id: 5, class_name: '一班', participant_count: 3, opens_at: null, closes_at: null, business_status: 'open', schedule_revision: 2, total_time_limit_minutes: 15, available_actions: ['adjust_time'] }] })
    mocks.api.progress.mockResolvedValue({ total_participants: 3, not_started: 2, in_progress: 1, completed: 0 })
    const wrapper = mount(TeacherExperimentList, { global: { stubs: { Teleport: true } } })
    await flushPromises()
    await wrapper.get('[data-class-schedules="13"]').trigger('click')
    await flushPromises()
    expect(mocks.push).not.toHaveBeenCalled()
    expect(mocks.api.classTasks).toHaveBeenCalledWith(13)
    const panel = wrapper.get('[data-inline-schedules="13"]')
    expect(panel.text()).toContain('一班')
    expect(panel.text()).toContain('开放中')
    await panel.get('[data-action="adjust-time"]').trigger('click')
    await flushPromises()
    expect(panel.get('dialog[open]').text()).toContain('学生作答时间延长')
    expect(mocks.api.reviseSchedule).not.toHaveBeenCalled()
    await panel.get('dialog[open]').trigger('cancel')
    await flushPromises()
    expect(panel.find('dialog[open]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('opens the saved publication plan from the server-authorized row action', async () => {
    mocks.api.list.mockResolvedValue(collection([{ ...experiment, can_publish: true }]))
    mocks.api.startPlan.mockResolvedValue({ id: 23, current_step: 1 })
    const wrapper = mount(TeacherExperimentList, { global: { stubs: { Teleport: true } } })
    await flushPromises()
    await wrapper.get('[data-publish-experiment="13"]').trigger('click')
    await flushPromises()
    expect(mocks.api.startPlan).toHaveBeenCalledWith(7, 13)
    expect(mocks.push).toHaveBeenCalledWith('/teacher/courses/7/experiments/13/publish-plan/23/step/1/')
  })
  it('previews server search results without replacing the main list or pagination', async () => {
    const wrapper = mount(TeacherExperimentList, { global: { stubs: { Teleport: true } } })
    await flushPromises()
    mocks.api.list.mockResolvedValueOnce(collection([{ ...experiment, id: 14, name: '期中实验' }], 1, 1))
    await wrapper.get('input[aria-label="搜索实验名称"]').setValue('期中')
    await wrapper.get('form[role="search"]').trigger('submit')
    await flushPromises()
    expect(mocks.api.list).toHaveBeenLastCalledWith(7, 1, false, '期中')
    expect(mocks.push).not.toHaveBeenCalled()
    expect(wrapper.get('[role="listbox"]').text()).toContain('期中实验')
    expect(wrapper.get('[aria-label="实验列表"]').text()).toContain('待删除实验')
    expect(wrapper.get('[aria-label="实验列表"]').text()).not.toContain('期中实验')
    await wrapper.get('[aria-label="清除搜索"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[role="listbox"]').exists()).toBe(false)
    expect(wrapper.get('[aria-label="实验列表"]').text()).toContain('待删除实验')
    expect(mocks.api.list).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })
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
    const wrapper = mount(TeacherExperimentList, { global: { stubs: { Teleport: true } } })
    await flushPromises()

    expect(wrapper.get('.kt-experiment-card h2').text()).toBe(experiment.name)
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

  it('summarizes class schedules as a single class-count button', async () => {
    mocks.api.list.mockResolvedValue(collection([{ ...experiment, activity: {
      pending_grading_count: 8, grading_url: '/grade/', schedules: [
        { task_id: 31, class_name: '一班', opens_at: null, closes_at: null },
        { task_id: 32, class_name: '二班', opens_at: null, closes_at: null },
      ],
    } }]))
    const wrapper = mount(TeacherExperimentList, { global: { stubs: { Teleport: true } } })
    await flushPromises()
    expect(wrapper.get('[data-class-schedules]').text()).toBe('2 个班级')
    expect(wrapper.get('[data-class-schedules]').attributes('aria-label')).toContain('查看 2 个班级')
    expect(wrapper.find('[data-inline-schedules]').exists()).toBe(false)
    expect(wrapper.findAll('[data-pending-grading]')).toHaveLength(1)
    wrapper.unmount()
  })

  it('keeps the grading menu entry clickable when the authorized pending count is zero', async () => {
    const gradingUrl = '/teacher/courses/7/experiments/13/class-tasks/31/grading/'
    mocks.api.list.mockResolvedValue(collection([{ ...experiment, activity: {
      pending_grading_count: 0, grading_url: gradingUrl, schedules: [],
    } }]))
    const wrapper = mount(TeacherExperimentList, { global: { stubs: { Teleport: true } } })
    await flushPromises()
    const pending = wrapper.get('a[data-pending-grading]')
    expect(pending.text()).toBe('人工批改（待批改：0）')
    expect(pending.attributes('href')).toBe(gradingUrl)
    expect(wrapper.findAll('a[data-pending-grading]')).toHaveLength(1)
    wrapper.unmount()
  })

  it('does not invent a grading link when the server does not authorize one', async () => {
    mocks.api.list.mockResolvedValue(collection([{ ...experiment, activity: {
      pending_grading_count: 0, grading_url: null, schedules: [],
    } }]))
    const wrapper = mount(TeacherExperimentList, { global: { stubs: { Teleport: true } } })
    await flushPromises()
    expect(wrapper.find('[data-pending-grading]').exists()).toBe(false)
    expect(wrapper.findAll('a').some(link => link.text() === '人工批改' || link.text().startsWith('待批改：'))).toBe(false)
    expect(wrapper.find('.kt-experiment-pending').exists()).toBe(false)
    wrapper.unmount()
  })

  it('offers the server-authorized student simulation from the shared card menu', async () => {
    const url = '/teacher/courses/7/experiments/13/student-preview/'
    mocks.api.list.mockResolvedValue(collection([{ ...experiment, student_preview_url: url }]))
    const wrapper = mount(TeacherExperimentList, { global: { stubs: { Teleport: true } } })
    await flushPromises()
    const links = wrapper.findAll('a').filter(link => link.text() === '模拟学生作答')
    expect(links).toHaveLength(1)
    expect(links.every(link => link.attributes('href') === url)).toBe(true)
    expect(wrapper.findAll('#experiment-menu-13 a').filter(link => link.text() === '模拟学生作答')).toHaveLength(1)
    wrapper.unmount()
  })

  it('archives with confirmation while preserving access to the archived list', async () => {
    mocks.api.list.mockResolvedValueOnce(collection([{ ...experiment, can_archive: true }]))
      .mockResolvedValueOnce(collection([]))
    mocks.api.archive.mockResolvedValue({ ...experiment, status: 'archived', can_archive: false })
    const wrapper = mount(TeacherExperimentList, { global: { stubs: { Teleport: true } } })
    await flushPromises()
    await wrapper.findAll('[data-archive-experiment="13"]')[0]!.trigger('click')
    await flushPromises()
    expect(wrapper.get('dialog[aria-label="归档实验"]').text()).toContain('保留')
    expect(mocks.api.archive).not.toHaveBeenCalled()
    await wrapper.get('[data-archive-confirm]').trigger('click')
    await flushPromises()
    expect(mocks.api.archive).toHaveBeenCalledWith(7, 13)
    expect(mocks.deletion.deleteExperiment).not.toHaveBeenCalled()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    await wrapper.get('[data-show-archived]').setValue('archived')
    await flushPromises()
    expect(mocks.push).toHaveBeenLastCalledWith({ query: { archived: '1' } })
    expect(mocks.api.list).toHaveBeenLastCalledWith(7, 1, true)
  })

  it('does not offer archiving without the server capability and keeps archive failures visible', async () => {
    const wrapper = mount(TeacherExperimentList, { global: { stubs: { Teleport: true } } })
    await flushPromises()
    expect(wrapper.find('[data-archive-experiment]').exists()).toBe(false)
    wrapper.unmount()
    mocks.api.list.mockResolvedValue(collection([{ ...experiment, can_archive: true }]))
    mocks.api.archive.mockRejectedValue(new Error('归档失败，请重试'))
    const authorized = mount(TeacherExperimentList, { global: { stubs: { Teleport: true } } })
    await flushPromises()
    await authorized.findAll('[data-archive-experiment="13"]')[0]!.trigger('click')
    await authorized.get('[data-archive-confirm]').trigger('click')
    await flushPromises()
    expect(authorized.get('[role="alert"]').text()).toContain('归档失败')
    expect(authorized.find('[role="dialog"]').exists()).toBe(true)
  })

  it('returns to the first page after archiving the last experiment on a later page', async () => {
    mocks.api.list.mockResolvedValueOnce(collection([{ ...experiment, can_archive: true }]))
      .mockImplementation(async (_course, page) => {
        if (page !== 1) throw new Error('分页不存在')
        return collection([], 1, 1)
      })
    mocks.api.archive.mockResolvedValue({ ...experiment, status: 'archived' })
    const wrapper = mount(TeacherExperimentList, { global: { stubs: { Teleport: true } } })
    await flushPromises()
    await wrapper.findAll('[data-archive-experiment="13"]')[0]!.trigger('click')
    await wrapper.get('[data-archive-confirm]').trigger('click')
    await flushPromises()
    expect(mocks.api.list).toHaveBeenLastCalledWith(7, 1)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  })

  it('restores archived experiments through the server and returns to the first archived page', async () => {
    route.query = { archived: '1', page: '2' }
    mocks.api.list.mockResolvedValueOnce(collection([{ ...experiment, status: 'archived', can_restore: true }]))
      .mockResolvedValueOnce(collection([], 1, 1))
    mocks.api.restore.mockResolvedValue({ ...experiment, status: 'draft' })
    const wrapper = mount(TeacherExperimentList, { global: { stubs: { Teleport: true } } })
    await flushPromises()
    expect(wrapper.findAll('[data-restore-experiment="13"]')).toHaveLength(1)
    await wrapper.findAll('[data-restore-experiment="13"]')[0]!.trigger('click')
    await flushPromises()
    expect(mocks.api.restore).toHaveBeenCalledWith(7, 13)
    expect(mocks.push).toHaveBeenCalledWith({ query: { archived: '1' } })
    expect(mocks.api.list).toHaveBeenLastCalledWith(7, 1, true)
    expect(mocks.deletion.deleteExperiment).not.toHaveBeenCalled()
  })

  it('keeps restoration failures visible and does not offer restoration without permission', async () => {
    const wrapper = mount(TeacherExperimentList, { global: { stubs: { Teleport: true } } })
    await flushPromises()
    expect(wrapper.find('[data-restore-experiment]').exists()).toBe(false)
    wrapper.unmount()
    route.query = { archived: '1' }
    mocks.api.list.mockResolvedValue(collection([{ ...experiment, status: 'archived', can_restore: true }]))
    mocks.api.restore.mockRejectedValue(new Error('恢复失败，请重试'))
    const archived = mount(TeacherExperimentList, { global: { stubs: { Teleport: true } } })
    await flushPromises()
    await archived.findAll('[data-restore-experiment="13"]')[0]!.trigger('click')
    await flushPromises()
    expect(archived.get('[role="alert"]').text()).toContain('恢复失败')
    expect(mocks.api.list).toHaveBeenLastCalledWith(7, 1, true)
  })
})
