import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ api: { exports: vi.fn(), createExport: vi.fn() } }))
vi.mock('vue-router', () => ({ useRoute: () => ({ path: '/teacher/courses/7/data/exports/', params: { courseId: '7' }, query: { experiment_id: '3', class_id: '4', search: '张同学', session_ref: 'S-001', package: 'experiment' } }) }))
vi.mock('./api', () => ({
  getTeacherDataCenterApi: () => mocks.api,
  routeQuery: (query: object) => query,
  queryString: () => '?package=experiment',
}))

import TeacherDataExports from './TeacherDataExports.vue'

const tables = [
  ['sessions', '会话', '01_课程学生.csv', 'course', 'csv'],
  ['bindings', '全局 Session 绑定', '02_全局Session绑定.csv', 'course', 'csv'],
  ['knowledge_points', '知识点', '03_知识点.csv', 'course', 'csv'],
  ['question_kc_mapping', '题目知识点映射', '04_题目与知识点映射.csv', 'course', 'csv'],
  ['question_library', '课程题目固定库', '课程题目固定库.json', 'course', 'json'],
  ['responses', '逐题作答', '05_逐题作答.csv', 'experiment', 'csv'],
  ['behavior_events', '行为过程', '06_行为过程.csv', 'experiment', 'csv'],
  ['event_dictionary', '事件字典', '07_事件字典.csv', 'dictionary', 'csv'],
].map(([key, label, path, level, format]) => ({ key, label, path, level, format, description: `${label}说明`, total_items: 1 }))

describe('TeacherDataExports', () => {
  beforeEach(() => mocks.api.exports.mockResolvedValue({
    course: { id: 7, name: '研究方法', code: 'KT101', term: '2026 秋', status: 'active' },
    scope: {
      values: { experiment_id: 3, class_id: 4, search: '张同学', session_ref: 'S-001' },
      query: 'experiment_id=3&class_id=4&search=%E5%BC%A0%E5%90%8C%E5%AD%A6&session_ref=S-001',
      display: '数据实验 · 实验班 · 张同学 · S-001', is_single_version: false, is_complete: true,
    },
    packages: [{ key: 'experiment', label: '标准实验数据包（ZIP）', description: '六张研究数据表', blocked: false }],
    selected_package: { key: 'experiment', label: '标准实验数据包（ZIP）', description: '六张研究数据表', blocked: false },
    export_versions: [],
    selected_export_version: null,
    identified_data_notice: '数据包包含学生姓名、学号和可识别作答记录。',
    tables,
    generated_at: '2026-09-09T00:00:00Z',
    actions: { create_url: '/api/v1/teacher/courses/7/data/export-jobs/', reset_url: '/teacher/courses/7/data/exports/' },
  }))

  it('groups the exact seven CSV files and fixed question-library JSON, then creates the package', async () => {
    mocks.api.createExport.mockResolvedValue({
      job_id: 'job-1', package: 'experiment', status: 'succeeded', status_label: '已完成',
      download_url: '/analytics/courses/7/export-jobs/job-1/download/', history_url: '/teacher/courses/7/data/exports/history/',
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
    expect(wrapper.text()).toContain('学生和会话筛选仅作用于逐题作答与行为过程')
    expect(wrapper.text()).toContain('数据包包含学生姓名')
    expect(wrapper.text()).not.toContain('Version')
    expect(wrapper.get('.kt-export-confirm button').text()).toBe('导出实验数据包')

    await wrapper.get('.kt-export-confirm form').trigger('submit')
    await flushPromises()
    expect(mocks.api.createExport).toHaveBeenCalledWith(7, {
      package: 'experiment', confirm_identified: true,
      scope: { experiment_id: 3, class_id: 4, search: '张同学', session_ref: 'S-001' },
    })
    expect(wrapper.text()).toContain('已完成')
  })
})
