import { describe, expect, it, vi } from 'vitest'

import { createTeacherDataCenterApi } from './api'

describe('teacher data-center API paths', () => {
  it('preserves the complete scoped query across read endpoints', async () => {
    const client = { get: vi.fn().mockResolvedValue({}), mutate: vi.fn().mockResolvedValue({}) }
    const api = createTeacherDataCenterApi(client as never)
    const scope = {
      experiment_ids: '3,5',
      class_id: 4,
      search: '张同学',
      session_ref: 'S-20260909-001',
    }

    await api.scope(7)
    await api.overview(7, scope)
    await api.exports(7, { ...scope, package: 'experiment' })
    await api.quality(7, { ...scope, page: 2 })
    await api.history(7, { ...scope, page: 3 })
    await api.advanced(7, { ...scope, view: 'timeline', table: 'behavior_events', page: 4 })
    await api.updateScope(7, { experiment_ids: [3, 5], class_id: 4 })
    await api.createExport(7, { package: 'experiment', confirm_identified: true, scope })
    await api.removeExport('/api/v1/teacher/courses/7/data/export-jobs/abc/')

    expect(client.get).toHaveBeenNthCalledWith(1, '/api/v1/teacher/courses/7/data/scope/')
    expect(client.get).toHaveBeenNthCalledWith(2, '/api/v1/teacher/courses/7/data/overview/?experiment_ids=3%2C5&class_id=4&search=%E5%BC%A0%E5%90%8C%E5%AD%A6&session_ref=S-20260909-001')
    expect(client.get).toHaveBeenNthCalledWith(3, '/api/v1/teacher/courses/7/data/exports/?experiment_ids=3%2C5&class_id=4&search=%E5%BC%A0%E5%90%8C%E5%AD%A6&session_ref=S-20260909-001&package=experiment')
    expect(client.get).toHaveBeenNthCalledWith(4, '/api/v1/teacher/courses/7/data/quality/?experiment_ids=3%2C5&class_id=4&search=%E5%BC%A0%E5%90%8C%E5%AD%A6&session_ref=S-20260909-001&page=2')
    expect(client.get).toHaveBeenNthCalledWith(5, '/api/v1/teacher/courses/7/data/exports/history/?experiment_ids=3%2C5&class_id=4&search=%E5%BC%A0%E5%90%8C%E5%AD%A6&session_ref=S-20260909-001&page=3')
    expect(client.get).toHaveBeenNthCalledWith(6, '/api/v1/teacher/courses/7/data/advanced/?experiment_ids=3%2C5&class_id=4&search=%E5%BC%A0%E5%90%8C%E5%AD%A6&session_ref=S-20260909-001&view=timeline&table=behavior_events&page=4')
    expect(client.mutate).toHaveBeenNthCalledWith(1, '/api/v1/teacher/courses/7/data/scope/', { method: 'POST', body: { experiment_ids: [3, 5], class_id: 4 } })
    expect(client.mutate).toHaveBeenNthCalledWith(2, '/api/v1/teacher/courses/7/data/export-jobs/', { method: 'POST', body: { package: 'experiment', confirm_identified: true, scope } })
    expect(client.mutate).toHaveBeenNthCalledWith(3, '/api/v1/teacher/courses/7/data/export-jobs/abc/', { method: 'DELETE' })
  })
})
