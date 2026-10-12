import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ api: { exports: vi.fn(), createExport: vi.fn(), exportJob: vi.fn() } }))
vi.mock('vue-router', () => ({ useRoute: () => ({ path: '/teacher/courses/7/data/exports/', params: { courseId: '7' }, query: { experiment_id: '3', class_id: '4', search: '张同学', session_ref: 'S-001', package: 'experiment' } }) }))
vi.mock('./api', () => ({
  getTeacherDataCenterApi: () => mocks.api,
  routeQuery: (query: object) => query,
  queryString: () => '?package=experiment',
}))

import TeacherDataExports from './TeacherDataExports.vue'

const tables = [
  ['sessions', '会话', '01_课程学生.csv', 'course', 'csv'],
  ['session_bindings', '学生 Session 实验绑定', '02_学生Session实验绑定表.csv', 'experiment_binding', 'csv'],
  ['knowledge_points', '知识点', '03_知识点.csv', 'course', 'csv'],
  ['question_kc_mapping', '题目知识点映射', '04_题目与知识点映射.csv', 'course', 'csv'],
  ['question_library', '课程题目固定库', '课程题目固定库.json', 'course', 'json'],
  ['responses', '逐题作答', '05_逐题作答.csv', 'experiment', 'csv'],
  ['behavior_events', '行为过程', '06_行为过程.csv', 'experiment', 'csv'],
  ['event_dictionary', '事件字典', '07_事件字典.csv', 'dictionary', 'csv'],
].map(([key, label, path, level, format]) => ({ key, label, path, level, format, description: `${label}说明`, total_items: 1 }))

