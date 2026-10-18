import { describe, expect, it, vi } from 'vitest'

import { createTeacherDeletionApi } from './api'

describe('teacher deletion API paths', () => {
  it('uses distinct preview and confirmation endpoints for global experiments, class experiments, and classes', async () => {
    const client = {
      get: vi.fn().mockResolvedValue({}),
      mutate: vi.fn().mockResolvedValue({}),
    }
    const api = createTeacherDeletionApi(client as never)

    await api.previewExperiment(7, 13)
    await api.deleteExperiment(7, 13, 'experiment-token', 'operation-1')
    await api.previewClassExperiment(7, 11, 13)
    await api.removeClassExperiment(7, 11, 13, 'class-experiment-token', 'operation-2')
    await api.previewClass(7, 11)
    await api.deleteClass(7, 11, 'class-token', 'operation-3')
    await api.cleanup(7, 'cleanup-1')

    expect(client.get).toHaveBeenNthCalledWith(1, '/api/v1/teacher/courses/7/experiments/13/deletion/', { signal: undefined, retry: false })
    expect(client.mutate).toHaveBeenNthCalledWith(1, '/api/v1/teacher/courses/7/experiments/13/deletion/', {
      method: 'POST', body: { confirmation: 'delete', confirmation_token: 'experiment-token', operation_id: 'operation-1' },
    })
    expect(client.get).toHaveBeenNthCalledWith(2, '/api/v1/teacher/courses/7/classes/11/experiments/13/remove/', { signal: undefined, retry: false })
    expect(client.mutate).toHaveBeenNthCalledWith(2, '/api/v1/teacher/courses/7/classes/11/experiments/13/remove/', {
      method: 'POST', body: { confirmation: 'delete', confirmation_token: 'class-experiment-token', operation_id: 'operation-2' },
    })
    expect(client.get).toHaveBeenNthCalledWith(3, '/api/v1/teacher/accounts/courses/7/classes/11/delete/', { signal: undefined, retry: false })
    expect(client.mutate).toHaveBeenNthCalledWith(3, '/api/v1/teacher/accounts/courses/7/classes/11/delete/', {
      method: 'POST', body: { confirmation: 'delete', confirmation_token: 'class-token', operation_id: 'operation-3' },
    })
    expect(client.get).toHaveBeenNthCalledWith(4, '/api/v1/teacher/courses/7/deletion-cleanups/cleanup-1/', { signal: undefined, retry: false })
    const admission = { operation_id: 'operation-1', action: 'delete_experiment' as const, object_id: 13, class_id: null, confirmation_token: 'experiment-token' }
    await api.registerOperation(7, admission)
    await api.operation(7, 'operation-1')
    expect(client.mutate).toHaveBeenLastCalledWith('/api/v1/teacher/courses/7/deletion-operations/', { method: 'POST', body: admission })
    expect(client.get).toHaveBeenLastCalledWith('/api/v1/teacher/courses/7/deletion-operations/operation-1/', { signal: undefined, retry: false })
  })
})
