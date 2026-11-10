import { ApiError } from '@/api/client'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  api: { list: vi.fn(), courseConcepts: vi.fn(), createBank: vi.fn(), createCourseConcept: vi.fn(), previewCourseConceptImport: vi.fn(), confirmCourseConceptImport: vi.fn() },
  managementApi: { bankEditor: vi.fn(), updateBank: vi.fn(), archiveBank: vi.fn(), updateCourseConcept: vi.fn(), setCourseConceptStatus: vi.fn(), deleteCourseConcept: vi.fn() },
  push: vi.fn(),
  replace: vi.fn(),
  route: { params: { courseId: '7' }, query: {} as Record<string, string> },
}))

vi.mock('vue-router', () => ({
  useRoute: () => mocks.route,
  useRouter: () => ({ push: mocks.push, replace: mocks.replace }),
  RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' },
}))
vi.mock('./api', () => ({ getTeacherQuestionBankApi: () => mocks.api }))
vi.mock('../teacher-bank-management/api', () => ({ getTeacherBankManagementApi: () => mocks.managementApi }))
vi.mock('@/app/bootstrap', () => ({ getBootstrapContext: () => ({ csrf_token: 'csrf-token' }) }))

import TeacherQuestionBankList from './TeacherQuestionBankList.vue'

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
})

