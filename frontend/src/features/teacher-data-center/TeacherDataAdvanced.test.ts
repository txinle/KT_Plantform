import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ api: { advanced: vi.fn() }, push: vi.fn() }))
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { courseId: '7' }, query: { experiment_id: '3', table: 'assessment_events' } }),
  useRouter: () => ({ push: mocks.push }),
}))
vi.mock('./api', () => ({ getTeacherDataCenterApi: () => mocks.api, routeQuery: (query: object) => query }))

import TeacherDataAdvanced from './TeacherDataAdvanced.vue'

describe('TeacherDataAdvanced', () => {
  beforeEach(() => mocks.api.advanced.mockResolvedValue({
    course: { id: 7, name: '研究方法', code: 'KT101', term: '2026 秋', status: 'active' },
    scope: { values: { experiment_id: 3 }, query: 'experiment_id=3', display: '数据实验', is_single_version: false },
    groups: [{ key: 'behavior_process', label: '行为过程', description: '原始事件', active: true, tables: [{ key: 'assessment_events', label: '权威总事件表', path: '03_raw_events/assessment_events.csv', role: 'source_of_truth', role_label: '权威总事件表 · Source of Truth', description: '不可变总表', schema_version: 'v2', source_layer: '原始科研观测', active: true }] }],
    selected_table: { key: 'assessment_events', label: '权威总事件表', path: '03_raw_events/assessment_events.csv', role: 'source_of_truth', role_label: '权威总事件表 · Source of Truth', description: '不可变总表', schema_version: 'v2', source_layer: '原始科研观测', active: true },
    selected_experiment: { id: 3, name: '数据实验', versions: [{ id: 8, version_number: 1 }] },
    preview: { columns: [{ name: 'event_id', description: '事件标识' }], rows: [['EV-1']], page: { number: 1, size: 20, total_items: 1, total_pages: 1, start: 1, end: 1 }, data_status: 'observed' },
  }))

  it('distinguishes the event source of truth from catalog navigation', async () => {
    const wrapper = mount(TeacherDataAdvanced)
    await flushPromises()
    expect(wrapper.get('h1').text()).toBe('高级数据工具')
    expect(wrapper.text()).toContain('Source of Truth')
    expect(wrapper.text()).toContain('03_raw_events/assessment_events.csv')
    expect(wrapper.text()).toContain('EV-1')
  })
})
