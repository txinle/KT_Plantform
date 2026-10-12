import { describe, expect, it, vi } from 'vitest'

import { createStudentCourseApi } from './api'
import { ApiError, createApiClient } from '@/api/client'
import { readAdmissionCredentials, writeAdmissionCredentials } from '@/assessment-runtime/admission'

describe('student course API', () => {
  it('merges identical lightweight checks independently from full learning requests', async () => {
    let resolve!: (value: object) => void
    const client = { get: vi.fn().mockImplementation(() => new Promise<object>((done) => { resolve = done })), mutate: vi.fn() }
    const api = createStudentCourseApi(client as never)
    const first = api.learningStatus(7)
    expect(api.learningStatus(7)).toBe(first)
    expect(client.get).toHaveBeenCalledTimes(1)
    expect(client.get).toHaveBeenCalledWith('/api/v1/student/courses/7/learning/?status_only=1', { retry: false })
    resolve({ version: 'v1' })
    await first
    const detail = api.learningExperimentStatus(7, 11)
    expect(api.learningExperimentStatus(7, 11)).toBe(detail)
    resolve({ version: 'v1' })
    await detail
    expect(client.get).toHaveBeenCalledTimes(2)
  })
  it('does not retry lightweight status failures but preserves full-read retries', async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError('network'))
    const api = createStudentCourseApi(createApiClient({ fetchImpl }))
    await expect(api.learningStatus(7)).rejects.toThrow()
    expect(fetchImpl).toHaveBeenCalledTimes(1)
    await expect(api.learningExperimentStatus(7, 11)).rejects.toThrow()
    expect(fetchImpl).toHaveBeenCalledTimes(2)
    await expect(api.learning(7)).rejects.toThrow()
    expect(fetchImpl).toHaveBeenCalledTimes(4)
  })
  it('queries the existing task after a lost start response', async () => {
    const client = { mutate: vi.fn().mockRejectedValue(new TypeError('network')), get: vi.fn().mockResolvedValue({ can_continue: true, question_url: '/student/sessions/7/questions/11/' }) }
    const api = createStudentCourseApi(client)
    const result = await api.start(19)
    expect(result.question_url).toBe('/student/sessions/7/questions/11/')
    expect(client.get).toHaveBeenCalledWith('/api/v1/student/tasks/19/recovery/')
    expect(client.mutate).toHaveBeenCalledOnce()
  })
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

  it('polls a queued admission and retries the original entry request once admitted', async () => {
    vi.useFakeTimers()
    try {
      sessionStorage.clear()
      const client = {
        get: vi.fn(),
        mutate: vi.fn()
          .mockResolvedValueOnce({ status: 'queued', admission_token: 'token-1', poll_url: '/api/v1/student/tasks/19/admission/status/', retry_after_seconds: 1 })
          .mockResolvedValueOnce({ status: 'admitted', admission_token: 'token-1', poll_url: '/api/v1/student/tasks/19/admission/status/', retry_after_seconds: 1 })
          .mockResolvedValueOnce({ task_id: 19, session_id: 7, question_url: '/student/sessions/7/questions/11/', admission_token: 'token-1', admission_action: 'start' }),
      }
      const api = createStudentCourseApi(client as never)
      const pending = api.start(19)
      await vi.advanceTimersByTimeAsync(1600)
      await expect(pending).resolves.toMatchObject({ session_id: 7 })
      expect(client.mutate.mock.calls).toEqual([
        ['/api/v1/student/tasks/19/start/', { method: 'POST' }],
        ['/api/v1/student/tasks/19/admission/status/', { method: 'POST', body: { admission_token: 'token-1', action: 'start' } }],
        ['/api/v1/student/tasks/19/start/', { method: 'POST', body: { admission_token: 'token-1', admission_action: 'start' } }],
      ])
    } finally { vi.useRealTimers() }
  })

  it.each(['start', 'resume'] as const)('reacquires an invalid saved %s grant once without stale credentials', async (action) => {
    sessionStorage.clear()
    writeAdmissionCredentials(19, { admission_token: 'expired', admission_action: action })
    const client = {
      get: vi.fn(),
      mutate: vi.fn()
        .mockRejectedValueOnce(new ApiError(409, { error: { code: 'ADMISSION_TOKEN_INVALID' } }))
        .mockResolvedValueOnce({ task_id: 19, session_id: 7, question_url: '/question/', admission_token: 'fresh', admission_action: action }),
    }
    await expect(createStudentCourseApi(client)[action](19)).resolves.toMatchObject({ session_id: 7 })
    expect(client.mutate.mock.calls).toEqual([
      [`/api/v1/student/tasks/19/${action}/`, { method: 'POST', body: { admission_token: 'expired', admission_action: action } }],
      [`/api/v1/student/tasks/19/${action}/`, { method: 'POST' }],
    ])
    expect(readAdmissionCredentials(19)).toEqual({ admission_token: 'fresh', admission_action: action, session_id: 7 })
    expect(client.get).not.toHaveBeenCalled()
    sessionStorage.clear()
  })

  it('stops after one invalid-grant refresh and does not retry other authorization errors', async () => {
    for (const code of ['ADMISSION_TOKEN_INVALID', 'RESEARCH_CONSENT_REQUIRED']) {
      sessionStorage.clear()
      writeAdmissionCredentials(19, { admission_token: 'expired', admission_action: 'start' })
      const error = new ApiError(code === 'ADMISSION_TOKEN_INVALID' ? 409 : 403, { error: { code } })
      const client = { get: vi.fn(), mutate: vi.fn().mockRejectedValue(error) }
      await expect(createStudentCourseApi(client).start(19)).rejects.toBe(error)
      expect(client.mutate).toHaveBeenCalledTimes(code === 'ADMISSION_TOKEN_INVALID' ? 2 : 1)
      expect(client.get).not.toHaveBeenCalled()
      expect(readAdmissionCredentials(19)?.admission_token).toBe(code === 'ADMISSION_TOKEN_INVALID' ? undefined : 'expired')
    }
    sessionStorage.clear()
  })

  it('reuses an in-flight experiment result request', async () => {
    let resolveRequest!: (value: object) => void
    const response = new Promise<object>((resolve) => { resolveRequest = resolve })
    const client = {
      get: vi.fn().mockReturnValue(response),
      mutate: vi.fn(),
    }
    const api = createStudentCourseApi(client as never)

    const first = api.learningExperiment(7, 11)
    const second = api.learningExperiment(7, 11)
    expect(client.get).toHaveBeenCalledOnce()
    expect(first).toBe(second)

    resolveRequest({ experiment_id: 11 })
    await expect(first).resolves.toEqual({ experiment_id: 11 })
    await api.learningExperiment(7, 11)
    expect(client.get).toHaveBeenCalledTimes(2)
  })

  it('allows retry after a failed shared request and isolates experiment keys', async () => {
    const client = {
      get: vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue({}),
      mutate: vi.fn(),
    }
    const api = createStudentCourseApi(client as never)
    const first = api.learningExperiment(7, 11)
    expect(api.learningExperiment(7, 11)).toBe(first)
    await expect(first).rejects.toThrow('offline')
    await api.learningExperiment(7, 11)
    await Promise.all([api.learningExperiment(8, 11), api.learningExperiment(7, 12)])
    expect(client.get).toHaveBeenCalledTimes(4)
  })
})
