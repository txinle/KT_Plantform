import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ api: { advanced: vi.fn(), studentPreview: vi.fn() }, push: vi.fn() }))
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { courseId: '7' }, query: { experiment_id: '3', class_id: '4' } }),
  useRouter: () => ({ push: mocks.push }),
}))
vi.mock('./api', () => ({ getTeacherDataCenterApi: () => mocks.api, routeQuery: (query: object) => query }))

import TeacherDataAdvanced from './TeacherDataAdvanced.vue'

describe('TeacherDataAdvanced', () => {
  beforeEach(() => { vi.clearAllMocks(); mocks.api.advanced.mockResolvedValue({
    course: { id: 7, name: '研究方法', code: 'KT101', term: '2026 秋', status: 'active' },
    scope: { experiment_id: 3, class_id: 4, is_complete: true, display: '数据实验 · 实验班' },
    search: '',
    students: [{ student_number: 'DATA-001', display_name: '张同学', class_name: '实验班', experiment_count: 1, progress_label: '3 / 4', status_label: '进行中', detail_url: '/teacher/courses/7/classes/4/students/9/experiments/3/', detail_api_url: '/api/v1/teacher/courses/7/classes/4/students/9/experiments/3/' }],
    page: { number: 1, size: 20, total_items: 1, total_pages: 1, start: 1, end: 1 },
  }) })

  it('shows one business student row without technical identifiers', async () => {
    const wrapper = mount(TeacherDataAdvanced)
    await flushPromises()
    expect(wrapper.get('h1').text()).toBe('学生作答预览')
    expect(wrapper.text()).toContain('DATA-001')
    expect(wrapper.text()).toContain('张同学')
    expect(wrapper.text()).toContain('3 / 4')
    expect(wrapper.get('a[href="/teacher/courses/7/classes/4/students/9/experiments/3/"]').text()).toContain('查看详情')
    for (const forbidden of ['Schema', 'Source of Truth', 'event_id', 'attempt_id', 'snapshot', 'hash']) {
      expect(wrapper.text()).not.toContain(forbidden)
    }
  })

  it('opens submitted questions on demand and restores the filtered list when closed', async () => {
    mocks.api.studentPreview.mockResolvedValue({ detail: { display_name: '张同学', student_number: 'DATA-001', experiment_name: '数据实验', questions: [
      { code: 'Q1', final_answer: 'B', result_label: '正确', score_display: '1 / 1', revision_count: 2, answer_time_display: '8 秒', confidence_label: '80%', submission_status_label: '已正式提交', submitted_at: '2026-09-09T00:00:00Z', process_url: '/teacher/process/' },
    ] } })
    const wrapper = mount(TeacherDataAdvanced)
    await flushPromises()
    expect(mocks.api.studentPreview).not.toHaveBeenCalled()
    await wrapper.get('input[type="search"]').setValue('DATA-001')
    await wrapper.get('a').trigger('click')
    await flushPromises()
    expect(mocks.api.studentPreview).toHaveBeenCalledWith('/api/v1/teacher/courses/7/classes/4/students/9/experiments/3/')
    expect(wrapper.get('[role="dialog"]').text()).toContain('80%')
    expect(wrapper.get('[role="dialog"]').findAll('tbody tr')).toHaveLength(1)
    await wrapper.get('[role="dialog"] > button').trigger('click')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(wrapper.get<HTMLInputElement>('input[type="search"]').element.value).toBe('DATA-001')
    expect(mocks.push).not.toHaveBeenCalled()
  })
})
