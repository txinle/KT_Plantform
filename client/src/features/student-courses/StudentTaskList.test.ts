import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ref, shallowRef } from 'vue'
import { studentTaskContext } from './task-context'

const mocks = vi.hoisted(() => ({
  api: { tasks: vi.fn(), start: vi.fn(), resume: vi.fn() },
  push: vi.fn(), primeStart: vi.fn(), warm: vi.fn().mockResolvedValue(undefined),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { courseId: '7' } }),
  useRouter: () => ({ push: mocks.push }),
}))
vi.mock('./api', () => ({ getStudentCourseApi: () => mocks.api }))
vi.mock('@/assessment-runtime/offline-cache', () => ({ prepareOfflineCommonResources: mocks.warm }))

vi.mock('@/assessment-runtime/session-controller', () => ({ getSessionController: async () => ({ primeStart: mocks.primeStart }) }))
vi.mock('@/app/bootstrap', () => ({ getBootstrapContext: () => ({ csrf_token: 'csrf' }) }))
import StudentTaskList from './StudentTaskList.vue'

async function mountTaskList() {
  return mount(StudentTaskList, { global: { provide: {
    [studentTaskContext as symbol]: { response: shallowRef(await mocks.api.tasks()), loading: ref(false), reload: vi.fn() },
  } } })
}

describe('StudentTaskList', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
    HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
    mocks.api.tasks.mockResolvedValue({
      course: {
        course_id: 7,
        course_name: '知识追踪研究方法',
        course_code: 'KT101',
        course_term: '2026春',
        course_status: 'active',
        class_name: '实验一班',
      },
      items: [{
        task_id: 19,
        experiment_id: 11,
        experiment_name: '反馈策略实验',
        class_name: '2026 实验一班',
        business_status: 'open',
        opens_at: null,
        closes_at: null,
        progress: { completed: 0, total: 8 },
        primary_action: 'start',
        available_actions: ['start'],
        feedback_mode: 'correctness',
        total_time_limit_seconds: null,
      }],
    })
  })
  it('starts a timed task only after confirmation, not after cancellation or dismissal', async () => {
    const data = await mocks.api.tasks()
    data.items[0].total_time_limit_seconds = 3600
    mocks.api.tasks.mockResolvedValue(data)
    mocks.api.start.mockResolvedValue({ session_id: 7, question_url: '/student/sessions/7/questions/11/' })
    const wrapper = await mountTaskList()
    const start = wrapper.get('[data-task-action="start"]')
    await start.trigger('click')
    await flushPromises()
    expect(wrapper.get('dialog[open]').text()).toContain('本次实验限时：60 分钟')
    expect(mocks.api.start).not.toHaveBeenCalled()
    await wrapper.get('[data-cancel-start]').trigger('click')
    await flushPromises()
    expect(wrapper.find('dialog[open]').exists()).toBe(false)
    expect(mocks.api.start).not.toHaveBeenCalled()
    await start.trigger('click')
    await wrapper.get('dialog').trigger('cancel')
    await flushPromises()
    expect(wrapper.find('dialog[open]').exists()).toBe(false)
    expect(mocks.api.start).not.toHaveBeenCalled()
    await start.trigger('click')
    await flushPromises()
    await wrapper.get('[data-confirm-start]').trigger('click')
    await flushPromises()
    expect(mocks.api.start).toHaveBeenCalledExactlyOnceWith(19)
    expect(mocks.push).toHaveBeenCalledWith('/student/sessions/7/questions/11/')
    wrapper.unmount()
  })

  it('resumes a timed task without showing the start confirmation', async () => {
    const data = await mocks.api.tasks()
    Object.assign(data.items[0], { total_time_limit_seconds: 3600, primary_action: 'resume' })
    mocks.api.tasks.mockResolvedValue(data)
    mocks.api.resume.mockResolvedValue({ session_id: 7, question_url: '/student/sessions/7/questions/11/' })
    const wrapper = await mountTaskList()
    await wrapper.get('[data-task-action="resume"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('dialog[open]').exists()).toBe(false)
    expect(mocks.api.start).not.toHaveBeenCalled()
    expect(mocks.api.resume).toHaveBeenCalledExactlyOnceWith(19)
    wrapper.unmount()
  })

  it('renders authoritative task progress without correctness state', async () => {
    const wrapper = await mountTaskList()
    await flushPromises()

    expect(wrapper.get('h1').text()).toBe('作业')
    expect(wrapper.text()).toContain('反馈策略实验')
    expect(wrapper.text()).toContain('0 / 8')
    expect(wrapper.get('.kt-student-task-row').text()).toContain('作业')
    expect(wrapper.get('[data-task-action="start"]').text()).toBe('开始作业')
    expect(wrapper.text()).not.toContain('正确')
    expect(wrapper.text()).not.toContain('错误')
    expect(mocks.warm).toHaveBeenCalledOnce()
    expect(mocks.api.start).not.toHaveBeenCalled()
  })
  it('primes the successful first entry and navigates the existing business route without a document reload', async () => {
    const runtime = { session_id: 7, entry_receipt: { navigation_id: 'original' } }
    mocks.api.start.mockResolvedValue({ session_id: 7, question_url: '/student/sessions/7/questions/11/', runtime })
    const wrapper = await mountTaskList()
    await flushPromises()
    await wrapper.get('[data-task-action="start"]').trigger('click')
    await flushPromises()
    expect(mocks.primeStart).toHaveBeenCalledWith(runtime)
    expect(mocks.push).toHaveBeenCalledWith('/student/sessions/7/questions/11/')
    wrapper.unmount()
  })

})
