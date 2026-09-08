import { describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/client'

import {
  authenticatedEntryRedirect,
  createAuthenticationApi,
  loginDestination,
  redirectPasswordChangeAuthenticationRequired,
  safeNextPath,
} from './auth'

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
        must_change_password: false,
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

  it('ignores next and enters password change when the session requires it', () => {
    const user = {
      id: 8,
      role: 'student',
      display_name: '待改密学生',
      username: 'S001',
      student_number: 'S001',
      home_url: '/password/change/',
      must_change_password: true,
    }

    expect(loginDestination(user, '/student/courses/3/')).toBe('/password/change/')
    expect(authenticatedEntryRedirect(user, '/student/courses/3/')).toBe('/password/change/')
    expect(authenticatedEntryRedirect(user, '/password/change/')).toBeUndefined()
  })

  it('posts a password change then refreshes the authoritative session context', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ user: { id: 8 } }))
    const refreshedContext = {
      csrf_token: 'new-csrf-token',
      user: {
        id: 8,
        role: 'student',
        display_name: '已改密学生',
        username: 'S001',
        student_number: 'S001',
        home_url: '/student/',
        must_change_password: false,
      },
    }
    const refreshBootstrapContext = vi.fn().mockResolvedValue(refreshedContext)
    const api = createAuthenticationApi({
      fetchImpl: fetchImpl as typeof fetch,
      getCsrfToken: () => 'csrf-token',
      refreshBootstrapContext,
      clearBootstrapContext: vi.fn(),
    })

    await expect(api.changePassword('new-password', 'new-password')).resolves.toEqual(refreshedContext)
    expect(fetchImpl).toHaveBeenCalledWith('/api/v1/auth/password/change/', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ new_password1: 'new-password', new_password2: 'new-password' }),
    }))
    expect(refreshBootstrapContext).toHaveBeenCalledWith({ force: true })
  })

  it('sends users without a password-change requirement away from that route', () => {
    const user = {
      id: 7,
      role: 'teacher',
      display_name: '认证教师',
      username: 'teacher',
      student_number: null,
      home_url: '/teacher/courses/',
      must_change_password: false,
    }

    expect(authenticatedEntryRedirect(user, '/password/change/')).toBe('/teacher/courses/')
    expect(authenticatedEntryRedirect(user, '/teacher/courses/')).toBeUndefined()
  })

  it('redirects only a structured authentication failure from password change to login', () => {
    const navigate = vi.fn()
    const authenticationRequired = new ApiError(401, { error: {
      code: 'AUTHENTICATION_REQUIRED', message: '登录状态已失效，请重新登录。', fields: {},
    } })

    expect(redirectPasswordChangeAuthenticationRequired(authenticationRequired, navigate)).toBe(true)
    expect(navigate).toHaveBeenCalledWith('/login/')
    expect(redirectPasswordChangeAuthenticationRequired(new Error('AUTHENTICATION_REQUIRED'), navigate)).toBe(false)
  })
})
