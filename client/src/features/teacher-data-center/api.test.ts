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
    const signal = new AbortController().signal
    await api.advanced(7, { ...scope, view: 'timeline', table: 'behavior_events', page: 4 }, signal)
    await api.advancedStudents(7, { ...scope, q: '张' }, signal)
    await api.updateScope(7, { experiment_ids: [3, 5], class_id: 4 })
    await api.createExport(7, { package: 'experiment', confirm_identified: true, scope })
    await api.exportJob(7, 'job-1', signal)
    await api.removeExport('/api/v1/teacher/courses/7/data/export-jobs/abc/')

    expect(client.get).toHaveBeenNthCalledWith(1, '/api/v1/teacher/courses/7/data/scope/')
    expect(client.get).toHaveBeenNthCalledWith(2, '/api/v1/teacher/courses/7/data/overview/?experiment_ids=3%2C5&class_id=4&search=%E5%BC%A0%E5%90%8C%E5%AD%A6&session_ref=S-20260909-001', { signal: undefined })
    expect(client.get).toHaveBeenNthCalledWith(3, '/api/v1/teacher/courses/7/data/exports/?experiment_ids=3%2C5&class_id=4&search=%E5%BC%A0%E5%90%8C%E5%AD%A6&session_ref=S-20260909-001&package=experiment')
    expect(client.get).toHaveBeenNthCalledWith(4, '/api/v1/teacher/courses/7/data/quality/?experiment_ids=3%2C5&class_id=4&search=%E5%BC%A0%E5%90%8C%E5%AD%A6&session_ref=S-20260909-001&page=2')
    expect(client.get).toHaveBeenNthCalledWith(5, '/api/v1/teacher/courses/7/data/exports/history/?experiment_ids=3%2C5&class_id=4&search=%E5%BC%A0%E5%90%8C%E5%AD%A6&session_ref=S-20260909-001&page=3')
    expect(client.get).toHaveBeenNthCalledWith(6, '/api/v1/teacher/courses/7/data/advanced/?experiment_ids=3%2C5&class_id=4&search=%E5%BC%A0%E5%90%8C%E5%AD%A6&session_ref=S-20260909-001&view=timeline&table=behavior_events&page=4', { signal, retryPolicy: 'teacher-read' })
    expect(client.get).toHaveBeenNthCalledWith(7, '/api/v1/teacher/courses/7/data/advanced/students/?experiment_ids=3%2C5&class_id=4&q=%E5%BC%A0', { signal, retryPolicy: 'teacher-read' })
    expect(client.get).toHaveBeenNthCalledWith(8, '/api/v1/teacher/courses/7/data/export-jobs/job-1/', { signal, retry: false })
    expect(client.mutate).toHaveBeenNthCalledWith(1, '/api/v1/teacher/courses/7/data/scope/', { method: 'POST', body: { experiment_ids: [3, 5], class_id: 4 } })
    expect(client.mutate).toHaveBeenNthCalledWith(2, '/api/v1/teacher/courses/7/data/export-jobs/', { method: 'POST', body: { package: 'experiment', confirm_identified: true, scope } })
    expect(client.mutate).toHaveBeenNthCalledWith(3, '/api/v1/teacher/courses/7/data/export-jobs/abc/', { method: 'DELETE' })
  })

  it('projects suggestion scope before sending it and keeps metadata checks single-attempt', async () => {
    const client = { get: vi.fn().mockResolvedValue({}), mutate: vi.fn() }
    const api = createTeacherDataCenterApi(client as never)
    const signal = new AbortController().signal
    await api.advancedStudents(7, { experiment_id: 3, version_mode: 'single', version_id: 4, class_snapshot_id: 'uuid', condition_id: 8,
      started_from_utc: '2026-01-01T00:00:00Z', student_id: 99, session_id: 88, search: 'old', session_ref: 'invalid', cursor: 'old', question_ref: 'Q1', visit: 'Q1:1', table: 'sessions', page: 2, q: '李', limit: 20 }, signal)
    const url = new URL(client.get.mock.calls[0]![0] as string, 'https://example.test')
    expect(Object.fromEntries(url.searchParams)).toEqual({ experiment_id: '3', version_mode: 'single', version_id: '4', class_snapshot_id: 'uuid', condition_id: '8', started_from_utc: '2026-01-01T00:00:00Z', q: '李', limit: '20' })
    await api.advancedStatus(7, {}, signal)
    expect(client.get).toHaveBeenLastCalledWith('/api/v1/teacher/courses/7/data/advanced/status/', { signal, retry: false })
  })
})
