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
      }],
    })
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
