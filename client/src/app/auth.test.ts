import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/client'
import * as bootstrap from './bootstrap'
import * as offlineCache from '@/assessment-runtime/offline-cache'
import * as sessionControllers from '@/assessment-runtime/session-controller'

import {
  authenticatedEntryRedirect,
  createAuthenticationApi,
  loginDestination,
  redirectPasswordChangeAuthenticationRequired,
  safeNextPath,
} from './auth'

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(status < 400 ? { data: body, meta: { request_id: 'test-request' } } : body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('bounded login retries', () => {
  const context = {
    csrf_token: 'rotated-csrf',
    user: { id: 8, role: 'student', display_name: '学生', username: 'S001', student_number: 'S001',
      home_url: '/student/', uses_initial_password: false, initial_password_reminder_pending: false,
      research_consent_required: false, research_consent_version: '2026-09-14' },
  }
  function busy(seconds = '1') {
    return new Response(JSON.stringify({ error: { code: 'LOGIN_BUSY' } }), {
      status: 429, headers: { 'Content-Type': 'application/json', 'Retry-After': seconds },
    })
  }
  function apiFor(fetchImpl: typeof fetch) {
    return createAuthenticationApi({ fetchImpl, getCsrfToken: () => 'csrf',
      refreshBootstrapContext: vi.fn(), clearBootstrapContext: vi.fn() })
  }
  beforeEach(() => {
    vi.useFakeTimers()
    bootstrap.clearBootstrapContext()
    vi.spyOn(Math, 'random').mockReturnValue(0.5)
    vi.spyOn(offlineCache, 'clearOfflineAssessmentCache').mockResolvedValue()
    vi.spyOn(sessionControllers, 'disposeAllSessionControllers')
    vi.spyOn(bootstrap, 'acceptBootstrapContext')
  })
  afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); bootstrap.clearBootstrapContext() })

  it.each([false, true])('supports login without AbortSignal.throwIfAborted, cancelled: %s', async cancelled => {
    const descriptor = Object.getOwnPropertyDescriptor(AbortSignal.prototype, 'throwIfAborted')!
    Object.defineProperty(AbortSignal.prototype, 'throwIfAborted', { configurable: true, value: undefined })
    try {
      const controller = new AbortController()
      if (cancelled) controller.abort()
      const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(context))
      const result = apiFor(fetchImpl).login('S001', 'password', { signal: controller.signal })
      if (cancelled) {
        await expect(result).rejects.toMatchObject({ name: 'AbortError' })
        expect(fetchImpl).not.toHaveBeenCalled()
      } else {
        await expect(result).resolves.toEqual(context)
        expect(fetchImpl).toHaveBeenCalledOnce()
        expect(bootstrap.acceptBootstrapContext).toHaveBeenCalledOnce()
      }
      expect(vi.getTimerCount()).toBe(0)
    } finally { Object.defineProperty(AbortSignal.prototype, 'throwIfAborted', descriptor) }
  })

  it('shares a single flow with fixed credentials and additive capped jitter, accepting success once', async () => {
    const times: number[] = []
    const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async () => {
      times.push(Date.now())
      return times.length <= 6 ? busy() : jsonResponse(context)
    })
    const store = vi.spyOn(Storage.prototype, 'setItem')
    const api = apiFor(fetchImpl)
    const first = api.login('S001', 'original-password')
    const duplicate = api.login('changed-account', 'changed-password')
    expect(duplicate).toBe(first)
    await vi.advanceTimersByTimeAsync(15000)
    await expect(first).resolves.toEqual(context)
    expect(times.slice(1).map((time, index) => time - times[index]!)).toEqual([1125, 1250, 1500, 2000, 2000, 2000])
    expect(fetchImpl.mock.calls.every(([, options]) => options?.body === JSON.stringify({ username: 'S001', password: 'original-password' }))).toBe(true)
    expect(offlineCache.clearOfflineAssessmentCache).toHaveBeenCalledOnce()
    expect(sessionControllers.disposeAllSessionControllers).toHaveBeenCalledOnce()
    expect(bootstrap.acceptBootstrapContext).toHaveBeenCalledOnce()
    expect(store).not.toHaveBeenCalled()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('cancels a busy wait without replaying or accepting context', async () => {
    const controller = new AbortController()
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(busy())
    const pending = apiFor(fetchImpl).login('S001', 'password', { signal: controller.signal })
    const rejected = expect(pending).rejects.toMatchObject({ name: 'AbortError' })
    await vi.advanceTimersByTimeAsync(100)
    controller.abort()
    await rejected
    await vi.advanceTimersByTimeAsync(30000)
    expect(fetchImpl).toHaveBeenCalledOnce()
    expect(bootstrap.acceptBootstrapContext).not.toHaveBeenCalled()
    expect(offlineCache.clearOfflineAssessmentCache).not.toHaveBeenCalled()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('cancels an in-flight request and allows a later independent login', async () => {
    const controller = new AbortController()
    const fetchImpl = vi.fn<typeof fetch>().mockImplementationOnce((_path, options) => new Promise((_resolve, reject) => {
      options!.signal!.addEventListener('abort', () => reject(options!.signal!.reason), { once: true })
    })).mockResolvedValueOnce(jsonResponse(context))
    const api = apiFor(fetchImpl)
    const pending = api.login('S001', 'password', { signal: controller.signal })
    const rejected = expect(pending).rejects.toMatchObject({ name: 'AbortError' })
    controller.abort()
    await rejected
    expect(fetchImpl.mock.calls[0]![1]!.signal!.aborted).toBe(true)
    await expect(api.login('S001', 'password')).resolves.toEqual(context)
    expect(fetchImpl).toHaveBeenCalledTimes(2)
    expect(bootstrap.acceptBootstrapContext).toHaveBeenCalledOnce()
  })

  it('does not send a request for an already cancelled login', async () => {
    const controller = new AbortController()
    controller.abort()
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(context))
    await expect(apiFor(fetchImpl).login('S001', 'password', { signal: controller.signal })).rejects.toMatchObject({ name: 'AbortError' })
    expect(fetchImpl).not.toHaveBeenCalled()
  })

  it('aborts a later attempt at the original thirty-second deadline using only its remaining time', async () => {
    const times: number[] = []
    const started = Date.now()
    const fetchImpl = vi.fn<typeof fetch>().mockImplementationOnce(async () => {
      times.push(Date.now())
      await new Promise(resolve => setTimeout(resolve, 25000))
      return busy()
    }).mockImplementationOnce((_path, options) => {
      times.push(Date.now())
      return new Promise((_resolve, reject) => options!.signal!.addEventListener('abort', () => reject(options!.signal!.reason), { once: true }))
    })
    const pending = apiFor(fetchImpl).login('S001', 'password')
    const rejected = expect(pending).rejects.toMatchObject({ name: 'TimeoutError' })
    await vi.advanceTimersByTimeAsync(30000)
    await rejected
    expect(times.map(time => time - started)).toEqual([0, 26125])
    expect(fetchImpl.mock.calls[1]![1]!.signal!.aborted).toBe(true)
    expect(fetchImpl).toHaveBeenCalledTimes(2)
    expect(bootstrap.acceptBootstrapContext).not.toHaveBeenCalled()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('does not schedule a retry which cannot fit in the total budget', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(busy('30'))
    await expect(apiFor(fetchImpl).login('S001', 'password')).rejects.toMatchObject({ status: 429, code: 'LOGIN_BUSY' })
    expect(fetchImpl).toHaveBeenCalledOnce()
    expect(vi.getTimerCount()).toBe(0)
  })

  it.each([
    [400, 'VALIDATION_ERROR'], [403, 'CSRF_FAILED'], [429, 'REQUEST_REJECTED'],
    [429, 'START_BUSY'], [503, 'LOGIN_BUSY'],
  ])('never replays rejected credentials or other status/code combinations (%s %s)', async (status, code) => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ error: { code } }, status))
    await expect(apiFor(fetchImpl).login('S001', 'password')).rejects.toMatchObject({ status, code })
    expect(fetchImpl).toHaveBeenCalledOnce()
    expect(bootstrap.acceptBootstrapContext).not.toHaveBeenCalled()
  })

  it.each(['network', 'malformed-success'])('never replays an unknown %s outcome', async (failure) => {
    const fetchImpl = vi.fn<typeof fetch>()
    if (failure === 'network') fetchImpl.mockRejectedValue(new TypeError('network'))
    else fetchImpl.mockResolvedValue(new Response('{"user": {"id": 8}}'))
    await expect(apiFor(fetchImpl).login('S001', 'password')).rejects.toMatchObject({ name: 'MutationOutcomeUnknownError' })
    expect(fetchImpl).toHaveBeenCalledOnce()
    expect(bootstrap.acceptBootstrapContext).not.toHaveBeenCalled()
  })
})

