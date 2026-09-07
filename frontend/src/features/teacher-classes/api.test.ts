import { describe, expect, it, vi } from 'vitest'

import { createTeacherClassApi } from './api'

describe('teacher class API paths', () => {
  it('uses the teacher class collection and authoritative center endpoints', async () => {
    const client = { get: vi.fn().mockResolvedValue({}), mutate: vi.fn().mockResolvedValue({}) }
    const api = createTeacherClassApi(client as never)

    await api.list(7, { page: 2, query: '2026 一班' })
    await api.create(7, { name: '实验二班', academic_year: '2027' })
    await api.center(7, 11, {
      section: 'students',
      page: 3,
      query: 'S001',
      sort: '-correct_rate',
      status: 'completed',
    })

    expect(client.get).toHaveBeenNthCalledWith(
      1,
      '/api/v1/teacher/courses/7/classes/?page=2&q=2026+%E4%B8%80%E7%8F%AD',
    )
    expect(client.get).toHaveBeenNthCalledWith(
      2,
      '/api/v1/teacher/courses/7/classes/11/center/?page=3&q=S001&sort=-correct_rate&status=completed',
    )
    expect(client.mutate).toHaveBeenCalledWith(
      '/api/v1/teacher/courses/7/classes/',
      { method: 'POST', body: { name: '实验二班', academic_year: '2027' } },
    )
  })
})
