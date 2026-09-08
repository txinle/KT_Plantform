import { describe, expect, it, vi } from 'vitest'

import {
  ApiError,
  MutationOutcomeUnknownError,
  createApiClient,
} from './client'

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('API client', () => {
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

  it('preserves a retained Django mutation error message', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse(
        {
          success: false,
          error: '数值题答案必须是有效数字',
          code: 'INVALID_ANSWER',
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

  it('routes a password-change gate by structured error code', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({
        error: {
          code: 'PASSWORD_CHANGE_REQUIRED',
          message: 'This text is deliberately not inspected.',
          fields: {},
        },
      }, 403),
    )
    const onPasswordChangeRequired = vi.fn()
    const client = createApiClient({ fetchImpl, onPasswordChangeRequired })

    await expect(client.get('/api/v1/teacher/accounts/classes/')).rejects.toBeInstanceOf(ApiError)
    expect(onPasswordChangeRequired).toHaveBeenCalledWith('/password/change/')
  })
})
