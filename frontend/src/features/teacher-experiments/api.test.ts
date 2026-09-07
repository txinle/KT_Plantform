import { describe, expect, it, vi } from 'vitest'

import { createTeacherExperimentApi } from './api'

describe('teacher experiment API paths', () => {
  it('uses the frozen same-origin experiment and publishing endpoints', async () => {
    const client = {
      get: vi.fn().mockResolvedValue({}),
      mutate: vi.fn().mockResolvedValue({}),
    }
    const api = createTeacherExperimentApi(client as never)

    await api.list(7, 2)
    await api.startPlan(7, 11)
    await api.saveSetup(13, {
      expected_revision: 1,
      classes: [],
      default_schedule: { opens_at: null, closes_at: null },
      schedules: [],
    })
    await api.manualClose(17, '结束')
    await api.releaseFeedback(17)

    expect(client.get).toHaveBeenCalledWith('/api/v1/teacher/courses/7/experiments/?page=2')
    expect(client.mutate).toHaveBeenNthCalledWith(
      1,
      '/api/v1/teacher/courses/7/experiments/11/publish-plans/',
      { method: 'POST' },
    )
    expect(client.mutate).toHaveBeenNthCalledWith(
      3,
      '/api/v1/teacher/class-tasks/17/manual-close/',
      expect.objectContaining({ method: 'POST', body: { reason: '结束' } }),
    )
    expect(client.mutate).toHaveBeenNthCalledWith(
      4,
      '/api/v1/teacher/class-tasks/17/feedback-release/',
      expect.objectContaining({ method: 'POST', body: {} }),
    )
  })
})
