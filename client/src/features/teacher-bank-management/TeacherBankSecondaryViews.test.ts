import { flushPromises, mount } from '@vue/test-utils'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  api: { archivedBanks: vi.fn(), restoreBank: vi.fn(), deleteBank: vi.fn(), sharedSources: vi.fn(), sharedCreators: vi.fn(), sharedBanks: vi.fn(), copySharedBank: vi.fn() },
  push: vi.fn(),
}))
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { courseId: '7', sourceCourseId: '8', creatorId: '9' }, query: {} }),
  useRouter: () => ({ push: mocks.push }),
}))
vi.mock('./api', () => ({ getTeacherBankManagementApi: () => mocks.api }))
import TeacherArchivedBanks from './TeacherArchivedBanks.vue'
import TeacherSharedSources from './TeacherSharedSources.vue'
import TeacherSharedCreators from './TeacherSharedCreators.vue'
import TeacherSharedBanks from './TeacherSharedBanks.vue'
const global = { stubs: { RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' } } }
const course = { id: 7, name: '英语', code: 'EN', term: '2026' }
beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
})
beforeEach(() => {
  vi.clearAllMocks()
  mocks.api.archivedBanks.mockResolvedValue({ course, items: [{ id: 11, name: '归档阅读', description: '', question_count: 3, complete_question_count: 1, created_by_name: '教师', updated_at: '2026-10-31', detail_url: '/bank/11/', can_edit_metadata: true }], page: { number: 1, total_pages: 1 } })
  mocks.api.sharedSources.mockResolvedValue({ course, items: [{ id: 8, name: '来源英语', code: 'EN2', term: '2026', shared_bank_count: 1, shared_creator_count: 1 }] })
  mocks.api.sharedCreators.mockResolvedValue({ course, source_course: course, items: [{ id: 9, name: '教师', username: 'teacher', shared_bank_count: 1 }] })
  mocks.api.sharedBanks.mockResolvedValue({ course, source_course: course, creator: { id: 9, name: '教师' }, items: [{ id: 11, name: '阅读', question_count: 3, complete_question_count: 1 }] })
})

describe('Site secondary bank routes', () => {
  it.each([TeacherSharedSources, TeacherSharedCreators, TeacherSharedBanks])('keeps the shared-bank header and all four module entries throughout source browsing', async component => {
    const wrapper = mount(component, { global })
    await flushPromises()
    expect(wrapper.get('h1').text()).toBe('共享题库')
    expect(wrapper.get('.kt-bank-module-nav').findAll('a')).toHaveLength(4)
    expect(wrapper.get('.kt-bank-module-nav a:nth-child(2)').attributes('href')).toBe('/teacher/courses/7/banks/?tab=course-kcs')
    expect(wrapper.get('.kt-bank-module-nav [aria-current="page"]').text()).toBe('共享题库')
    wrapper.unmount()
  })
  it('opens a native deletion confirmation, cancels without a write, and preserves server refusal', async () => {
    const wrapper = mount(TeacherArchivedBanks, { global })
    await flushPromises()
    expect(wrapper.get('.kt-bank-module-nav [aria-current="page"]').text()).toBe('已归档')
    await wrapper.get('[data-delete-bank="11"]').trigger('click')
    const dialog = wrapper.get('dialog[open]')
    expect(dialog.text()).toContain('归档阅读')
    expect(mocks.api.deleteBank).not.toHaveBeenCalled()
    await dialog.trigger('cancel')
    await flushPromises()
    expect(wrapper.find('dialog[open]').exists()).toBe(false)
    await wrapper.get('[data-delete-bank="11"]').trigger('click')
    mocks.api.deleteBank.mockRejectedValueOnce(new Error('题目已被实验引用'))
    await wrapper.get('dialog [data-confirm-delete]').trigger('click')
    await flushPromises()
    expect(mocks.api.deleteBank).toHaveBeenCalledWith(7, 11)
    expect(wrapper.get('dialog[open]').text()).toContain('题目已被实验引用')
    wrapper.unmount()
  })
})