describe('TeacherDataExports', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.api.exports.mockResolvedValue({
    course: { id: 7, name: '研究方法', code: 'KT101', term: '2026 秋', status: 'active' },
    scope: {
      values: { scope_version: 2, experiment_id: 3, class_mode: 'single', class_id: 4 },
      query: 'scope_version=2&experiment_id=3&class_mode=single&class_id=4',
      display: '数据实验 · 实验班', is_single_version: true, is_complete: true,
    },
    packages: [{ key: 'experiment', label: '标准实验数据包（ZIP）', description: '六张研究数据表', blocked: false }],
    selected_package: { key: 'experiment', label: '标准实验数据包（ZIP）', description: '六张研究数据表', blocked: false },
    export_versions: [],
    selected_export_version: null,
    identified_data_notice: '数据包包含学生姓名、学号和可识别作答记录。',
    tables,
    experiments: [{ id: 3, name: '数据实验' }, { id: 5, name: '第二实验' }],
    class_choices: [{ id: 4, name: '实验班', academic_year: '2026' }],
    preparation: { state: 'ready', ready_count: 1, pending_count: 0 },
    generated_at: '2026-09-09T00:00:00Z',
    actions: { create_url: '/api/v1/teacher/courses/7/data/export-jobs/', reset_url: '/teacher/courses/7/data/exports/' },
    })
  })

  afterEach(() => vi.useRealTimers())

  it('groups the exact seven CSV files and fixed question-library JSON, then creates the package', async () => {
    mocks.api.createExport.mockResolvedValue({
      job_id: 'job-1', package: 'experiment', status: 'queued', status_label: '排队中',
      download_url: null, history_url: '/teacher/courses/7/data/exports/history/',
    })
    const wrapper = mount(TeacherDataExports)
    await flushPromises()

    expect(wrapper.get('h1').text()).toBe('导出研究数据')
    expect(wrapper.text()).toContain('标准实验数据包（ZIP）')
    expect(wrapper.findAll('[data-testid="export-table"]')).toHaveLength(8)
    expect(wrapper.text()).toContain('课程基础资料')
    expect(wrapper.text()).toContain('实验原始记录')
    expect(wrapper.text()).toContain('事件说明')
    expect(wrapper.text()).toContain('课程题目固定库.json')
    expect(wrapper.text()).toContain('表2覆盖当前实验全部已发布班级')
    expect(wrapper.text()).toContain('数据包包含学生姓名')
    expect(wrapper.find('.kt-export-scope').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Version')
    expect(wrapper.get('.kt-export-confirm button').text()).toBe('导出实验数据包')

    await wrapper.get('.kt-export-confirm form').trigger('submit')
    await flushPromises()
    expect(mocks.api.createExport).toHaveBeenCalledWith(7, {
      package: 'experiment', confirm_identified: true,
      scope: { scope_version: 2, experiment_id: 3, class_mode: 'single', class_id: 4 },
    })
    expect(wrapper.text()).toContain('排队中')
    expect(wrapper.text()).not.toContain('下载文件')
  })

  it('supports one combined export for all published classes', async () => {
    mocks.api.createExport.mockResolvedValue({
      job_id: 'job-all', package: 'experiment', status: 'queued', status_label: '排队中',
      download_url: null, history_url: '/teacher/courses/7/data/exports/history/',
    })
    const firstWrapper = mount(TeacherDataExports)
    await flushPromises()
    const initialCatalog = await mocks.api.exports.mock.results[0]?.value
    firstWrapper.unmount()
    mocks.api.exports.mockReset()
    mocks.api.exports.mockResolvedValue({
      ...initialCatalog,
      scope: {
        ...initialCatalog.scope,
        values: { scope_version: 2, experiment_id: 3, class_mode: 'all' },
        query: 'scope_version=2&experiment_id=3&class_mode=all',
        display: '数据实验 · 全部已发布班级',
      },
    })
    const wrapper = mount(TeacherDataExports)
    await flushPromises()
    await wrapper.get('.kt-export-confirm form').trigger('submit')
    await flushPromises()

    expect(mocks.api.createExport).toHaveBeenCalledWith(7, {
      package: 'experiment', confirm_identified: true,
      scope: { scope_version: 2, experiment_id: 3, class_mode: 'all' },
    })
  })

  it('blocks incomplete exports and stops polling after success', async () => {
    vi.useFakeTimers()
    mocks.api.createExport.mockResolvedValue({
      job_id: 'job-1', package: 'experiment', status: 'queued', status_label: '排队中',
      download_url: null, history_url: '/teacher/courses/7/data/exports/history/',
    })
    mocks.api.exportJob.mockResolvedValue({
      job_id: 'job-1', package: 'experiment', status: 'succeeded', status_label: '已完成',
      download_url: '/download/', history_url: '/history/',
    })
    const wrapper = mount(TeacherDataExports)
    await flushPromises()
    await wrapper.get('.kt-export-confirm form').trigger('submit')
    await flushPromises()
    await vi.runOnlyPendingTimersAsync()
    await flushPromises()

    expect(mocks.api.exportJob).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('已完成')
    expect(vi.getTimerCount()).toBe(0)
  })

  it('does not reschedule an in-flight poll after the page unmounts', async () => {
    vi.useFakeTimers()
    const queued = {
      job_id: 'job-1', package: 'experiment', status: 'queued', status_label: '排队中',
      download_url: null, history_url: '/history/',
    }
    mocks.api.createExport.mockResolvedValue(queued)
    let resolvePoll!: (value: typeof queued) => void
    mocks.api.exportJob.mockReturnValue(new Promise(resolve => { resolvePoll = resolve }))
    const wrapper = mount(TeacherDataExports)
    await flushPromises()
    await wrapper.get('.kt-export-confirm form').trigger('submit')
    await flushPromises()

    vi.advanceTimersByTime(5000)
    await flushPromises()
    expect(mocks.api.exportJob).toHaveBeenCalledTimes(1)
    wrapper.unmount()
    resolvePoll(queued)
    await flushPromises()

    expect(vi.getTimerCount()).toBe(0)
  })
  it('checks queued jobs only three times at 5/15/30 seconds and allows manual checks', async () => {
    vi.useFakeTimers()
    const queued = { job_id: 'bounded', package: 'experiment', status: 'queued', status_label: '排队中', download_url: null, history_url: '/history/' }
    mocks.api.createExport.mockResolvedValue(queued)
    mocks.api.exportJob.mockResolvedValue(queued)
    const wrapper = mount(TeacherDataExports)
    await flushPromises()
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(5000)
    expect(mocks.api.exportJob).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(10000)
    expect(mocks.api.exportJob).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(15000)
    expect(mocks.api.exportJob).toHaveBeenCalledTimes(3)
    await vi.advanceTimersByTimeAsync(120000)
    expect(mocks.api.exportJob).toHaveBeenCalledTimes(3)
    await wrapper.get('[data-testid="check-export-status"]').trigger('click')
    await flushPromises()
    expect(mocks.api.exportJob).toHaveBeenCalledTimes(4)
    wrapper.unmount()
  })

  it('merges duplicate submits and stops checks permanently on hidden', async () => {
    vi.useFakeTimers()
    const queued = { job_id: 'merged', package: 'experiment', status: 'queued', status_label: '排队中', download_url: null, history_url: '/history/', reused: true }
    let resolve!: (value: typeof queued) => void
    mocks.api.createExport.mockReturnValue(new Promise(done => { resolve = done }))
    const wrapper = mount(TeacherDataExports)
    await flushPromises()
    await wrapper.get('form').trigger('submit')
    await wrapper.get('form').trigger('submit')
    expect(mocks.api.createExport).toHaveBeenCalledTimes(1)
    resolve(queued)
    await flushPromises()
    expect(wrapper.text()).toContain('复用已有任务')
    const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
    document.dispatchEvent(new Event('visibilitychange'))
    visibility.mockReturnValue('visible')
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.advanceTimersByTimeAsync(120000)
    expect(mocks.api.exportJob).not.toHaveBeenCalled()
    wrapper.unmount()
    visibility.mockRestore()
  })

})
