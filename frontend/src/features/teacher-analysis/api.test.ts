import { describe, expect, it, vi } from 'vitest'

import { createTeacherAnalysisApi } from './api'


describe('teacher analysis API paths', () => {
  it('uses the class-scoped frozen fact endpoints', async () => {
    const client = { get: vi.fn().mockResolvedValue({}) }
    const api = createTeacherAnalysisApi(client as never)

    await api.studentDetail(7, 11, 23)
    await api.studentExperimentDetail(7, 11, 23, 41)
    await api.questionDetail(7, 11, 41, 59)
    await api.attemptProcess(7, 11, 41, 59, 23)
    await api.experimentResultsResolution(7, 41, 'completed')
    await api.experimentSessionResolution(7, 41, 88)

    expect(client.get).toHaveBeenNthCalledWith(
      1,
      '/api/v1/teacher/courses/7/classes/11/students/23/analysis/',
    )
    expect(client.get).toHaveBeenNthCalledWith(
      2,
      '/api/v1/teacher/courses/7/classes/11/students/23/experiments/41/',
    )
    expect(client.get).toHaveBeenNthCalledWith(
      3,
      '/api/v1/teacher/courses/7/classes/11/experiments/41/questions/59/',
    )
    expect(client.get).toHaveBeenNthCalledWith(
      4,
      '/api/v1/teacher/courses/7/classes/11/experiments/41/questions/59/students/23/process/',
    )
    expect(client.get).toHaveBeenNthCalledWith(
      5,
      '/api/v1/teacher/courses/7/experiments/41/results/resolve/?status=completed',
    )
    expect(client.get).toHaveBeenNthCalledWith(
      6,
      '/api/v1/teacher/courses/7/experiments/41/sessions/88/resolve/',
    )
  })
})
