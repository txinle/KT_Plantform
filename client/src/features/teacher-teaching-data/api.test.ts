import { describe, expect, it, vi } from 'vitest'

import { createTeacherTeachingDataApi } from './api'

describe('teacher teaching-data API', () => {
  it('keeps legacy teaching filters on the same-origin adapter endpoint', async () => {
    const client = { get: vi.fn().mockResolvedValue({}) }
    const api = createTeacherTeachingDataApi(client)

    await api.teaching(7, {
      experiment_id: 3,
      class_id: 4,
      q: '张 同学',
      student_id: 9,
      session_id: 12,
    })

    expect(client.get).toHaveBeenCalledWith(
      '/api/v1/teacher/courses/7/data/teaching/?experiment_id=3&class_id=4&q=%E5%BC%A0+%E5%90%8C%E5%AD%A6&student_id=9&session_id=12',
    )
  })
})
