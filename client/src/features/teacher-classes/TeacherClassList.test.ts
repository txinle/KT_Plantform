import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  operationId: '',
  api: { list: vi.fn(), create: vi.fn() },
  deletion: {
    previewExperiment: vi.fn(), deleteExperiment: vi.fn(),
    previewClassExperiment: vi.fn(), removeClassExperiment: vi.fn(),
    previewClass: vi.fn(), deleteClass: vi.fn(), cleanup: vi.fn(),
    registerOperation: vi.fn(), operation: vi.fn(),
  },
  push: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { courseId: '7' }, query: {} }),
  useRouter: () => ({ push: mocks.push }),
  RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' },
}))
vi.mock('./api', () => ({ getTeacherClassApi: () => mocks.api }))
vi.mock('@/features/teacher-deletion/api', () => ({ getTeacherDeletionApi: () => mocks.deletion }))
vi.mock('@/app/bootstrap', () => ({
  getBootstrapContext: () => ({ csrf_token: 'csrf-token', user: { id: 71, role: 'teacher' } }),
  bootstrapContext: { value: { user: { id: 71, role: 'teacher' } } },
  getAuthenticationGeneration: () => 0,
}))

import TeacherClassList from './TeacherClassList.vue'

describe('TeacherClassList', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    sessionStorage.clear()
    mocks.operationId = ''
    mocks.deletion.registerOperation.mockImplementation(async (...args: [number, { operation_id: string }]) => {
      mocks.operationId = args[1].operation_id
      return { operation_id: mocks.operationId, state: 'pending', result: null }
    })
    mocks.deletion.operation.mockImplementation(async () => ({ operation_id: mocks.operationId, state: 'pending', result: null }))
    mocks.api.list.mockResolvedValue({
      course: { id: 7, name: '研究方法', code: 'KT101', term: '2026 秋', status: 'active' },
      capabilities: { can_create: true, can_manage: true },
      items: [{
        id: 11,
        name: '实验一班',
        academic_year: '2026',
        is_active: true,
        active_student_count: 32,
        detail_url: '/teacher/courses/7/classes/11/',
      }],
      page: { number: 1, size: 20, total_items: 1, total_pages: 1, start: 1, end: 1 },
    })
    mocks.deletion.previewClass.mockResolvedValue({
      action: 'delete_class', object_id: 11, object_name: '实验一班', allowed: true, blocked_reason: '',
      experiments: [], counts: { class_count: 1, session_count: 0, export_count: 0, member_count: 0 }, confirmation_token: 'class-token',
    })
    mocks.deletion.deleteClass.mockImplementation(async () => ({ deleted: true, cleanup_pending: false, cleanup_id: null, operation_id: mocks.operationId }))
  })

  it('renders the paged class facts and creates a class through the JSON API', async () => {
    mocks.api.create.mockResolvedValue({
      class_group: {
        id: 12,
        name: '实验二班',
        academic_year: '2027',
        is_active: true,
        active_student_count: 0,
        detail_url: '/teacher/courses/7/classes/12/',
      },
    })
    const wrapper = mount(TeacherClassList)
    await flushPromises()

    expect(wrapper.get('h1').text()).toBe('班级')
    expect(wrapper.text()).toContain('实验一班')
    expect(wrapper.text()).toContain('32 人')
    expect(wrapper.find('form[method="post"]').exists()).toBe(false)
    await wrapper.get('input[name="name"]').setValue('实验二班')
    await wrapper.get('input[name="academic_year"]').setValue('2027')
    await wrapper.get('.kt-class-create-form').trigger('submit')
    await flushPromises()

    expect(mocks.api.create).toHaveBeenCalledWith(7, {
      name: '实验二班',
      academic_year: '2027',
    })
    expect(mocks.push).toHaveBeenCalledWith('/teacher/courses/7/classes/12/')
  })

  it('uses the server creation capability instead of inferring it from course status', async () => {
    mocks.api.list.mockResolvedValue({
      course: { id: 7, name: '研究方法', code: 'KT101', term: '2026 秋', status: 'active' },
      capabilities: { can_create: false },
      items: [],
      page: { number: 1, size: 20, total_items: 0, total_pages: 1, start: 0, end: 0 },
    })

    const wrapper = mount(TeacherClassList)
    await flushPromises()

    expect(wrapper.find('.kt-class-create').exists()).toBe(false)
  })

  it('deletes a class only after the server preview and refreshes the list', async () => {
    const wrapper = mount(TeacherClassList)
    await flushPromises()

    await wrapper.findAll('[data-delete-class="11"]')[0]!.trigger('click')
    await flushPromises()
    await wrapper.get('[data-deletion-confirm]').trigger('click')
    await flushPromises()

    expect(mocks.deletion.previewClass).toHaveBeenCalledWith(7, 11, expect.any(AbortSignal))
    expect(mocks.deletion.deleteClass).toHaveBeenCalledWith(7, 11, 'class-token', mocks.operationId)
    expect(mocks.api.list).toHaveBeenCalledTimes(2)
  })

  it('shows an API validation failure without navigating away', async () => {
    mocks.api.create.mockRejectedValue(new Error('年级必须填写四位年份，如：2025。'))
    const wrapper = mount(TeacherClassList)
    await flushPromises()
    await wrapper.get('input[name="name"]').setValue('实验二班')
    await wrapper.get('input[name="academic_year"]').setValue('2027秋')
    await wrapper.get('.kt-class-create-form').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain('年级必须填写四位年份，如：2025。')
    expect(mocks.push).not.toHaveBeenCalled()
  })
})
