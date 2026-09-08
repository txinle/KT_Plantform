import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ attemptProcess: vi.fn() }))
vi.mock('vue-router', () => ({ useRoute: () => ({ params: { courseId: '1', classId: '2', experimentId: '3', questionId: '4', studentId: '5' } }) }))
vi.mock('./api', () => ({ getTeacherAnalysisApi: () => mocks }))

import TeacherAttemptProcess from './TeacherAttemptProcess.vue'

describe('TeacherAttemptProcess', () => {
  beforeEach(() => { mocks.attemptProcess.mockResolvedValue({
    actions: { back_url: '/back/' },
    detail: {
      generated_at: '2026-09-08T10:00:00Z', experiment_name: '实验一', question_code: 'Q1', question_text: '题干', student_number: 'S001', display_name: '张同学', is_current_member: true, result_label: '正确', score_display: '1 / 1',
      coverage_status_title: '记录完整', coverage_status_description: '可验证', coverage_time_title: '时间可用', coverage_time_description: '同一时钟', coverage_reason_labels: [], has_cross_instance_order_uncertainty: false,
      process: { question_type: 'short_answer', completeness: 'complete', completeness_label: '完整链', final_value: 'A', steps: [
        { label: '进入题目', occurred_at: '2026-09-08T10:00:01Z', value: null, value_before: null, value_after: null, length_before: null, length_after: null, time_source: '服务端记录时间', interval_ms: null },
        { label: '修改答案', occurred_at: '2026-09-08T10:00:02Z', value: 'A', value_before: 'B', value_after: 'A', length_before: null, length_after: null, time_source: '同一客户端时间', interval_ms: 800 },
      ] },
    },
  }) })

  it('renders numbered timeline rows, right-side time, duration, and full before-after values', async () => {
    const wrapper = mount(TeacherAttemptProcess)
    await flushPromises()
    expect(wrapper.findAll('.kt-process-node')).toHaveLength(2)
    expect(wrapper.findAll('.kt-process-node')[0].text()).toBe('1')
    expect(wrapper.get('.kt-process-time').text()).toContain('2026')
    expect(wrapper.get('.kt-process-duration').text()).toContain('800 ms')
    expect(wrapper.text()).toContain('修改前：B')
    expect(wrapper.text()).toContain('修改后：A')
    expect(wrapper.get('.kt-analysis-final-answer').text()).toBe('A')
  })

  it('does not expose technical identifiers from failed requests', async () => {
    mocks.attemptProcess.mockRejectedValue(new Error('attempt_id=93428 event_id=sentinel-technical-value'))
    const wrapper = mount(TeacherAttemptProcess)
    await flushPromises()
    expect(wrapper.text()).not.toContain('sentinel-technical-value')
    expect(wrapper.text()).not.toContain('attempt_id')
    expect(wrapper.text()).toContain('作答过程暂时无法加载')
  })
})