describe('TeacherQuestionBankList', () => {
  it('confirms bank archiving in a centered modal before sending the request', async () => {
    const wrapper = mount(TeacherQuestionBankList)
    await flushPromises()
    await wrapper.get('[data-bank-archive]').trigger('click')
    await flushPromises()
    const dialog = wrapper.get('dialog[aria-label="归档题库"]')
    expect(mocks.managementApi.archiveBank).not.toHaveBeenCalled()
    await dialog.trigger('cancel')
    await flushPromises()
    expect(mocks.managementApi.archiveBank).not.toHaveBeenCalled()
    await wrapper.get('[data-bank-archive]').trigger('click')
    await wrapper.get('[data-confirm-bank-archive]').trigger('click')
    await flushPromises()
    expect(mocks.managementApi.archiveBank).toHaveBeenCalledWith(7, 11)
    wrapper.unmount()
  })

  it('edits bank metadata in place without navigating away or saving on cancellation', async () => {
    mocks.managementApi.bankEditor.mockResolvedValue({ id: 11, name: '演示题库', description: '', visibility: 'private', visibility_choices: [{ value: 'private', label: '私有' }], course: { code: 'KT101', term: '秋' } })
    mocks.managementApi.updateBank.mockResolvedValue({ detail_url: '/teacher/courses/7/banks/11/' })
    const wrapper = mount(TeacherQuestionBankList)
    await flushPromises()
    await wrapper.get('[data-edit-bank="11"]').trigger('click')
    await flushPromises()
    let dialog = wrapper.get('dialog[aria-label="编辑题库"]')
    await dialog.get('#bank-name').setValue('取消的修改')
    await dialog.trigger('cancel')
    await flushPromises()
    expect(mocks.managementApi.updateBank).not.toHaveBeenCalled()
    await wrapper.get('[data-edit-bank="11"]').trigger('click')
    await flushPromises()
    dialog = wrapper.get('dialog[aria-label="编辑题库"]')
    expect(dialog.get<HTMLInputElement>('#bank-name').element.value).toBe('演示题库')
    await dialog.get('#bank-name').setValue('新的题库名')
    await dialog.get('form').trigger('submit')
    await flushPromises()
    expect(mocks.managementApi.updateBank).toHaveBeenCalledWith(7, 11, expect.objectContaining({ name: '新的题库名' }))
    expect(mocks.push).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('uses the four Site module entries and a native modal for creating a bank', async () => {
    const wrapper = mount(TeacherQuestionBankList)
    await flushPromises()
    expect(wrapper.get('.kt-bank-module-tabs').text()).toContain('共享题库')
    expect(wrapper.get('.kt-bank-module-tabs').text()).toContain('已归档')
    expect(wrapper.find('#course-bank-heading').exists()).toBe(false)
    await wrapper.get('.kt-site-bank-toolbar button').trigger('click')
    await flushPromises()
    const dialog = wrapper.get('dialog[aria-label="创建题库"]')
    expect(dialog.attributes()).toHaveProperty('open')
    await dialog.get('input[name="name"]').setValue('不应保存')
    await dialog.trigger('cancel')
    await flushPromises()
    expect(mocks.api.createBank).not.toHaveBeenCalled()
    expect(dialog.attributes()).not.toHaveProperty('open')
    wrapper.unmount()
  })

  beforeEach(() => {
    vi.clearAllMocks()
    mocks.route.query = {}
    mocks.api.list.mockResolvedValue({
      course: { id: 7, name: '研究方法', code: 'KT101', term: '2026 秋', status: 'active' },
      items: [{ id: 11, name: '演示题库', description: '覆盖六类题型', visibility: 'private', visibility_label: '私有', question_count: 4, created_by_name: '课程教师', updated_at: '2026-08-30T08:00:00Z', detail_url: '/teacher/courses/7/banks/11/', visibility_update_url: '/teacher/courses/7/banks/11/visibility/', archive_url: '/teacher/courses/7/banks/11/archive/', can_edit_metadata: true }],
      page: { number: 1, size: 20, total_items: 1, total_pages: 1, start: 1, end: 1 },
      summary: { archived_count: 2, archived_banks_url: '/teacher/courses/7/banks/archived/', shared_sources_url: '/teacher/courses/7/banks/shared/' },
    })
    mocks.api.courseConcepts.mockResolvedValue({
      can_manage: true,
      course: { id: 7, name: '研究方法', code: 'KT101', term: '2026 秋', status: 'active' },
      items: [{ id: 23, kc_code: 'KC1', name: '基础运算', description: '整数四则运算', status: 'active', question_count: 2, bank_count: 1, role_counts: { level_1: 1, level_2: 1, level_3: 0 }, distribution: [{ bank_id: 11, bank_name: '演示题库', question_count: 2, detail_url: '/teacher/courses/7/banks/11/' }], can_rename: true, can_delete: true, can_archive: true, can_restore: false, rename_url: '/teacher/courses/7/concepts/23/rename/', delete_url: '/teacher/courses/7/concepts/23/delete/' }],
      page: { number: 1, size: 20, total_items: 1, total_pages: 1, start: 1, end: 1 },
      summary: { total: 3, active: 1, used: 1, archived: 2 },
    })
    mocks.managementApi.archiveBank.mockResolvedValue({ id: 11, visibility: 'archived' })
    mocks.managementApi.updateCourseConcept.mockResolvedValue({ id: 23, kc_code: 'KC1', name: '更新名称', description: '更新说明', status: 'active' })
    mocks.managementApi.setCourseConceptStatus.mockResolvedValue({ id: 23, status: 'archived' })
  })

  it('keeps course KC management and archive access in the Site module structure', async () => {
    const wrapper = mount(TeacherQuestionBankList)
    await flushPromises()
    expect(wrapper.get('h1').text()).toBe('题库')
    expect(wrapper.find('[aria-label="层级目录"]').exists()).toBe(false)
    expect(wrapper.get('.kt-site-bank-toolbar select + button').text()).toBe('新建题库')
    expect(wrapper.get('[aria-label="题库模块"]').classes()).toContain('kt-sidebar-secondary-nav')
    expect(wrapper.get('[data-tab="banks"]').classes()).toContain('is-active')
    expect(wrapper.find('[data-panel="course-kcs"]').exists()).toBe(false)
    expect(wrapper.get('[aria-label="课程题库"]').text()).toContain('演示题库')
    expect(wrapper.text()).toContain('演示题库')
    expect(wrapper.get('[data-tab="course-kcs"] span').text()).toBe('1')
    expect(wrapper.get('a[href="/teacher/courses/7/banks/archived/"]').text()).toBe('已归档')
    expect(wrapper.get('form[data-create-bank] input[name="name"]')).toBeTruthy()
    await wrapper.get('[data-bank-archive]').trigger('click')
    await wrapper.get('[data-confirm-bank-archive]').trigger('click')
    await flushPromises()
    expect(mocks.managementApi.archiveBank).toHaveBeenCalledWith(7, 11)
  })

  it('renders only the KC overview/list/create/import composition on the KC tab', async () => {
    mocks.route.query = { tab: 'course-kcs' }
    const wrapper = mount(TeacherQuestionBankList)
    await flushPromises()
    expect(wrapper.get('[data-tab="course-kcs"]').classes()).toContain('is-active')
    expect(wrapper.find('[data-panel="banks"]').exists()).toBe(false)
    expect(wrapper.get('[data-panel="course-kcs"]')).toBeTruthy()
    expect(wrapper.find('[aria-label="层级目录"]').exists()).toBe(false)
    expect(wrapper.find('.kt-qb-titlebar').exists()).toBe(false)
    expect(wrapper.text()).toContain('课程 KC 总览')
    expect(wrapper.text()).toContain('课程 KC 列表')
    expect(mocks.api.courseConcepts).toHaveBeenCalledWith(7, { status: 'active', page: 1, pageSize: 5 })
    expect(wrapper.get('[data-archived-kcs]').attributes('href')).toContain('tab=archived-kcs')
    expect(wrapper.text()).toContain('基础运算')
    expect(wrapper.get('form[data-create-concept] input[name="sequence"]')).toBeTruthy()
    expect(wrapper.get('form[data-create-concept] input[name="name"]')).toBeTruthy()
    expect(wrapper.get('[data-course-kc-import]')).toBeTruthy()
    expect(wrapper.get('[data-course-kc-template]').attributes('href')).toBe('/api/v1/teacher/import-templates/course-kcs.csv')
  })

  it('shows the server file validation detail next to the course KC upload control', async () => {
    mocks.route.query = { tab: 'course-kcs' }
    mocks.api.previewCourseConceptImport.mockRejectedValueOnce(new ApiError(400, {
      error: { code: 'VALIDATION_ERROR', message: '请求数据无效。', fields: { file: '第 168 行知识点名称重复：分析步骤' } },
    }))
    const wrapper = mount(TeacherQuestionBankList)
    await flushPromises()
    const input = wrapper.get('[data-course-kc-file]')
    Object.defineProperty(input.element, 'files', { value: [new File([''], '电工.csv')] })
    await input.trigger('change')
    await flushPromises()
    const card = wrapper.get('[data-course-kc-import]')
    expect(card.text()).toContain('电工.csv')
    expect(card.text()).toContain('第 168 行知识点名称重复：分析步骤')
    expect(wrapper.text()).not.toContain('请求数据无效。')
  })

  it('previews and confirms a persistent course KC import batch in place', async () => {
    mocks.route.query = { tab: 'course-kcs' }
    mocks.api.previewCourseConceptImport.mockResolvedValue({
      batch_id: 'batch-1',
      preview: {
        confirmable: true,
        rows: [
          { sequence: 1, code: 'KC1', name: '数学基础', classification: 'new' },
          { sequence: 2, code: 'KC2', name: '数与运算', classification: 'unchanged' },
        ],
      },
    })
    mocks.api.confirmCourseConceptImport.mockResolvedValue({ created: 1, unchanged: 1 })
    const wrapper = mount(TeacherQuestionBankList)
    await flushPromises()
    const input = wrapper.get('input[data-course-kc-file]')
    const file = new File(['序号,知识点名称\n1,数学基础'], 'course-kcs.csv')
    Object.defineProperty(input.element, 'files', { value: [file] })

    await input.trigger('change')
    await flushPromises()

    expect(mocks.api.previewCourseConceptImport).toHaveBeenCalledWith(7, file)
    expect(wrapper.get('[role="dialog"]').text()).toContain('数学基础')
    expect(wrapper.get('[role="dialog"]').text()).toContain('可新增')
    await wrapper.get('[data-confirm-course-kc-import]').trigger('click')
    await flushPromises()
    expect(mocks.api.confirmCourseConceptImport).toHaveBeenCalledWith(7, 'batch-1')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
  })

  it.each(['.kt-qb-desktop-table', '.kt-qb-mobile-list'])('renames and archives course KCs through the JSON API from %s', async (selector) => {
    mocks.route.query = { tab: 'course-kcs' }
    const wrapper = mount(TeacherQuestionBankList)
    await flushPromises()

    const list = wrapper.get(selector)
    await list.get('[data-concept-rename]').trigger('click')
    const renameForm = list.get('[data-concept-rename-form]')
    await renameForm.get('input[name="name"]').setValue('更新名称')
    await renameForm.get('textarea[name="description"]').setValue('更新说明')
    await renameForm.trigger('submit')
    await flushPromises()
    expect(mocks.managementApi.updateCourseConcept).toHaveBeenCalledWith(7, 23, {
      name: '更新名称', description: '更新说明',
    })

    await wrapper.get(selector).get('[data-concept-archive]').trigger('click')
    await flushPromises()
    expect(mocks.managementApi.setCourseConceptStatus).toHaveBeenCalledWith(7, 23, 'archived')
  })
  it.each(['.kt-qb-desktop-table', '.kt-qb-mobile-list'])('restores an archived KC using server capabilities from %s', async (selector) => {
    mocks.route.query = { tab: 'archived-kcs', page: '2' }
    const collection = await mocks.api.courseConcepts()
    Object.assign(collection.items[0], { status: 'archived', can_archive: false, can_restore: true })
    mocks.api.courseConcepts.mockClear()
    mocks.api.courseConcepts.mockResolvedValue(collection)
    const wrapper = mount(TeacherQuestionBankList)
    await flushPromises()
    expect(mocks.api.courseConcepts).toHaveBeenCalledWith(7, { status: 'archived', page: 2, pageSize: 5 })
    expect(wrapper.get('h1').text()).toBe('已归档课程 KC')
    expect(wrapper.find('[data-create-concept]').exists()).toBe(false)
    expect(wrapper.get(selector).find('[data-concept-archive]').exists()).toBe(false)
    await wrapper.get(selector).get('[data-concept-restore]').trigger('click')
    await flushPromises()
    expect(mocks.managementApi.setCourseConceptStatus).toHaveBeenCalledWith(7, 23, 'active')
    expect(mocks.api.courseConcepts).toHaveBeenCalledTimes(2)
  })


  it.each([[undefined, '已永久删除'], [{ id: 23, status: 'archived' }, '已归档']])('confirms deletion and displays the server outcome', async (result, message) => {
    mocks.route.query = { tab: 'course-kcs' }
    mocks.managementApi.deleteCourseConcept.mockResolvedValue(result)
    const wrapper = mount(TeacherQuestionBankList)
    await flushPromises()
    await wrapper.get('[data-concept-delete]').trigger('click')
    expect(wrapper.get('dialog').text()).toContain('基础运算')
    expect(mocks.managementApi.deleteCourseConcept).not.toHaveBeenCalled()
    await wrapper.get('[data-confirm-concept-delete]').trigger('click')
    await flushPromises()
    expect(mocks.managementApi.deleteCourseConcept).toHaveBeenCalledWith(7, 23)
    expect(wrapper.get('[role="status"]').text()).toContain(message)
  })

  it('hides delete on both layouts when the server denies it', async () => {
    mocks.route.query = { tab: 'course-kcs' }
    const collection = await mocks.api.courseConcepts()
    collection.items[0].can_delete = false
    collection.can_manage = false
    const wrapper = mount(TeacherQuestionBankList)
    await flushPromises()
    expect(wrapper.find('[data-concept-delete]').exists()).toBe(false)
    expect(wrapper.find('[data-course-kc-import]').exists()).toBe(false)
    expect(wrapper.find('[data-create-concept]').exists()).toBe(false)
  })

  it('recovers a page emptied by the last delete without leaving a 404', async () => {
    mocks.route.query = { tab: 'course-kcs', page: '2' }
    const wrapper = mount(TeacherQuestionBankList)
    await flushPromises()
    await wrapper.get('[data-concept-delete]').trigger('click')
    mocks.api.courseConcepts.mockRejectedValueOnce(new ApiError(404, { error: { message: '无效页面' } }))
    await wrapper.get('[data-confirm-concept-delete]').trigger('click')
    await flushPromises()
    expect(mocks.replace).toHaveBeenCalledWith({ query: { tab: 'course-kcs', page: 1 } })
    expect(wrapper.text()).not.toContain('无效页面')
  })

  it('searches across archived pages and applies a keyboard selection by identity', async () => {
    mocks.route.query = { tab: 'archived-kcs', page: '4' }
    const wrapper = mount(TeacherQuestionBankList)
    await flushPromises()
    const search = wrapper.get('[role="combobox"]')
    await search.setValue('kc1')
    await vi.waitFor(() => expect(wrapper.get('[role="option"]').text()).toContain('基础运算'))
    expect(mocks.api.courseConcepts).toHaveBeenLastCalledWith(7, { query: 'kc1', status: 'archived', page: 1, pageSize: 5 })
    await search.trigger('keydown', { key: 'ArrowDown' })
    await search.trigger('keydown', { key: 'Enter' })
    expect(mocks.push).toHaveBeenCalledWith({ query: { tab: 'archived-kcs', concept_id: 23, q: '基础运算' } })
    wrapper.unmount()
  })

  it('clears an exact-match filter and returns to the whole active list', async () => {
    mocks.route.query = { tab: 'course-kcs', q: '基础运算', concept_id: '23' }
    const wrapper = mount(TeacherQuestionBankList)
    await flushPromises()
    expect(mocks.api.courseConcepts).toHaveBeenCalledWith(7, { status: 'active', page: 1, pageSize: 5, conceptId: 23 })
    await wrapper.get('[aria-label="清除 KC 搜索"]').trigger('click')
    expect(mocks.push).toHaveBeenCalledWith({ query: { tab: 'course-kcs' } })
    wrapper.unmount()
  })

  it('uses the tall desktop list capacity and resets an old page on resize', async () => {
    vi.stubGlobal('innerWidth', 1440)
    vi.stubGlobal('innerHeight', 900)
    mocks.route.query = { tab: 'course-kcs', page: '3' }
    const wrapper = mount(TeacherQuestionBankList)
    try {
      await flushPromises()
      expect(mocks.api.courseConcepts).toHaveBeenCalledWith(7, expect.objectContaining({ page: 3, pageSize: 7 }))
      vi.stubGlobal('innerHeight', 1080)
      window.dispatchEvent(new Event('resize'))
      await flushPromises()
      expect(mocks.replace).toHaveBeenCalledWith({ query: { tab: 'course-kcs', page: 1 } })
    } finally {
      wrapper.unmount()
      vi.unstubAllGlobals()
    }
  })
})
