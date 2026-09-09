import { describe, expect, it, vi } from 'vitest'

import { createStudentAssessmentApi } from './api'

describe('student assessment API', () => {
  it('loads the authoritative assignment and preserves legacy mutation URLs', async () => {
    const client = {
      get: vi.fn().mockResolvedValue({}),
      mutate: vi.fn().mockResolvedValue({}),
    }
    const api = createStudentAssessmentApi(client as never)

    await api.question(7, 10)
    await api.question(7, 11, 'navigation_click', {
      navigationId: '00000000-0000-4000-8000-000000000010',
      clientOccurredAt: '2026-08-30T08:00:00.000Z',
      clientMonotonicMs: 62422.300000000745,
      clientTimeOrigin: '2026-08-30T07:59:58.765Z',
    })
    await api.saveDraft('/student/attempts/13/draft/', 'B', '2026-08-30T08:00:00Z')
    await api.submit('/student/attempts/13/submit/', '00000000-0000-4000-8000-000000000001', '2026-08-30T08:00:01Z')
    await api.skip('/student/attempts/13/skip/')
    await api.post('/assessment/process/confidence/13/commit/', { probability: 80 })

    expect(client.get.mock.calls).toEqual([
      ['/api/v1/student/sessions/7/questions/10/?reason=direct_open'],
      [
        '/api/v1/student/sessions/7/questions/11/?reason=navigation_click&navigation_id=00000000-0000-4000-8000-000000000010&client_occurred_at=2026-08-30T08%3A00%3A00.000Z&client_monotonic_ms=62422.300&client_time_origin=2026-08-30T07%3A59%3A58.765Z',
      ],
    ])
    expect(client.mutate.mock.calls).toEqual([
      [
        '/student/attempts/13/draft/',
        {
          method: 'POST',
          body: { answer: 'B', client_occurred_at: '2026-08-30T08:00:00Z' },
        },
      ],
      [
        '/student/attempts/13/submit/',
        {
          method: 'POST',
          body: {
            submission_token: '00000000-0000-4000-8000-000000000001',
            client_clicked_at: '2026-08-30T08:00:01Z',
          },
        },
      ],
      ['/student/attempts/13/skip/', { method: 'POST' }],
      [
        '/assessment/process/confidence/13/commit/',
        { method: 'POST', body: { probability: 80 } },
      ],
    ])
  })
})