describe('authentication API', () => {
  it('clears cached assessment pages after logout without touching the process outbox', async () => {
    const postMessage = vi.fn((_message, ports: MessagePort[]) => ports[0]?.postMessage({ ok: true }))
    const original = Object.getOwnPropertyDescriptor(navigator, 'serviceWorker')
    Object.defineProperty(navigator, 'serviceWorker', { configurable: true, value: {
      getRegistration: vi.fn().mockResolvedValue({ active: { postMessage } }),
    } })
    try {
      const clear = vi.fn()
      const api = createAuthenticationApi({
        fetchImpl: vi.fn().mockResolvedValue(jsonResponse({ success: true })) as typeof fetch,
        getCsrfToken: () => 'csrf',
        refreshBootstrapContext: vi.fn(),
        clearBootstrapContext: clear,
      })
      await api.logout()
      expect(postMessage).toHaveBeenCalledWith({ type: 'clear-capture-v2' }, expect.any(Array))
      expect(clear).toHaveBeenCalledOnce()
    } finally {
      if (original) Object.defineProperty(navigator, 'serviceWorker', original)
      else Reflect.deleteProperty(navigator, 'serviceWorker')
    }
  })

  it('loads and accepts the current research consent through the authenticated API', async () => {
    const consent = {
      version: '2026-09-14',
      title: '研究参与说明与知情同意',
      paragraphs: ['说明正文'],
      researcher_name: '唐鑫乐',
      researcher_email: 'txinle@163.com',
      checkbox_label: '我已阅读并理解上述说明',
      exit_label: '退出',
      accept_label: '同意并继续',
    }
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(jsonResponse(consent))
      .mockResolvedValueOnce(jsonResponse({ user: { research_consent_required: false } }))
    const refreshedContext = {
      csrf_token: 'csrf-token',
      user: { research_consent_required: false },
    }
    const api = createAuthenticationApi({
      fetchImpl: fetchImpl as typeof fetch,
      getCsrfToken: () => 'csrf-token',
      refreshBootstrapContext: vi.fn().mockResolvedValue(refreshedContext),
      clearBootstrapContext: vi.fn(),
    })

    await expect(api.getResearchConsent()).resolves.toEqual(consent)
    await expect(api.acceptResearchConsent(consent.version)).resolves.toEqual(refreshedContext)
    expect(fetchImpl).toHaveBeenNthCalledWith(
      2,
      '/api/v1/auth/research-consent/',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ accepted: true, version: consent.version }),
        headers: expect.objectContaining({ 'X-CSRFToken': 'csrf-token' }),
      }),
    )
  })

  it('accepts rotated CSRF and authoritative identity directly from login without refreshing', async () => {
    const refreshedContext = {
      csrf_token: 'new-csrf-token',
      user: {
        id: 7,
        role: 'teacher',
        display_name: '认证教师',
        username: 'teacher',
        student_number: null,
        home_url: '/teacher/courses/',
      uses_initial_password: false,
      initial_password_reminder_pending: false,
      research_consent_required: false,
      research_consent_version: '2026-09-14',
      },
    }
    const refreshBootstrapContext = vi.fn().mockResolvedValue(refreshedContext)
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(refreshedContext))
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
    expect(refreshBootstrapContext).not.toHaveBeenCalled()
    expect(fetchImpl).toHaveBeenCalledOnce()
  })

  it('uses only same-origin relative next paths after login', () => {
    expect(safeNextPath('/student/?course=3#tasks', '/teacher/courses/', 'http://localhost:8000'))
      .toBe('/student/?course=3#tasks')
    expect(safeNextPath('//outside.example/', '/teacher/courses/', 'http://localhost:8000'))
      .toBe('/teacher/courses/')
    expect(safeNextPath('https://outside.example/', '/teacher/courses/', 'http://localhost:8000'))
      .toBe('/teacher/courses/')
  })

  it('preserves a safe next path when the student still uses the initial password', () => {
    const user = {
      id: 8,
      role: 'student',
      display_name: '待改密学生',
      username: 'S001',
      student_number: 'S001',
      home_url: '/student/',
      uses_initial_password: true,
      initial_password_reminder_pending: true,
      research_consent_required: true,
      research_consent_version: '2026-09-14',
    }

    expect(loginDestination(user, '/student/courses/3/')).toBe('/student/courses/3/')
    expect(authenticatedEntryRedirect(user, '/student/courses/3/')).toBeUndefined()
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
        uses_initial_password: false,
        initial_password_reminder_pending: false,
        research_consent_required: false,
        research_consent_version: '2026-09-14',
      },
    }
    const refreshBootstrapContext = vi.fn().mockResolvedValue(refreshedContext)
    const api = createAuthenticationApi({
      fetchImpl: fetchImpl as typeof fetch,
      getCsrfToken: () => 'csrf-token',
      refreshBootstrapContext,
      clearBootstrapContext: vi.fn(),
    })

    await expect(api.changePassword('current-password', 'new-password', 'new-password')).resolves.toEqual(refreshedContext)
    expect(fetchImpl).toHaveBeenCalledWith('/api/v1/auth/password/change/', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({
        current_password: 'current-password',
        new_password1: 'new-password',
        new_password2: 'new-password',
      }),
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
        uses_initial_password: false,
        initial_password_reminder_pending: false,
        research_consent_required: false,
        research_consent_version: '2026-09-14',
    }

    expect(authenticatedEntryRedirect(user, '/password/change/')).toBe('/teacher/courses/')
    expect(authenticatedEntryRedirect(user, '/')).toBe('/teacher/courses/')
    expect(authenticatedEntryRedirect(user, '/teacher/')).toBe('/teacher/courses/')
    expect(authenticatedEntryRedirect(user, '/teacher/courses/')).toBeUndefined()
  })

  it('confirms a rendered initial-password reminder without refreshing the session', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    const api = createAuthenticationApi({
      fetchImpl: fetchImpl as typeof fetch,
      getCsrfToken: () => 'csrf-token',
      refreshBootstrapContext: vi.fn(),
      clearBootstrapContext: vi.fn(),
    })

    await expect(api.markInitialPasswordReminderShown()).resolves.toBeUndefined()
    expect(fetchImpl).toHaveBeenCalledWith(
      '/api/v1/auth/password/reminder-shown/',
      expect.objectContaining({
        method: 'POST',
        credentials: 'same-origin',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'X-CSRFToken': 'csrf-token' },
      }),
    )
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


it('does not refresh authentication state from a mutation missing the success envelope', async () => {
  const refresh = vi.fn()
  const api = createAuthenticationApi({ fetchImpl: vi.fn().mockResolvedValue(new Response(JSON.stringify({ user: { id: 2 } }))) as typeof fetch, getCsrfToken: () => 'csrf', refreshBootstrapContext: refresh, clearBootstrapContext: vi.fn() })
  await expect(api.login('user', 'password')).rejects.toThrow('尚未确认')
  expect(refresh).not.toHaveBeenCalled()
})
