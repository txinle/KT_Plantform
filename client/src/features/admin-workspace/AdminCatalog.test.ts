import { mount, flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
const mocks = vi.hoisted(() => ({ get: vi.fn(), route: { query: {} as Record<string, string> } }))
vi.mock('./api', () => ({ getAdminApi: () => mocks, statusLabel: () => '状态' }))
vi.mock('vue-router', () => ({ useRoute: () => mocks.route }))
import AdminCatalog from './AdminCatalog.vue'
const global = { stubs: { SiteDialog: true, AdminQuestionDialog: true, AdminConceptDialog: true, AdminTaskDialog: true, AdminBankDialog: true, AdminExportDialog: true } }
describe('administrator catalog destinations', () => {
  beforeEach(() => { vi.resetAllMocks(); mocks.route.query = {} })
  it('opens a class task from the administrator overview without a teacher page', async () => {
    mocks.route.query = { tab: 'tasks', task_id: '5' }
    mocks.get.mockResolvedValue({ experiments: [], tasks: [{ id: 5, name: '任务', course_id: 3 }], exports: [] })
    const wrapper = mount(AdminCatalog, { props: { section: 'data' }, global })
    await flushPromises()
    expect(wrapper.findComponent({ name: 'AdminTaskDialog' }).props('row').id).toBe(5)
    expect(wrapper.find('a[href^="/teacher/"]').exists()).toBe(false)
  })
  it('opens resource tabs and visibility filters inside the administrator directory', async () => {
    mocks.route.query = { tab: 'concepts', concept_id: '9' }
    mocks.get.mockResolvedValue({ banks: [], questions: [], concepts: [{ id: 9, name: '概念' }] })
    const wrapper = mount(AdminCatalog, { props: { section: 'bank' }, global })
    await flushPromises()
    expect(wrapper.findComponent({ name: 'AdminConceptDialog' }).props('row').id).toBe(9)
  })
})
