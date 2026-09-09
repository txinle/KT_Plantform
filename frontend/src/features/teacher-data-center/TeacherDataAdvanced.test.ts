import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ api: { advanced: vi.fn() }, push: vi.fn() }))
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { courseId: '7' }, query: { experiment_id: '3', table: 'assessment_events' } }),
  useRouter: () => ({ push: mocks.push }),
}))
vi.mock('./api', () => ({ getTeacherDataCenterApi: () => mocks.api, routeQuery: (query: object) => query }))

import TeacherDataAdvanced from './TeacherDataAdvanced.vue'

const response = {
  course: { id: 7, name: '研究方法', code: 'KT101', term: '2026 秋', status: 'active' },
  scope: { values: { experiment_id: 3 }, query: 'experiment_id=3', display: '数据实验', is_single_version: false },
  search: '',
  groups: [{
    key: 'behavior_process', label: '行为过程', description: '原始事件', active: true,
    tables: [{ key: 'assessment_events', label: '权威总事件表', path: '03_raw_events/assessment_events.csv', role: 'source_of_truth', role_label: '权威总事件表 · Source of Truth', description: '不可变总表', schema_version: 'v2', source_layer: '原始科研观测', active: true }],
  }],
  selected_table: { key: 'assessment_events', label: '权威总事件表', path: '03_raw_events/assessment_events.csv', role: 'source_of_truth', role_label: '权威总事件表 · Source of Truth', description: '不可变总表', schema_version: 'v2', source_layer: '原始科研观测', active: true },
  selected_experiment: { id: 3, name: '数据实验', versions: [{ id: 8, version_number: 1 }] },
  preview: {
    columns: [
      { name: 'student_number', description: '平台学生学号' },
      { name: 'student_name', description: '学生姓名' },
      { name: 'question_code', description: '题库唯一编号' },
      { name: 'attempt_id', description: '作答记录唯一标识' },
      { name: 'event_type', description: '事件类型' },
      { name: 'server_received_at', description: '服务端接收时间' },
    ],
    rows: [['DATA-001', '张同学', 'DATA-Q1', 918, 'item_submitted', '2026-09-09T00:00:00Z']],
    page: { number: 1, size: 20, total_items: 1, total_pages: 1, start: 1, end: 1 },
    data_status: 'observed',
  },
}

describe('TeacherDataAdvanced', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.api.advanced.mockResolvedValue(response)
  })

  it('keeps the research catalog while presenting a readable student-question-process row', async () => {
    const wrapper = mount(TeacherDataAdvanced)
    await flushPromises()

    expect(wrapper.get('h1').text()).toBe('高级数据工具')
    expect(wrapper.text()).toContain('Source of Truth')
    expect(wrapper.text()).toContain('03_raw_events/assessment_events.csv')
    const preview = wrapper.get('[data-testid="readable-preview"]')
    expect(preview.text()).toContain('张同学')
    expect(preview.text()).toContain('DATA-001')
    expect(preview.text()).toContain('DATA-Q1')
    expect(preview.text()).toContain('item_submitted')
    expect(preview.text()).not.toContain('attempt_id')
    expect(preview.text()).not.toContain('918')

    await preview.get('button').trigger('click')
    const detail = wrapper.get('[role="dialog"]')
    expect(detail.text()).toContain('作答记录唯一标识')
    expect(detail.text()).toContain('918')
  })

  it('submits the student name search to the authoritative preview endpoint', async () => {
    const wrapper = mount(TeacherDataAdvanced)
    await flushPromises()

    await wrapper.get('input[aria-label="按学生姓名或学号搜索"]').setValue('张同学')
    await wrapper.get('form[role="search"]').trigger('submit')
    await flushPromises()

    expect(mocks.push).toHaveBeenCalledWith({
      query: { experiment_id: '3', table: 'assessment_events', search: '张同学', page: undefined },
    })
    expect(mocks.api.advanced).toHaveBeenLastCalledWith(7, {
      experiment_id: '3', table: 'assessment_events', search: '张同学', page: undefined,
    })
  })
})
