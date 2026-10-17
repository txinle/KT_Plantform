import { describe, expect, it, vi } from 'vitest'

import {
  ApiError,
  MutationOutcomeUnknownError,
  createApiClient,
} from './client'

function jsonResponse(body: unknown, status = 200, raw = false) {
  return new Response(JSON.stringify(status < 400 && !raw && !(body && typeof body === 'object' && Object.hasOwn(body, 'data')) ? { data: body, meta: { request_id: 'test-request' } } : body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('API client', () => {
  it.each([
    ['2', 2],
    ['Thu, 15 Oct 2026 00:00:04 GMT', 4],
    ['Thu, 15 Oct 2026 00:00:00 GMT', 0],
    ['invalid', undefined],
    ['-1', undefined],
    [null, undefined],
  ])('retains Retry-After %s on structured rejection', async (value, expected) => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-15T00:00:00Z'))
    try {
      const headers = new Headers({ 'Content-Type': 'application/json' })
      if (value !== null) headers.set('Retry-After', value)
      const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ error: { code: 'LOGIN_BUSY' } }), { status: 429, headers }))
      await expect(createApiClient({ fetchImpl }).mutate('/login/', { method: 'POST' })).rejects.toMatchObject({
        status: 429, code: 'LOGIN_BUSY', retryAfterSeconds: expected,
      })
      expect(fetchImpl).toHaveBeenCalledOnce()
    } finally { vi.useRealTimers() }
  })

  it('downloads binary exports with repeated form fields, CSRF and server filename', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(new Response('file-data', { headers: {
      'Content-Type': 'application/octet-stream', 'Content-Disposition': "attachment; filename*=UTF-8''%E6%8A%A5%E5%91%8A.xlsx",
    } }))
    const body = new FormData()
    body.append('fields', 'student_number')
    body.append('fields', 'answer')
    const file = await createApiClient({ fetchImpl, getCsrfToken: () => 'csrf' }).download('/api/v1/export/', { method: 'POST', body })
    expect(await file.blob.text()).toBe('file-data')
    expect(file.filename).toBe('报告.xlsx')
    expect(fetchImpl).toHaveBeenCalledWith('/api/v1/export/', expect.objectContaining({ method: 'POST', credentials: 'same-origin', body,
      headers: { Accept: '*/*', 'X-CSRFToken': 'csrf' } }))
    expect(body.getAll('fields')).toEqual(['student_number', 'answer'])
  })

  it('retains structured export authorization errors and never retries a POST download', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ error: { code: 'PERMISSION_DENIED', message: 'forbidden', request_id: 'denied-request' } }, 403))
    await expect(createApiClient({ fetchImpl }).download('/api/v1/export/', { method: 'POST' })).rejects.toMatchObject({ code: 'PERMISSION_DENIED', requestId: 'denied-request' })
    expect(fetchImpl).toHaveBeenCalledTimes(1)
  })
  it('unwraps the sole success envelope while retaining structured error correlation', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValueOnce(jsonResponse({ data: { saved: true }, meta: { request_id: 'server-request' } }))
      .mockResolvedValueOnce(jsonResponse({ error: { code: 'ENTRY_MATERIAL_CONFLICT', message: 'conflict', fields: { navigation_id: 'original' }, request_id: 'error-request' } }, 409))
    const client = createApiClient({ fetchImpl })
    await expect(client.get('/api/v1/entry/')).resolves.toEqual({ saved: true })
    await expect(client.mutate('/api/v1/entry/', { method: 'POST', body: {} })).rejects.toMatchObject({ code: 'ENTRY_MATERIAL_CONFLICT', requestId: 'error-request' })
  })

  it('rejects a flat success response instead of permanently accepting two wire protocols', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ saved: true }, 200, true))
    await expect(createApiClient({ fetchImpl }).get('/api/v1/entry/', { retry: false })).rejects.toThrow()
  })
  it('correlates assessment requests without adding payload data to headers', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async () => jsonResponse({ success: true }))
    const client = createApiClient({ fetchImpl, getOperationId: () => 'b'.repeat(32) })
    await client.get('/api/v1/student/session/')
    await client.mutate('/student/attempts/1/submit/', { method: 'POST', body: { answer: 'PRIVATE' } })
    for (const call of fetchImpl.mock.calls) {
      const headers = call[1]?.headers as Record<string, string>
      expect(headers['X-KT-Operation-ID']).toBe('b'.repeat(32))
      expect(headers['X-Request-ID']).toMatch(/^[a-f0-9]{32}$/)
      expect(JSON.stringify(headers)).not.toContain('PRIVATE')
    }
  })

  it.each(['network', 'gateway'])('does not retry bounded status checks after %s failure', async (failure) => {
    const fetchImpl = vi.fn<typeof fetch>()
    if (failure === 'network') fetchImpl.mockRejectedValue(new TypeError('network'))
    else fetchImpl.mockResolvedValue(jsonResponse({ error: 'unavailable' }, 503))
    const client = createApiClient({ fetchImpl })

    await expect(client.get('/status/', { retry: false })).rejects.toBeInstanceOf(Error)
    expect(fetchImpl).toHaveBeenCalledTimes(1)
  })

  it('retries a failed GET once', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockRejectedValueOnce(new TypeError('network'))
      .mockResolvedValueOnce(jsonResponse({ status: 'ok' }))
    const client = createApiClient({ fetchImpl })

    await expect(client.get('/api/v1/health/')).resolves.toEqual({ status: 'ok' })
    expect(fetchImpl).toHaveBeenCalledTimes(2)
  })

  it('does not retry an aborted GET', async () => {
    const error = new DOMException('aborted', 'AbortError')
    const fetchImpl = vi.fn<typeof fetch>().mockRejectedValue(error)
    const client = createApiClient({ fetchImpl })

    await expect(client.get('/api/v1/health/')).rejects.toBe(error)
    expect(fetchImpl).toHaveBeenCalledTimes(1)
  })

  it('sends same-origin session and CSRF credentials for a mutation', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ saved: true }))
    const client = createApiClient({
      fetchImpl,
      getCsrfToken: () => 'csrf-token',
    })

    await client.mutate('/api/v1/items/1/', { method: 'PATCH', body: { name: 'A' } })

    expect(fetchImpl).toHaveBeenCalledWith(
      '/api/v1/items/1/',
      expect.objectContaining({
        method: 'PATCH',
        credentials: 'same-origin',
        headers: expect.objectContaining({ 'X-CSRFToken': 'csrf-token' }),
      }),
    )
  })

  it('forwards keepalive for a pagehide mutation', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ success: true }))
    const client = createApiClient({ fetchImpl })

    await client.mutate('/assessment/process/lease/release/', {
      method: 'POST',
      body: {},
      keepalive: true,
    } as never)

    expect(fetchImpl).toHaveBeenCalledWith(
      '/assessment/process/lease/release/',
      expect.objectContaining({ keepalive: true }),
    )
  })

  it('uploads multipart mutations without forcing a JSON content type', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ batch_id: 'batch-1' }))
    const client = createApiClient({ fetchImpl, getCsrfToken: () => 'csrf-token' })
    const body = new FormData()
    body.append('file', new File(['序号,知识点名称\n1,数学基础'], 'course-kcs.csv'))

    await client.mutateForm('/api/v1/import/preview/', { method: 'POST', body })

    expect(fetchImpl).toHaveBeenCalledWith(
      '/api/v1/import/preview/',
      expect.objectContaining({
        method: 'POST',
        credentials: 'same-origin',
        body,
        headers: { Accept: 'application/json', 'X-CSRFToken': 'csrf-token' },
      }),
    )
  })

  it('never retries an uncertain mutation and refreshes authoritative state', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockRejectedValue(new TypeError('network'))
    const refresh = vi.fn().mockResolvedValue({ status: 'saved' })
    const client = createApiClient({ fetchImpl })

    const promise = client.mutate('/api/v1/items/1/', {
      method: 'POST',
      body: {},
      refreshAuthoritativeState: refresh,
    })

    await expect(promise).rejects.toMatchObject({
      name: 'MutationOutcomeUnknownError',
      authoritativeState: { status: 'saved' },
    } satisfies Partial<MutationOutcomeUnknownError>)
    expect(fetchImpl).toHaveBeenCalledTimes(1)
    expect(refresh).toHaveBeenCalledTimes(1)
  })

  it('maps a structured 409 response without retrying', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse(
        {
          error: {
            code: 'PUBLISH_PLAN_STALE',
            message: '发布计划已更新。',
            fields: {},
            request_id: 'req-1',
          },
        },
        409,
      ),
    )
    const client = createApiClient({ fetchImpl })

    const promise = client.mutate('/api/v1/plans/1/', { method: 'PATCH', body: {} })

    await expect(promise).rejects.toMatchObject({
      status: 409,
      code: 'PUBLISH_PLAN_STALE',
      requestId: 'req-1',
    } satisfies Partial<ApiError>)
    expect(fetchImpl).toHaveBeenCalledTimes(1)
  })

  it('does not claim authority refreshed when recovery is absent or fails', async () => {
    const client = createApiClient({ fetchImpl: vi.fn().mockRejectedValue(new TypeError('network')) })
    await expect(client.mutate('/save', { method: 'POST' })).rejects.toMatchObject({
      name: 'MutationOutcomeUnknownError', message: '网络中断，尚未确认操作是否成功。请核验状态后重试。', authoritativeState: undefined,
    })
    await expect(client.mutate('/save', {
      method: 'POST', refreshAuthoritativeState: () => Promise.reject(new TypeError('offline')),
    })).rejects.toMatchObject({ name: 'MutationOutcomeUnknownError', authoritativeState: undefined })
  })

  it('reports a non-JSON server failure as a server error', async () => {
    const client = createApiClient({ fetchImpl: vi.fn().mockResolvedValue(new Response('<h1>error</h1>', { status: 500 })) })
    await expect(client.mutate('/save', { method: 'POST' })).rejects.toMatchObject({
      name: 'ApiError', status: 500, code: 'SERVER_ERROR', message: '服务器暂时异常，请稍后核验作答状态。',
    })
  })

  it('preserves the structured domain mutation error message', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse(
        {
          error: { message: '数值题答案必须是有效数字', code: 'INVALID_ANSWER', fields: {}, request_id: 'test' },
        },
        400,
      ),
    )
    const client = createApiClient({ fetchImpl })

    await expect(client.mutate('/student/attempts/1/submit/', {
      method: 'POST',
      body: {},
    })).rejects.toMatchObject({
      status: 400,
      message: '数值题答案必须是有效数字',
      code: 'INVALID_ANSWER',
    } satisfies Partial<ApiError>)
  })

  it('routes an expired session to login with the current page as next', async () => {
    window.history.replaceState({}, '', '/teacher/courses/7/experiments/?page=2')
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse(
        {
          error: {
            code: 'AUTHENTICATION_REQUIRED',
            message: 'Authentication required.',
            fields: {},
            request_id: 'req-2',
          },
        },
        401,
      ),
    )
    const onAuthenticationRequired = vi.fn()
    const client = createApiClient({ fetchImpl, onAuthenticationRequired })

    await expect(client.get('/api/v1/auth/session/')).rejects.toBeInstanceOf(ApiError)
    expect(onAuthenticationRequired).toHaveBeenCalledWith(
      '/login/?next=%2Fteacher%2Fcourses%2F7%2Fexperiments%2F%3Fpage%3D2',
    )
  })


})
