import { describe, expect, it, vi } from 'vitest'

import { createTeacherCourseApi } from './api'

describe('teacher course API', () => {
  it('uses the approved course endpoints without altering their payloads', async () => {
    const client = { get: vi.fn(), mutate: vi.fn() }
    client.get.mockResolvedValue({})
    client.mutate.mockResolvedValue({})
    const api = createTeacherCourseApi(client)

    await api.list()
    await api.create({ name: '研究方法', code: 'RM101', term: '2026 秋', description: '课程说明' })
    await api.detail(7)
    await api.settings(7)
    await api.saveSettings(7, {
      name: '研究方法', code: 'RM101', term: '2026 秋', description: '课程说明', state_token: 'state-1', teachers: [2, 3],
    })
    await api.archive(7)
    await api.createPurgeRequest(7, 'collection_error')
    await api.executePurgeRequest(7, 'purge-1')

    expect(client.get).toHaveBeenNthCalledWith(1, '/api/v1/teacher/courses/')
    expect(client.mutate).toHaveBeenNthCalledWith(1, '/api/v1/teacher/courses/', {
      method: 'POST', body: { name: '研究方法', code: 'RM101', term: '2026 秋', description: '课程说明' },
    })
    expect(client.get).toHaveBeenNthCalledWith(2, '/api/v1/teacher/courses/7/')
    expect(client.get).toHaveBeenNthCalledWith(3, '/api/v1/teacher/courses/7/settings/')
    expect(client.mutate).toHaveBeenNthCalledWith(2, '/api/v1/teacher/courses/7/settings/', {
      method: 'POST', body: {
        name: '研究方法', code: 'RM101', term: '2026 秋', description: '课程说明', state_token: 'state-1', teachers: [2, 3],
      },
    })
    expect(client.mutate).toHaveBeenNthCalledWith(3, '/api/v1/teacher/courses/7/archive/', {
      method: 'POST', body: { confirmation: 'archive' },
    })
    expect(client.mutate).toHaveBeenNthCalledWith(4, '/api/v1/teacher/courses/7/purge-requests/', {
      method: 'POST', body: { confirmation: 'PURGE', reason_code: 'collection_error' },
    })
    expect(client.mutate).toHaveBeenNthCalledWith(5, '/api/v1/teacher/courses/7/purge-requests/purge-1/execute/', {
      method: 'POST', body: { confirmation: 'execute' },
    })
  })
})
