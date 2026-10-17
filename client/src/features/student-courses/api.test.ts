import { describe, expect, it, vi } from 'vitest'
vi.mock('@/assessment-runtime/session-controller', () => ({ assessmentClientInstanceId: async () => '00000000-0000-4000-8000-000000000099' }))
function entryCalls(client: { mutate: ReturnType<typeof vi.fn> }) {
  return client.mutate.mock.calls.map(([path, options]) => {
    if (!/\/(start|resume)\/$/.test(path)) return [path, options]
    const { client_instance_id: _client, navigation_id: _navigation, client_occurred_at: _occurred, client_monotonic_ms: _monotonic, client_time_origin: _origin, reason: _reason, ...body } = options.body || {}
    void [_client, _navigation, _occurred, _monotonic, _origin, _reason]
    return [path, { ...options, body: Object.keys(body).length ? body : undefined }].map((row, index) => index === 1 && !(row as { body?: unknown }).body ? { method: 'POST' } : row)
  })
}

import { createStudentCourseApi, getStudentCourseApi } from './api'
import { acceptBootstrapContext, clearBootstrapContext } from '@/app/bootstrap'
import { ApiError, createApiClient } from '@/api/client'
import { readAdmissionCredentials, writeAdmissionCredentials } from '@/assessment-runtime/admission'

