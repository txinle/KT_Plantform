import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ api: { quality: vi.fn() }, push: vi.fn() }))
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { courseId: '7' }, query: { experiment_id: '3', class_id: '4', search: '张同学', session_ref: 'S-001' } }),
  useRouter: () => ({ push: mocks.push }),
}))
vi.mock('./api', () => ({ getTeacherDataCenterApi: () => mocks.api, routeQuery: (query: object) => query }))

import TeacherDataQuality from './TeacherDataQuality.vue'

describe('TeacherDataQuality', () => {
  beforeEach(() => mocks.api.quality.mockResolvedValue({
    course: { id: 7, name: '研究方法', code: 'KT101', term: '2026 秋', status: 'active' },
    scope: { values: {}, query: '', display: '数据实验 · 实验班', is_single_version: false, is_complete: true },
    counts: { '可用': 2, '需要核查': 1 },
    items: [{
      student_number: 'DATA-001', student_name: '张同学', session_ref: 'S-001', question_code: 'Q-01',
      state: 'warning', state_label: '需要核查', problem: '行为事件时间不完整', impact: '无法可靠比较步骤间隔',
    }],
    page: { number: 1, size: 20, total_items: 1, total_pages: 1, start: 1, end: 1 },
  }))

  it('shows readable Chinese quality findings without internal rule codes', async () => {
    const wrapper = mount(TeacherDataQuality)
    await flushPromises()

    expect(wrapper.text()).toContain('可用')
    expect(wrapper.text()).toContain('需要核查')
    expect(wrapper.text()).toContain('张同学')
    expect(wrapper.text()).toContain('S-001')
    expect(wrapper.text()).toContain('行为事件时间不完整')
    expect(wrapper.text()).toContain('无法可靠比较步骤间隔')
    expect(wrapper.text()).not.toContain('warning')
  })
})
