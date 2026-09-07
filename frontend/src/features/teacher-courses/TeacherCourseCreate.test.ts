import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ api: { create: vi.fn() }, push: vi.fn() }))

vi.mock('vue-router', () => ({
  RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' },
  useRouter: () => ({ push: mocks.push }),
}))
vi.mock('@/app/bootstrap', () => ({
  getBootstrapContext: () => ({ csrf_token: 'csrf-token', user: { display_name: '李老师', username: 'teacher' } }),
}))
vi.mock('./api', () => ({ getTeacherCourseApi: () => mocks.api }))

import TeacherCourseCreate from './TeacherCourseCreate.vue'

describe('TeacherCourseCreate', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.api.create.mockResolvedValue({ course: { detail_url: '/teacher/courses/7/' } })
  })

  it('submits exactly the existing CourseForm fields then enters the returned course', async () => {
    const wrapper = mount(TeacherCourseCreate)
    await wrapper.get('input[name="name"]').setValue('研究方法')
    await wrapper.get('input[name="code"]').setValue('RM101')
    await wrapper.get('input[name="term"]').setValue('2026 秋')
    await wrapper.get('textarea[name="description"]').setValue('课程说明')
    await wrapper.get('form[data-course-create]').trigger('submit')
    await flushPromises()

    expect(mocks.api.create).toHaveBeenCalledWith({ name: '研究方法', code: 'RM101', term: '2026 秋', description: '课程说明' })
    expect(mocks.push).toHaveBeenCalledWith('/teacher/courses/7/')
  })
})
