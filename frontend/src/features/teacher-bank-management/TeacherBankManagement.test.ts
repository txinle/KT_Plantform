import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  route: { params: { courseId: '7', bankId: '11', questionId: '31', sourceCourseId: '8', creatorId: '9' }, query: {} as Record<string, string> },
  push: vi.fn(),
  api: {
    bankEditor: vi.fn(), updateBank: vi.fn(), previewQuestionImport: vi.fn(), confirmQuestionImport: vi.fn(),
    sharedBanks: vi.fn(), copySharedBank: vi.fn(),
  },
}))

vi.mock('vue-router', () => ({
  useRoute: () => mocks.route,
  useRouter: () => ({ push: mocks.push }),
}))
vi.mock('./api', () => ({ getTeacherBankManagementApi: () => mocks.api }))

import TeacherBankEditor from './TeacherBankEditor.vue'
import TeacherQuestionImport from './TeacherQuestionImport.vue'
import TeacherSharedBanks from './TeacherSharedBanks.vue'

const routerLink = { props: ['to'], template: '<a :href="to"><slot /></a>' }

describe('Teacher bank-management Vue views', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.api.bankEditor.mockResolvedValue({
      id: 11, name: '原题库', description: '原说明', visibility: 'private',
      visibility_choices: [{ value: 'private', label: '私有' }, { value: 'shared', label: '共享' }],
      course: { id: 7, name: '课程', code: 'C101', term: '2026 秋', status: 'active' },
      detail_url: '/teacher/courses/7/banks/11/',
    })
    mocks.api.updateBank.mockResolvedValue({ detail_url: '/teacher/courses/7/banks/11/' })
    mocks.api.previewQuestionImport.mockResolvedValue({
      preview_id: 'preview-1',
      preview: { rows: [{ sequence: 2, text: '导入题目', question_type: 'true_false', question_type_label: '判断题', option_count: 0, teacher_difficulty: null }] },
    })
    mocks.api.confirmQuestionImport.mockResolvedValue({ created: 1, detail_url: '/teacher/courses/7/banks/11/' })
    mocks.api.sharedBanks.mockResolvedValue({
      course: { id: 7, name: '课程', code: 'C101', term: '2026 秋', status: 'active' },
      source_course: { id: 8, name: '来源', code: 'S101', term: '2026 秋', status: 'active' },
      creator: { id: 9, name: '来源教师', username: 'source' },
      items: [{ id: 21, name: '共享题库', description: '说明', question_count: 3 }],
    })
    mocks.api.copySharedBank.mockResolvedValue({ id: 31, name: '副本', detail_url: '/teacher/courses/7/banks/31/' })
  })

  it('saves bank metadata through the JSON editor endpoint', async () => {
    const wrapper = mount(TeacherBankEditor, { global: { stubs: { RouterLink: routerLink } } })
    await flushPromises()
    await wrapper.get('#bank-name').setValue('更新题库')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(mocks.api.updateBank).toHaveBeenCalledWith(7, 11, {
      name: '更新题库', description: '原说明', visibility: 'private',
    })
    expect(mocks.push).toHaveBeenCalledWith('/teacher/courses/7/banks/11/')
  })

  it('keeps question imports in preview until the user confirms', async () => {
    const wrapper = mount(TeacherQuestionImport, { global: { stubs: { RouterLink: routerLink } } })
    const input = wrapper.get('input[type="file"]')
    const file = new File(['序号,题目\n2,导入题目'], 'questions.csv')
    Object.defineProperty(input.element, 'files', { value: [file] })

    await input.trigger('change')
    await flushPromises()
    expect(mocks.api.previewQuestionImport).toHaveBeenCalledWith(7, 11, file)
    expect(wrapper.text()).toContain('导入题目')
    expect(mocks.api.confirmQuestionImport).not.toHaveBeenCalled()

    await wrapper.get('[role="dialog"] button').trigger('click')
    await flushPromises()
    expect(mocks.api.confirmQuestionImport).toHaveBeenCalledWith(7, 11, 'preview-1')
    expect(mocks.push).toHaveBeenCalledWith('/teacher/courses/7/banks/11/')
  })

  it('copies a selected shared bank through the JSON action before navigating to its private copy', async () => {
    const wrapper = mount(TeacherSharedBanks, { global: { stubs: { RouterLink: routerLink } } })
    await flushPromises()
    await wrapper.get('button[type="button"]').trigger('click')
    await flushPromises()

    expect(mocks.api.copySharedBank).toHaveBeenCalledWith(7, 8, 9, 21)
    expect(mocks.push).toHaveBeenCalledWith('/teacher/courses/7/banks/31/')
  })
})