describe('student course API', () => {
  it('rebuilds authenticated clients and prevents old pending reads from entering a new account', async () => {
    const context = { csrf_token: 'csrf-old', user: { id: 7, role: 'student', display_name: '学生', username: 'S7',
      student_number: 'S7', home_url: '/student/', uses_initial_password: false, initial_password_reminder_pending: false,
      research_consent_required: false, research_consent_version: 'v1' } }
    let resolve!: (response: Response) => void
    const fetchImpl = vi.fn(() => new Promise<Response>(done => { resolve = done }))
    vi.stubGlobal('fetch', fetchImpl)
    try {
      acceptBootstrapContext(context)
      const oldApi = getStudentCourseApi()
      const oldRequest = oldApi.tasks(7)
      const rejected = expect(oldRequest).rejects.toMatchObject({ code: 'AUTHENTICATION_CHANGED' })
      acceptBootstrapContext({ ...context, csrf_token: 'csrf-new', user: { ...context.user, id: 9 } })
      expect(getStudentCourseApi()).not.toBe(oldApi)
      resolve(new Response(JSON.stringify({ data: { items: ['old-user'] }, meta: { request_id: 'old' } })))
      await rejected
      await expect(oldApi.tasks(8)).rejects.toMatchObject({ code: 'AUTHENTICATION_CHANGED' })
      expect(fetchImpl).toHaveBeenCalledTimes(1)
    } finally { clearBootstrapContext(); vi.unstubAllGlobals() }
  })
  it('merges only concurrent course/task reads and reads again after success or failure', async () => {
    let resolve!: (value: unknown) => void
    const client = { get: vi.fn(() => new Promise(done => { resolve = done })), mutate: vi.fn() }
    const api = createStudentCourseApi(client as never)
    const first = api.tasks(7)
    expect(api.tasks(7)).toBe(first)
    expect(client.get).toHaveBeenCalledOnce()
    resolve({ items: [] })
    await first
    const refresh = api.tasks(7)
    expect(refresh).not.toBe(first)
    resolve({ items: [] })
    await refresh
    const courses = api.courses()
    expect(api.courses()).toBe(courses)
    resolve({ items: [] })
    await courses
    client.get.mockRejectedValueOnce(new Error('offline'))
    await expect(api.tasks(8)).rejects.toThrow('offline')
    const retry = api.tasks(8)
    resolve({ items: [] })
    await retry
    expect(client.get).toHaveBeenCalledTimes(5)
  })

  it('adds expanding jitter after Retry-After without synchronizing starts at one second', async () => {
    vi.useFakeTimers()
    sessionStorage.clear()
    const random = vi.spyOn(Math, 'random').mockReturnValue(0.5)
    const times: number[] = []
    const client = { get: vi.fn(), mutate: vi.fn(async () => { times.push(Date.now()); if (times.length <= 5) throw new ApiError(429, { error: { code: 'START_BUSY' } }); return { session_id: 7, question_url: '/question/' } }) }
    try {
      const pending = createStudentCourseApi(client as never, () => 0.75).start(19)
      await vi.advanceTimersByTimeAsync(15000)
      await pending
      expect(times.slice(1).map((time, index) => time - times[index]!)).toEqual([1250, 1750, 2750, 2750, 2750])
      expect(client.mutate.mock.calls.every(call => JSON.stringify(call) === JSON.stringify(client.mutate.mock.calls[0]))).toBe(true)
    } finally { random.mockRestore(); vi.useRealTimers(); sessionStorage.clear() }
  })
  it('retries busy direct starts using one intent without admission status polling', async () => {
    vi.useFakeTimers()
    sessionStorage.clear()
    try {
      const client = { get: vi.fn(), mutate: vi.fn()
        .mockRejectedValueOnce(new ApiError(429, { error: { code: 'START_BUSY' } }))
        .mockResolvedValueOnce({ status: 'executing', retry_after_seconds: 1 })
        .mockResolvedValueOnce({ session_id: 7, question_url: '/question/' }) }
      const pending = createStudentCourseApi(client, () => 1).start(19)
      await vi.advanceTimersByTimeAsync(6000)
      await expect(pending).resolves.toMatchObject({ session_id: 7 })
      expect(client.mutate).toHaveBeenCalledTimes(3)
      const original = client.mutate.mock.calls[0]![1].body
      for (const [path, options] of client.mutate.mock.calls) {
        expect(path).toBe('/api/v1/student/tasks/19/start/')
        expect(options.body).toEqual(original)
      }
      expect(client.get).not.toHaveBeenCalled()
    } finally { vi.useRealTimers(); sessionStorage.clear() }
  })

  it('ends repeated busy starts within the existing sixty-second budget without changing identity or polling recovery', async () => {
    vi.useFakeTimers()
    sessionStorage.clear()
    const random = vi.spyOn(Math, 'random').mockReturnValue(0.999)
    const startedAt = Date.now()
    const client = { get: vi.fn(), mutate: vi.fn().mockRejectedValue(new ApiError(429, { error: { code: 'START_BUSY' } })) }
    const pending = createStudentCourseApi(client, () => 2).start(19)
    const rejected = expect(pending).rejects.toMatchObject({ code: 'START_BUSY' })
    try {
      await vi.advanceTimersByTimeAsync(60000)
      await rejected
      expect(client.mutate.mock.calls.length).toBeLessThan(20)
      expect(client.mutate.mock.calls.every(call => JSON.stringify(call) === JSON.stringify(client.mutate.mock.calls[0]))).toBe(true)
      expect(client.get).not.toHaveBeenCalled()
      expect(Date.now() - startedAt).toBe(60000)
    } finally { random.mockRestore(); vi.useRealTimers(); sessionStorage.clear() }
  })

  it('restores an uncertain direct start intent after reload instead of creating a resume visit', async () => {
    sessionStorage.clear()
    const client = { get: vi.fn().mockResolvedValue({ code: 'SESSION_NOT_STARTED' }), mutate: vi.fn()
      .mockRejectedValueOnce(new TypeError('network'))
      .mockResolvedValueOnce({ session_id: 7, question_url: '/question/' }) }
    await expect(createStudentCourseApi(client).start(19)).rejects.toThrow()
    await createStudentCourseApi(client).resume(19)
    expect(client.mutate.mock.calls[1]![0]).toBe('/api/v1/student/tasks/19/start/')
    expect(client.mutate.mock.calls[1]![1].body).toEqual(client.mutate.mock.calls[0]![1].body)
    sessionStorage.clear()
  })

  it('restores the canonical direct activation receipt after a committed response is lost', async () => {
    sessionStorage.clear()
    const receipt = { session_id: 7, question_url: '/question/', runtime: { entry_receipt: { visit_index: 1 } } }
    const client = { get: vi.fn().mockResolvedValue({ can_continue: true, session_id: 7, question_url: '/question/' }),
      mutate: vi.fn().mockRejectedValueOnce(new TypeError('lost response')).mockResolvedValueOnce(receipt) }
    await expect(createStudentCourseApi(client).start(19)).resolves.toEqual(receipt)
    expect(client.mutate).toHaveBeenCalledTimes(2)
    expect(client.mutate.mock.calls[1]).toEqual(client.mutate.mock.calls[0])
    sessionStorage.clear()
  })
  it('recovers the original activation receipt when reload offers resume after a committed start response was lost', async () => {
    sessionStorage.clear()
    try {
      const receipt = { navigation_id: 'original-navigation', visit_index: 1 }
      const client = { get: vi.fn().mockResolvedValue({ can_continue: true, session_id: 7, question_url: '/student/sessions/7/questions/11/' }), mutate: vi.fn()
        .mockResolvedValueOnce({ status: 'admitted', admission_token: 'original-grant', poll_url: '/status/' })
        .mockRejectedValueOnce(new TypeError('activation response aborted'))
        .mockResolvedValueOnce({ session_id: 7, question_url: '/student/sessions/7/questions/11/', admission_token: 'original-grant', runtime: { entry_receipt: receipt } }) }
      await createStudentCourseApi(client).start(19)
      expect(readAdmissionCredentials(19)).toEqual({ admission_token: 'original-grant', admission_action: 'start' })

      const recovered = await createStudentCourseApi(client).resume(19)

      expect(recovered.runtime?.entry_receipt).toEqual(receipt)
      expect(entryCalls(client)).toEqual([
        ['/api/v1/student/tasks/19/start/', { method: 'POST' }],
        ['/api/v1/student/tasks/19/start/', { method: 'POST', body: { admission_token: 'original-grant', admission_action: 'start' } }],
        ['/api/v1/student/tasks/19/start/', { method: 'POST', body: { admission_token: 'original-grant', admission_action: 'start' } }],
      ])
      expect(client.mutate.mock.calls[2]![1].body.client_instance_id).toBe(client.mutate.mock.calls[1]![1].body.client_instance_id)
      expect(readAdmissionCredentials(19)?.session_id).toBe(7)
      expect(client.get).toHaveBeenCalledOnce()
    } finally { sessionStorage.clear() }
  })

  it('shares a pending start recovery between simultaneous resume and start calls', async () => {
    sessionStorage.clear()
    try {
      writeAdmissionCredentials(19, { admission_token: 'pending', admission_action: 'start' })
      let finish!: (value: object) => void
      const client = { get: vi.fn(), mutate: vi.fn().mockImplementation(() => new Promise(resolve => { finish = resolve })) }
      const api = createStudentCourseApi(client)
      const resumed = api.resume(19)
      const started = api.start(19)
      await vi.waitFor(() => expect(client.mutate).toHaveBeenCalledOnce())
      finish({ session_id: 7, question_url: '/question/', admission_token: 'pending' })
      await expect(Promise.all([resumed, started])).resolves.toEqual([
        { session_id: 7, question_url: '/question/', admission_token: 'pending' },
        { session_id: 7, question_url: '/question/', admission_token: 'pending' },
      ])
      expect(entryCalls(client)).toEqual([
        ['/api/v1/student/tasks/19/start/', { method: 'POST', body: { admission_token: 'pending', admission_action: 'start' } }],
      ])
    } finally { sessionStorage.clear() }
  })

  it('keeps ordinary resume after an acknowledged start on the resume endpoint', async () => {
    sessionStorage.clear()
    try {
      writeAdmissionCredentials(19, { admission_token: 'acknowledged', admission_action: 'start', session_id: 7 })
      const client = { get: vi.fn(), mutate: vi.fn().mockResolvedValue({ session_id: 7, question_url: '/question/' }) }
      await createStudentCourseApi(client).resume(19)
      expect(entryCalls(client)).toEqual([['/api/v1/student/tasks/19/resume/', { method: 'POST' }]])
    } finally { sessionStorage.clear() }
  })

  it('stops on a server-confirmed terminal activation failure without reclaiming or treating it as a lost response', async () => {
    sessionStorage.clear()
    const client = { get: vi.fn(), mutate: vi.fn().mockResolvedValue({ status: 'failed', admission_token: 'failed' }) }
    await expect(createStudentCourseApi(client).start(19)).rejects.toThrow('作业准备失败')
    expect(client.mutate).toHaveBeenCalledOnce()
    expect(client.get).not.toHaveBeenCalled()
  })

  it('activates a direct grant in a separate request with honest ordered browser phases', async () => {
    sessionStorage.clear()
    window.__ktPreparedStartupWitness = Object.freeze([])
    const client = { get: vi.fn(), mutate: vi.fn()
      .mockResolvedValueOnce({ status: 'admitted', admission_token: 'direct', poll_url: '/status/', retry_after_seconds: 1 })
      .mockResolvedValueOnce({ session_id: 7, question_url: '/student/sessions/7/questions/11/', admission_token: 'direct' }) }
    await expect(createStudentCourseApi(client).start(19)).resolves.toMatchObject({ session_id: 7 })
    expect(client.mutate).toHaveBeenCalledTimes(2)
    const original = client.mutate.mock.calls[0]![1].body
    expect(client.mutate.mock.calls[1]![1].body).toEqual({ ...original, admission_token: 'direct', admission_action: 'start' })
    expect(window.__ktPreparedStartupWitness?.map(row => row.phase)).toEqual(['request', 'grant', 'activation_start', 'activation_response'])
    expect(client.get).not.toHaveBeenCalled()
    sessionStorage.clear()
  })

  it('waits through executing with the same token, stable activation metadata and Retry-After', async () => {
    vi.useFakeTimers()
    sessionStorage.clear()
    try {
      const client = { get: vi.fn(), mutate: vi.fn()
        .mockResolvedValueOnce({ status: 'executing', admission_token: 'held', poll_url: '/status/', retry_after_seconds: 1 })
        .mockResolvedValueOnce({ status: 'executing', admission_token: 'held', poll_url: '/status/', retry_after_seconds: 1 })
        .mockResolvedValueOnce({ status: 'succeeded', admission_token: 'held', poll_url: '/status/' })
        .mockResolvedValueOnce({ session_id: 7, question_url: '/student/sessions/7/questions/11/', admission_token: 'held' }) }
      const promise = createStudentCourseApi(client, () => 4).start(19)
      await vi.advanceTimersByTimeAsync(3900)
      expect(client.mutate).toHaveBeenCalledTimes(1)
      await vi.advanceTimersByTimeAsync(11000)
      await expect(promise).resolves.toMatchObject({ session_id: 7 })
      const original = client.mutate.mock.calls[0]![1].body
      expect(original.client_instance_id).toBe('00000000-0000-4000-8000-000000000099')
      expect(client.mutate.mock.calls[3]![1].body).toEqual({ ...original, admission_token: 'held', admission_action: 'start' })
      expect(client.mutate.mock.calls[1]![1].body).toEqual({ admission_token: 'held', action: 'start' })
    } finally { vi.useRealTimers(); sessionStorage.clear() }
  })

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
    expect(entryCalls(client)).toEqual([
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
      await vi.advanceTimersByTimeAsync(2100)
      await expect(pending).resolves.toMatchObject({ session_id: 7 })
      expect(entryCalls(client)).toEqual([
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
    expect(entryCalls(client)).toEqual([
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
