import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { acceptBootstrapContext, clearBootstrapContext } from '@/app/bootstrap'
import StudentCoursesLayout from './StudentCoursesLayout.vue'
import StudentTaskList from './StudentTaskList.vue'

const mocks = vi.hoisted(() => ({ tasks: vi.fn() }))
vi.mock('./api', () => ({ getStudentCourseApi: () => mocks }))
vi.mock('@/assessment-runtime/offline-cache', () => ({ prepareOfflineCommonResources: async () => undefined }))
vi.mock('@/assessment-runtime/session-controller', () => ({ getSessionController: vi.fn() }))
vi.mock('@/design-system/components/WorkspaceShell.vue', () => ({ default: defineComponent({
  props: { courseName: { type: String, default: '' }, userName: { type: String, default: '' } }, template: '<div><header>{{ userName }} {{ courseName }}</header><slot /></div>',
}) }))

function signIn(id = 7) {
  acceptBootstrapContext({ csrf_token: `csrf-${id}`, user: { id, role: 'student', display_name: `学生${id}`,
    username: `student${id}`, student_number: `S${id}`, home_url: '/student/', uses_initial_password: false,
    initial_password_reminder_pending: false, research_consent_required: false, research_consent_version: 'v1' } })
}
function collection(id: number, name = `课程${id}`) {
  return { course: { course_id: id, course_name: name, course_code: `KT${id}`, class_name: '一班' }, items: [
    { task_id: id, experiment_name: `任务${id}`, business_status: 'open', primary_action: 'start', progress: { completed: 0, total: 2 } },
  ] }
}
async function openCourse() {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/student/courses/:courseId/', component: StudentCoursesLayout, children: [
      { path: '', name: 'student-course', component: StudentTaskList },
    ] },
  ] })
  await router.push('/student/courses/7/')
  const wrapper = mount(RouterView, { global: { plugins: [router] } })
  await flushPromises()
  return { router, wrapper }
}

describe('student course shared loading', () => {
  beforeEach(() => { vi.clearAllMocks(); signIn() })
  afterEach(() => clearBootstrapContext())

  it('loads once for the actual layout and child, and reloads both on course changes', async () => {
    mocks.tasks.mockImplementation(async (id: number) => collection(id))
    const { router, wrapper } = await openCourse()
    expect(mocks.tasks).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('课程7')
    await router.push('/student/courses/8/')
    await flushPromises()
    expect(mocks.tasks).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('课程8')
    expect(wrapper.text()).toContain('任务8')
    expect(wrapper.text()).not.toContain('任务7')
    wrapper.unmount()
  })

  it('allows retry after failure and explicit refresh without retaining actions', async () => {
    mocks.tasks.mockRejectedValueOnce(new Error('temporary')).mockResolvedValueOnce(collection(7))
    const { wrapper } = await openCourse()
    expect(wrapper.text()).toContain('temporary')
    await wrapper.get('[data-refresh-tasks]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('任务7')
    mocks.tasks.mockResolvedValueOnce({ ...collection(7), items: [] })
    await wrapper.get('[data-refresh-tasks]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).not.toContain('任务7')
    expect(mocks.tasks).toHaveBeenCalledTimes(3)
    wrapper.unmount()
  })

  it('discards old course and account responses that arrive after a new request', async () => {
    let oldResponse!: (value: unknown) => void
    mocks.tasks.mockReturnValueOnce(new Promise(resolve => { oldResponse = resolve }))
      .mockResolvedValueOnce(collection(8))
      .mockResolvedValueOnce(collection(8, '新账号课程'))
    const { router, wrapper } = await openCourse()
    await router.push('/student/courses/8/')
    await flushPromises()
    signIn(9)
    await flushPromises()
    oldResponse(collection(7, '旧账号课程'))
    await flushPromises()
    expect(wrapper.text()).toContain('新账号课程')
    expect(wrapper.text()).not.toContain('旧账号课程')
    expect(wrapper.text()).toContain('学生9')
    expect(mocks.tasks).toHaveBeenCalledTimes(3)
    clearBootstrapContext()
    await flushPromises()
    expect(wrapper.text()).not.toContain('新账号课程')
    wrapper.unmount()
  })
})
