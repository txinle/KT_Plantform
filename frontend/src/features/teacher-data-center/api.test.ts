import { describe, expect, it, vi } from 'vitest'

import { createTeacherDataCenterApi } from './api'

describe('teacher data-center API paths', () => {
  it('preserves the complete scoped query across read endpoints', async () => {
    const client = { get: vi.fn().mockResolvedValue({}) }
    const api = createTeacherDataCenterApi(client as never)
    const scope = {
      experiment_id: 3,
      class_id: 4,
      version_mode: 'single',
      version_id: 8,
    }

    await api.scope(7)
    await api.overview(7, scope)
    await api.exports(7, { ...scope, package: 'teaching' })
    await api.quality(7, { ...scope, page: 2 })
    await api.history(7, 3)
    await api.advanced(7, { ...scope, table: 'assessment_events', page: 4 })

    expect(client.get).toHaveBeenNthCalledWith(1, '/api/v1/teacher/courses/7/data/scope/')
    expect(client.get).toHaveBeenNthCalledWith(2, '/api/v1/teacher/courses/7/data/overview/?experiment_id=3&class_id=4&version_mode=single&version_id=8')
    expect(client.get).toHaveBeenNthCalledWith(3, '/api/v1/teacher/courses/7/data/exports/?experiment_id=3&class_id=4&version_mode=single&version_id=8&package=teaching')
    expect(client.get).toHaveBeenNthCalledWith(4, '/api/v1/teacher/courses/7/data/quality/?experiment_id=3&class_id=4&version_mode=single&version_id=8&page=2')
    expect(client.get).toHaveBeenNthCalledWith(5, '/api/v1/teacher/courses/7/data/exports/history/?page=3')
    expect(client.get).toHaveBeenNthCalledWith(6, '/api/v1/teacher/courses/7/data/advanced/?experiment_id=3&class_id=4&version_mode=single&version_id=8&table=assessment_events&page=4')
  })
})
