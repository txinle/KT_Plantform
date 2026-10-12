import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ teaching: vi.fn(), push: vi.fn() }))

vi.mock('vue-router', () => ({
  useRoute: () => ({
    params: { courseId: '7' },
    query: { experiment_id: '3', class_id: '4' },
  }),
  useRouter: () => ({ push: mocks.push }),
}))
vi.mock('./api', () => ({
  getTeacherTeachingDataApi: () => ({ teaching: mocks.teaching }),
  routeQuery: (value: Record<string, unknown>) => value,
}))

import TeacherTeachingData from './TeacherTeachingData.vue'

describe('TeacherTeachingData', () => {
  it('renders server-projected roster, session, and attempt facts', async () => {
    mocks.teaching.mockResolvedValue({
      blocked_message: '',
      filters: { q: '', student_id: 9, session_id: 12 },
      total_roster_count: 1,
      student_summaries: [{
        student_id: 9,
        student_number: 'S-009',
        display_name: '张同学',
        data_status: 'completed',
        data_status_label: '已完成',
        attempt_count: 2,
        submitted_count: 2,
        graded_count: 2,
        correct_rate: 100,
        score: '2.00',
        max_score: '2.00',
      }],
      sessions: [{
        id: 12,
        status: 'completed',
        status_label: '已完成',
        attempt_count: 2,
        submitted_count: 2,
        started_at: '2026-09-07T10:00:00Z',
      }],
      attempts: [{
        id: 21,
        question_code: 'Q-01',
        status: 'submitted',
        status_label: '已提交',
        grade_score: '1.00',
        timing: { display_duration_ms: 1200, display_label: '总历时' },
        timeline: [{
          event_id: 'event-1',
          event_type: 'response_state_changed',
          occurred_at: '2026-09-07T10:01:00Z',
          interval_quality: 'exact_monotonic',
        }],
        raw_events_url: '/teacher/courses/7/data/advanced/?table=assessment_events',
      }],
    })

    const wrapper = mount(TeacherTeachingData, {
      global: {
        stubs: {
          PageHeader: { template: '<header><slot /><slot name="kicker" /></header>' },
          Alert: { template: '<div><slot /></div>' },
          EmptyState: { template: '<div><slot /></div>' },
          StatusBadge: { props: ['label'], template: '<span>{{ label }}</span>' },
        },
      },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('学生数据')
    expect(wrapper.text()).toContain('S-009')
    expect(wrapper.text()).toContain('Session 12')
    expect(wrapper.text()).toContain('Q-01')
    expect(wrapper.text()).toContain('response_state_changed')
    expect(wrapper.get('a[href*="assessment_events"]').text()).toContain('查看原始事件')
  })
})
