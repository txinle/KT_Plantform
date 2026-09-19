import { describe, expect, it, vi } from 'vitest'

import { createTeacherExperimentApi } from './api'

describe('teacher experiment API paths', () => {
  it('builds collection participant filters with the typed client', async () => {
    const client = {
      get: vi.fn().mockResolvedValue({}),
      mutate: vi.fn().mockResolvedValue({}),
    }
    const api = createTeacherExperimentApi(client as never)

    await api.collectionParticipants(11, { classId: 5, status: 'expired', query: ' S019 ', page: 2 })

    expect(client.get).toHaveBeenCalledWith(
      '/api/v1/teacher/experiments/11/collection-participants/?class_id=5&status=expired&q=S019&page=2',
    )
  })

  it('omits query parameters for empty collection participant filters on page one', async () => {
    const client = {
      get: vi.fn().mockResolvedValue({}),
      mutate: vi.fn().mockResolvedValue({}),
    }
    const api = createTeacherExperimentApi(client as never)

    await api.collectionParticipants(11, { page: 1 })

    expect(client.get).toHaveBeenCalledWith(
      '/api/v1/teacher/experiments/11/collection-participants/',
    )
  })

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
    await api.grading(17)
    await api.gradingQuestion(17, 41, 'pending', 2)
    await api.gradeSubmission(17, 41, 83, {
      expected_grade_version: 1,
      is_correct: true,
      comment: '',
    })

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
    expect(client.get).toHaveBeenCalledWith('/api/v1/teacher/class-tasks/17/grading/')
    expect(client.get).toHaveBeenCalledWith(
      '/api/v1/teacher/class-tasks/17/grading/questions/41/?status=pending&page=2',
    )
    expect(client.mutate).toHaveBeenNthCalledWith(
      5,
      '/api/v1/teacher/class-tasks/17/grading/submissions/83/',
      expect.objectContaining({
        method: 'POST',
        body: { expected_grade_version: 1, is_correct: true, comment: '' },
      }),
    )
  })
})
