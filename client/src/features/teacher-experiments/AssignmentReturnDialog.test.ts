import { flushPromises, mount } from '@vue/test-utils'
import { beforeAll, beforeEach, expect, it, vi } from 'vitest'
import AssignmentReturnDialog from './AssignmentReturnDialog.vue'

const api = vi.hoisted(() => ({ collectionParticipants: vi.fn(), previewReturn: vi.fn(), returnAssignment: vi.fn() }))
vi.mock('./api', () => ({ getTeacherExperimentApi: () => api }))
beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
})
const props = { experimentId: 11, task: { id: 31, class_id: 5, class_name: '实验班' } }
beforeEach(() => {
  vi.resetAllMocks()
  api.collectionParticipants.mockResolvedValue({ items: [{ participant_id: 101, student_number: 'S019', display_name: '李小满', status_label: '已超时' }],
    status_choices: [{ value: 'not_started', label: '未开始' }, { value: 'timed_out', label: '已超时' }],
    page: { number: 1, total_items: 1, total_pages: 1 } })
  api.previewReturn.mockResolvedValue({ participant_id: 101, student_number: 'S019', display_name: '李小满', class_name: '实验班',
    question_count: 40, submission_count: 17, can_return: true, blocked_reason: '', confirmation_token: 'frozen-scope' })
  api.returnAssignment.mockResolvedValue({ participant_id: 101, session_id: 200, status: 'not_started' })
})

it('searches only the selected class and requires explicit confirmation before returning one student', async () => {
  const wrapper = mount(AssignmentReturnDialog, { props })
  await flushPromises()
  await wrapper.get('[data-return-search] input').setValue('S019')
  await wrapper.get('[data-return-search]').trigger('submit')
  await flushPromises()
  expect(api.collectionParticipants).toHaveBeenLastCalledWith(11, { classId: 5, query: 'S019', status: '', page: 1 })
  await wrapper.get('[data-return-select="101"]').trigger('click')
  await flushPromises()
  expect(api.previewReturn).toHaveBeenCalledWith(31, 101)
  expect(wrapper.text()).toContain('清除作答、提交及评分记录')
  expect(api.returnAssignment).not.toHaveBeenCalled()
  await wrapper.get('[data-return-confirm]').trigger('click')
  await flushPromises()
  expect(api.returnAssignment).toHaveBeenCalledWith(31, 101, 'frozen-scope')
  expect(wrapper.emitted('returned')).toHaveLength(1)
  expect(wrapper.text()).toContain('已恢复为未开始')
  wrapper.unmount()
})

it('combines server-provided status filters with student search and resets pagination', async () => {
  const wrapper = mount(AssignmentReturnDialog, { props })
  await flushPromises()
  const filter = wrapper.get('[data-return-status]')
  expect(filter.text()).toContain('全部状态')
  expect(filter.text()).toContain('已超时')
  await wrapper.get('[data-return-search] input').setValue('S019')
  await filter.setValue('timed_out')
  await flushPromises()
  expect(api.collectionParticipants).toHaveBeenLastCalledWith(11, { classId: 5, query: 'S019', status: 'timed_out', page: 1 })
  await wrapper.get('[data-return-select="101"]').trigger('click')
  await flushPromises()
  await wrapper.get('[data-return-confirm]').trigger('click')
  await flushPromises()
  expect(api.collectionParticipants).toHaveBeenLastCalledWith(11, { classId: 5, query: 'S019', status: 'timed_out', page: 1 })
  await wrapper.get('[data-return-status]').setValue('')
  await flushPromises()
  expect(api.collectionParticipants).toHaveBeenLastCalledWith(11, { classId: 5, query: 'S019', status: '', page: 1 })
  wrapper.unmount()
})

it('shows server refusal and does not offer a reset for a closed task', async () => {
  api.previewReturn.mockResolvedValue({ participant_id: 101, display_name: '李小满', can_return: false,
    confirmation_token: null, blocked_reason: '该班级作业当前未开放，请先调整排期后再打回。' })
  const wrapper = mount(AssignmentReturnDialog, { props })
  await flushPromises()
  await wrapper.get('[data-return-select="101"]').trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('请先调整排期')
  expect(wrapper.find('[data-return-confirm]').exists()).toBe(false)
  wrapper.unmount()
})

it('keeps failed confirmation visible and blocks duplicate clicks while sending', async () => {
  let reject!: (error: Error) => void
  api.returnAssignment.mockReturnValue(new Promise((_resolve, fail) => { reject = fail }))
  const wrapper = mount(AssignmentReturnDialog, { props })
  await flushPromises()
  await wrapper.get('[data-return-select="101"]').trigger('click')
  await flushPromises()
  await wrapper.get('[data-return-confirm]').trigger('click')
  expect(wrapper.get('[data-return-confirm]').attributes('disabled')).toBeDefined()
  reject(new Error('作答已变化，请重新核对'))
  await flushPromises()
  expect(wrapper.get('[role="alert"]').text()).toContain('作答已变化')
  expect(wrapper.emitted('returned')).toBeUndefined()
  wrapper.unmount()
})
