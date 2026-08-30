import { describe, expect, it, vi } from 'vitest'

import { createStudentCourseApi } from './api'

describe('student course API', () => {
  it('uses task-centered start, resume, and result endpoints', async () => {
    const client = {
      get: vi.fn().mockResolvedValue({}),
      mutate: vi.fn().mockResolvedValue({}),
    }
    const api = createStudentCourseApi(client as never)

    await api.courses()
    await api.tasks(7)
    await api.learning(7)
    await api.learningExperiment(7, 11)
    await api.learningQuestion(7, 11, 13)
    await api.result(19)
    await api.start(19)
    await api.resume(19)

    expect(client.get.mock.calls.map(([path]) => path)).toEqual([
      '/api/v1/student/courses/',
      '/api/v1/student/courses/7/tasks/',
      '/api/v1/student/courses/7/learning/',
      '/api/v1/student/courses/7/learning/experiments/11/',
      '/api/v1/student/courses/7/learning/experiments/11/questions/13/',
      '/api/v1/student/tasks/19/results/',
    ])
    expect(client.mutate.mock.calls).toEqual([
      ['/api/v1/student/tasks/19/start/', { method: 'POST' }],
      ['/api/v1/student/tasks/19/resume/', { method: 'POST' }],
    ])
  })
})
