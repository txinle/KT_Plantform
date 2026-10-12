import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  api: { list: vi.fn(), courseConcepts: vi.fn(), createBank: vi.fn(), createCourseConcept: vi.fn(), previewCourseConceptImport: vi.fn(), confirmCourseConceptImport: vi.fn() },
  managementApi: { archiveBank: vi.fn(), updateCourseConcept: vi.fn(), setCourseConceptStatus: vi.fn() },
  push: vi.fn(),
  route: { params: { courseId: '7' }, query: {} as Record<string, string> },
}))

vi.mock('vue-router', () => ({
  useRoute: () => mocks.route,
  useRouter: () => ({ push: mocks.push }),
  RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' },
}))
vi.mock('./api', () => ({ getTeacherQuestionBankApi: () => mocks.api }))
vi.mock('../teacher-bank-management/api', () => ({ getTeacherBankManagementApi: () => mocks.managementApi }))
vi.mock('@/app/bootstrap', () => ({ getBootstrapContext: () => ({ csrf_token: 'csrf-token' }) }))

import TeacherQuestionBankList from './TeacherQuestionBankList.vue'

describe('TeacherQuestionBankList', () => {
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
      course: { id: 7, name: '研究方法', code: 'KT101', term: '2026 秋', status: 'active' },
      items: [{ id: 23, kc_code: 'KC1', name: '基础运算', description: '整数四则运算', status: 'active', question_count: 2, bank_count: 1, role_counts: { level_1: 1, level_2: 1, level_3: 0 }, distribution: [{ bank_id: 11, bank_name: '演示题库', question_count: 2, detail_url: '/teacher/courses/7/banks/11/' }], can_rename: true, can_delete: true, can_archive: true, can_restore: false, rename_url: '/teacher/courses/7/concepts/23/rename/', delete_url: '/teacher/courses/7/concepts/23/delete/' }],
      page: { number: 1, size: 20, total_items: 1, total_pages: 1, start: 1, end: 1 },
      summary: { total: 1, active: 1, used: 1, archived: 0 },
    })
    mocks.managementApi.archiveBank.mockResolvedValue({ id: 11, visibility: 'archived' })
    mocks.managementApi.updateCourseConcept.mockResolvedValue({ id: 23, kc_code: 'KC1', name: '更新名称', description: '更新说明', status: 'active' })
    mocks.managementApi.setCourseConceptStatus.mockResolvedValue({ id: 23, status: 'archived' })
  })

  it('uses the confirmed two-tab bank-management desktop structure by default', async () => {
    const wrapper = mount(TeacherQuestionBankList)
    await flushPromises()
    expect(wrapper.get('h1').text()).toBe('题库')
    expect(wrapper.get('[aria-label="层级目录"]').text()).toContain('研究方法')
    expect(wrapper.get('[aria-label="题库模块"]').classes()).toContain('kt-sidebar-secondary-nav')
    expect(wrapper.get('[data-tab="banks"]').classes()).toContain('is-active')
    expect(wrapper.find('[data-panel="course-kcs"]').exists()).toBe(false)
    expect(wrapper.get('[data-panel="banks"] h2').text()).toBe('课程题库')
    expect(wrapper.text()).toContain('演示题库')
    expect(wrapper.text()).toContain('课程 KC 总库')
    expect(wrapper.get('a[href="/teacher/courses/7/banks/archived/"]').text()).toContain('2')
    expect(wrapper.get('form[data-create-bank] input[name="name"]')).toBeTruthy()
    await wrapper.get('[data-bank-archive]').trigger('click')
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
    expect(wrapper.text()).toContain('课程 KC 总览')
    expect(wrapper.text()).toContain('课程 KC 列表')
    expect(wrapper.text()).toContain('基础运算')
    expect(wrapper.get('form[data-create-concept] input[name="sequence"]')).toBeTruthy()
    expect(wrapper.get('form[data-create-concept] input[name="name"]')).toBeTruthy()
    expect(wrapper.get('[data-course-kc-import]')).toBeTruthy()
    expect(wrapper.get('[data-course-kc-template]').attributes('href')).toBe('/api/v1/teacher/import-templates/course-kcs.csv')
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
    mocks.route.query = { tab: 'course-kcs' }
    const collection = await mocks.api.courseConcepts()
    Object.assign(collection.items[0], { status: 'archived', can_archive: false, can_restore: true })
    mocks.api.courseConcepts.mockClear()
    mocks.api.courseConcepts.mockResolvedValue(collection)
    const wrapper = mount(TeacherQuestionBankList)
    await flushPromises()
    expect(wrapper.get(selector).find('[data-concept-archive]').exists()).toBe(false)
    await wrapper.get(selector).get('[data-concept-restore]').trigger('click')
    await flushPromises()
    expect(mocks.managementApi.setCourseConceptStatus).toHaveBeenCalledWith(7, 23, 'active')
    expect(mocks.api.courseConcepts).toHaveBeenCalledTimes(2)
  })

})
