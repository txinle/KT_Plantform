import { describe, expect, it, vi } from 'vitest'

import { createAuthenticationApi, safeNextPath } from './auth'

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('authentication API', () => {
  it('sends login credentials with the bootstrap CSRF token then refreshes the session', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ user: {} }))
    const refreshedContext = {
      csrf_token: 'new-csrf-token',
      user: {
        id: 7,
        role: 'teacher',
        display_name: '认证教师',
        username: 'teacher',
        student_number: null,
        home_url: '/teacher/courses/',
      },
    }
    const refreshBootstrapContext = vi.fn().mockResolvedValue(refreshedContext)
    const api = createAuthenticationApi({
      fetchImpl: fetchImpl as typeof fetch,
      getCsrfToken: () => 'csrf-token',
      refreshBootstrapContext,
      clearBootstrapContext: vi.fn(),
    })

    await expect(api.login('teacher', 'test-password')).resolves.toEqual(refreshedContext)

    expect(fetchImpl).toHaveBeenCalledWith(
      '/api/v1/auth/login/',
      expect.objectContaining({
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
          'X-CSRFToken': 'csrf-token',
        },
        body: JSON.stringify({ username: 'teacher', password: 'test-password' }),
      }),
    )
    expect(refreshBootstrapContext).toHaveBeenCalledWith({ force: true })
  })

  it('uses only same-origin relative next paths after login', () => {
    expect(safeNextPath('/student/?course=3#tasks', '/teacher/courses/', 'http://localhost:8000'))
      .toBe('/student/?course=3#tasks')
    expect(safeNextPath('//outside.example/', '/teacher/courses/', 'http://localhost:8000'))
      .toBe('/teacher/courses/')
    expect(safeNextPath('https://outside.example/', '/teacher/courses/', 'http://localhost:8000'))
      .toBe('/teacher/courses/')
  })
})
