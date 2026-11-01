import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
const mocks = vi.hoisted(() => ({ route: { path: '/teacher/courses/7/settings/', params: { courseId: '7' } }, adminGet: vi.fn(), api: { settings: vi.fn(), saveSettings: vi.fn(), archive: vi.fn(), restore: vi.fn(), createPurgeRequest: vi.fn(), executePurgeRequest: vi.fn() }, collaboration: { get: vi.fn(), search: vi.fn(), mutate: vi.fn() } }))
vi.mock('vue-router', () => ({ RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' }, useRoute: () => mocks.route }))
vi.mock('@/app/bootstrap', () => ({ getBootstrapContext: () => ({ csrf_token: 'csrf-token', user: { display_name: '李老师', username: 'teacher', home_url: '/teacher/courses/' } }) }))
vi.mock('@/features/admin-workspace/api', () => ({ getAdminApi: () => ({ get: mocks.adminGet }) }))
vi.mock('./api', () => ({ getTeacherCourseApi: () => mocks.api }))
vi.mock('./collaboration-api', () => ({ collaborationApi: mocks.collaboration }))
import TeacherCourseSettings from './TeacherCourseSettings.vue'
function payload() {
  return {
    course: { id: 7, name: '研究方法', code: 'RM101', term: '2026 秋', description: '研究设计课程', status: 'active', status_label: '进行中' },
    form: { name: '研究方法', code: 'RM101', term: '2026 秋', description: '研究设计课程', state_token: 'settings-1', teachers: [2], teacher_options: [] },
    archive_url: '/api/v1/teacher/courses/7/archive/', purge_requests: [], capabilities: { can_update: true, can_archive: true },
  }
}
function collaboration() {
  return { course: payload().course, owner: { id: 1, display_name: '负责人', username: 'owner' }, members: [{ id: 2, display_name: '李老师', username: 'teacher', permissions: ['bank'] }], permission_options: [{ key: 'bank', label: '题库与知识点' }, { key: 'class', label: '班级与学生' }, { key: 'experiment', label: '实验管理' }, { key: 'data', label: '数据查看' }, { key: 'export', label: '数据导出' }], granted_permissions: ['bank', 'class', 'experiment', 'data', 'export'], capabilities: { can_manage_members: true, can_transfer: true, can_update: true, can_archive: true }, state_token: 'collaboration-1' }
}
const mountPage = () => mount(TeacherCourseSettings, { global: { stubs: { SiteWorkspaceShell: { name: 'SiteWorkspaceShell', props: ['mode', 'active'], template: '<div><slot /></div>' }, SiteDialog: { props: ['open', 'title'], template: '<section v-if="open" role="dialog"><h2>{{ title }}</h2><slot /><slot name="footer" /></section>' } } } })
describe('course collaboration settings', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.route.path = '/teacher/courses/7/settings/'
    mocks.adminGet.mockResolvedValue({})
    mocks.api.settings.mockResolvedValue(payload())
    mocks.api.saveSettings.mockResolvedValue(payload())
    mocks.api.archive.mockResolvedValue(payload())
    mocks.collaboration.get.mockResolvedValue(collaboration())
    mocks.collaboration.mutate.mockResolvedValue(collaboration())
    mocks.collaboration.search.mockResolvedValue({ items: [{ id: 3, display_name: '王老师', username: 'wang' }] })
  })
  it('retains teacher navigation for the existing teacher route', async () => {
    const wrapper = mountPage(); await flushPromises()
    expect(wrapper.findComponent({ name: 'SiteWorkspaceShell' }).props()).toMatchObject({ mode: 'course', active: 'settings' })
    expect(mocks.adminGet).not.toHaveBeenCalled()
  })
  it('authorizes admin context and keeps governance within the admin shell', async () => {
    mocks.route.path = '/manage/courses/7/settings/'
    mocks.api.createPurgeRequest.mockResolvedValue(payload())
    const wrapper = mountPage(); await flushPromises()
    expect(mocks.adminGet).toHaveBeenCalledWith('access/')
    expect(wrapper.findComponent({ name: 'SiteWorkspaceShell' }).props()).toMatchObject({ mode: 'admin', active: 'course' })
    await wrapper.get('[data-purge-create]').trigger('click')
    await wrapper.findAll('button').find(button => button.text() === '生成影响预览')!.trigger('click'); await flushPromises()
    expect(mocks.api.createPurgeRequest).toHaveBeenCalledWith(7, 'research_withdrawal')
    expect(wrapper.findComponent({ name: 'SiteWorkspaceShell' }).props('mode')).toBe('admin')
  })
  it('does not load or show course forms when admin access is denied', async () => {
    mocks.route.path = '/manage/courses/7/settings/'
    mocks.adminGet.mockRejectedValue(new Error('只有管理员可以访问'))
    const wrapper = mountPage(); await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('只有管理员可以访问')
    expect(mocks.api.settings).not.toHaveBeenCalled()
    expect(mocks.collaboration.get).not.toHaveBeenCalled()
    expect(wrapper.find('[data-course-settings]').exists()).toBe(false)
  })
  it('keeps administrators on course settings after a successful owner transfer', async () => {
    mocks.route.path = '/manage/courses/7/settings/'
    mocks.collaboration.mutate.mockResolvedValue({ ...collaboration(), redirect_url: '/teacher/courses/' })
    const wrapper = mountPage(); await flushPromises()
    await wrapper.get('.transfer-owner').trigger('click')
    await wrapper.get('input[aria-label="搜索教师姓名或账号"]').setValue('wang')
    await wrapper.get('[data-search-teachers]').trigger('submit'); await flushPromises()
    await wrapper.get('[data-teacher-id="3"]').trigger('click')
    await wrapper.get('.transfer-reason textarea').setValue('教学安排变更')
    await wrapper.get('[data-save-collaborator]').trigger('click'); await flushPromises()
    expect(wrapper.text()).toContain('课程负责人已移交。')
    expect(wrapper.findComponent({ name: 'SiteWorkspaceShell' }).props('mode')).toBe('admin')
    expect(mocks.api.settings).toHaveBeenCalledTimes(2)
  })
  it('saves basics with the current token without rewriting collaboration membership', async () => {
    const wrapper = mountPage(); await flushPromises()
    await wrapper.get('input[name="name"]').setValue('高级研究方法')
    await wrapper.get('form[data-course-settings]').trigger('submit'); await flushPromises()
    expect(mocks.api.saveSettings).toHaveBeenCalledWith(7, { name: '高级研究方法', code: 'RM101', term: '2026 秋', description: '研究设计课程', state_token: 'settings-1', teachers: [2] })
    expect(mocks.collaboration.mutate).not.toHaveBeenCalled()
  })
  it('searches actual teachers and sends selected permissions with a concurrency token', async () => {
    const wrapper = mountPage(); await flushPromises()
    await wrapper.get('[data-add-collaborator]').trigger('click')
    expect(mocks.collaboration.search).not.toHaveBeenCalled()
    await wrapper.get('input[aria-label="搜索教师姓名或账号"]').setValue('wang')
    await wrapper.get('[data-search-teachers]').trigger('submit'); await flushPromises()
    await wrapper.get('[data-teacher-id="3"]').trigger('click')
    await wrapper.get('input[value="export"]').setValue(true)
    await wrapper.get('[data-save-collaborator]').trigger('click'); await flushPromises()
    expect(mocks.collaboration.mutate).toHaveBeenCalledWith(7, { action: 'add', teacher_id: 3, permissions: ['export', 'data'], state_token: 'collaboration-1' })
  })
  it('requires confirmation before removing a member or archiving a course', async () => {
    const wrapper = mountPage(); await flushPromises()
    await wrapper.get('[data-remove-collaborator="2"]').trigger('click')
    expect(mocks.collaboration.mutate).not.toHaveBeenCalled()
    await wrapper.get('[data-confirm-remove]').trigger('click'); await flushPromises()
    expect(mocks.collaboration.mutate).toHaveBeenCalledWith(7, { action: 'remove', teacher_id: 2, state_token: 'collaboration-1' })
    await wrapper.get('[data-course-archive]').trigger('click')
    expect(mocks.api.archive).not.toHaveBeenCalled()
    await wrapper.get('[data-confirm-archive]').trigger('click'); await flushPromises()
    expect(mocks.api.archive).toHaveBeenCalledWith(7)
  })
  it('renders a limited collaborator read-only according to server capabilities', async () => {
    mocks.api.settings.mockResolvedValue({ ...payload(), capabilities: { can_update: false, can_archive: false } })
    mocks.collaboration.get.mockResolvedValue({ ...collaboration(), capabilities: { can_manage_members: false, can_transfer: false, can_update: false, can_archive: false }, granted_permissions: ['bank'] })
    const wrapper = mountPage(); await flushPromises()
    expect(wrapper.get('input[name="name"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-add-collaborator]').exists()).toBe(false)
    expect(wrapper.find('[data-remove-collaborator]').exists()).toBe(false)
    expect(wrapper.find('[data-course-archive]').exists()).toBe(false)
    expect(wrapper.text()).toContain('题库与知识点')
  })
})
