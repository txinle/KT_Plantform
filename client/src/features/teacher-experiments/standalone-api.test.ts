import { describe, expect, it, vi } from 'vitest'

import { createStandaloneExperimentApi } from './standalone-api'

describe('standalone experiment API paths', () => {
  it('uses the same-origin compatibility endpoints for legacy experiment actions', async () => {
    const client = {
      get: vi.fn().mockResolvedValue({}),
      mutate: vi.fn().mockResolvedValue({}),
    }
    const api = createStandaloneExperimentApi(client as never)

    await api.collection()
    await api.detail(17, 'edit')
    await api.assignStudents(17, [3, 4])
    await api.publish(17)
    await api.results(17, 'completed')

    expect(client.get).toHaveBeenNthCalledWith(1, '/api/v1/teacher/standalone-experiments/')
    expect(client.get).toHaveBeenNthCalledWith(
      2,
      '/api/v1/teacher/standalone-experiments/17/?destination=edit',
    )
    expect(client.mutate).toHaveBeenNthCalledWith(
      1,
      '/api/v1/teacher/standalone-experiments/17/students/',
      { method: 'POST', body: { student_ids: [3, 4] } },
    )
    expect(client.mutate).toHaveBeenNthCalledWith(
      2,
      '/api/v1/teacher/standalone-experiments/17/publish/',
      { method: 'POST' },
    )
    expect(client.get).toHaveBeenNthCalledWith(
      3,
      '/api/v1/teacher/standalone-experiments/17/results/?status=completed',
    )
  })
})
