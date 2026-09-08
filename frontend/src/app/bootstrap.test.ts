import { afterEach, describe, expect, it, vi } from 'vitest'

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

async function loadBootstrap() {
  vi.resetModules()
  return import('./bootstrap')
}

function installValidOfflineBootstrap() {
  document.body.innerHTML = `<script id="kt-offline-bootstrap" type="application/json">${JSON.stringify({
    version: 1,
    page_path: '/student/sessions/17/questions/23/',
    assessment_session_id: 17,
    assignment_id: 23,
    csrf_token: 'offline-csrf-token',
    user: {
      id: 7,
      role: 'student',
      display_name: '离线学生',
      username: 'S20260001',
      student_number: 'S20260001',
      home_url: '/student/',
      uses_initial_password: false,
      initial_password_reminder_pending: false,
    },
  })}</script>`
  window.history.replaceState({}, '', '/student/sessions/17/questions/23/')
}

describe('bootstrap initialization', () => {
  afterEach(() => {
    document.body.innerHTML = ''
    window.history.replaceState({}, '', '/')
    vi.restoreAllMocks()
  })

  it('loads CSRF before an authenticated session and then exposes synchronous context', async () => {
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ csrf_token: 'csrf-token' }))
      .mockResolvedValueOnce(jsonResponse({
        user: {
          id: 7,
          role: 'teacher',
          display_name: '认证教师',
          username: 'teacher',
          student_number: null,
          home_url: '/teacher/courses/',
          uses_initial_password: false,
          initial_password_reminder_pending: false,
        },
      }))
    const bootstrap = await loadBootstrap()

    const context = await bootstrap.initializeBootstrapContext({
      fetchImpl: fetchImpl as typeof fetch,
    })

    expect(context).toEqual({
      csrf_token: 'csrf-token',
      user: {
        id: 7,
        role: 'teacher',
        display_name: '认证教师',
        username: 'teacher',
        student_number: null,
        home_url: '/teacher/courses/',
        uses_initial_password: false,
        initial_password_reminder_pending: false,
      },
    })
    expect(fetchImpl).toHaveBeenNthCalledWith(
      1,
      '/api/v1/auth/csrf/',
      expect.objectContaining({ credentials: 'same-origin', method: 'GET' }),
    )
    expect(fetchImpl).toHaveBeenNthCalledWith(
      2,
      '/api/v1/auth/session/',
      expect.objectContaining({ credentials: 'same-origin', method: 'GET' }),
    )
    expect(bootstrap.getBootstrapContext()).toEqual(context)
    expect(bootstrap.getCsrfToken()).toBe('csrf-token')
  })

  it('returns a stable unauthenticated state while retaining the CSRF token for login', async () => {
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ csrf_token: 'csrf-token' }))
      .mockResolvedValueOnce(jsonResponse({ error: { code: 'AUTHENTICATION_REQUIRED' } }, 401))
    const bootstrap = await loadBootstrap()

    await expect(bootstrap.initializeBootstrapContext({
      fetchImpl: fetchImpl as typeof fetch,
    })).resolves.toBeNull()

    expect(bootstrap.getCsrfToken()).toBe('csrf-token')
    expect(() => bootstrap.getBootstrapContext()).toThrow('not authenticated')
  })

  it('uses only a page-bound offline envelope when an authorized question reloads offline', async () => {
    vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
    document.body.innerHTML = `<script id="kt-offline-bootstrap" type="application/json">${JSON.stringify({
      version: 1,
      page_path: '/student/sessions/17/questions/23/',
      assessment_session_id: 17,
      assignment_id: 23,
      csrf_token: 'offline-csrf-token',
      user: {
        id: 7,
        role: 'student',
        display_name: '离线学生',
        username: 'S20260001',
        student_number: 'S20260001',
        home_url: '/student/',
        uses_initial_password: false,
        initial_password_reminder_pending: false,
      },
    })}</script>`
    window.history.replaceState({}, '', '/student/sessions/17/questions/23/')
    const fetchImpl = vi.fn()
    const bootstrap = await loadBootstrap()

    await expect(bootstrap.initializeBootstrapContext({
      fetchImpl: fetchImpl as typeof fetch,
    })).resolves.toMatchObject({
      csrf_token: 'offline-csrf-token',
      user: { id: 7, home_url: '/student/' },
    })

    expect(fetchImpl).not.toHaveBeenCalled()
  })

  it('rejects an offline assessment bootstrap that lacks authoritative password state', async () => {
    vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
    document.body.innerHTML = `<script id="kt-offline-bootstrap" type="application/json">${JSON.stringify({
      version: 1,
      page_path: '/student/sessions/17/questions/23/',
      assessment_session_id: 17,
      assignment_id: 23,
      csrf_token: 'offline-csrf-token',
      user: {
        id: 7,
        role: 'student',
        display_name: '离线学生',
        username: 'S20260001',
        student_number: 'S20260001',
        home_url: '/student/',
      },
    })}</script>`
    window.history.replaceState({}, '', '/student/sessions/17/questions/23/')
    const fetchImpl = vi.fn().mockRejectedValue(new Error('offline'))
    const bootstrap = await loadBootstrap()

    await expect(bootstrap.initializeBootstrapContext({
      fetchImpl: fetchImpl as typeof fetch,
    })).rejects.toThrow('Bootstrap network request failed')
    expect(fetchImpl).toHaveBeenCalledOnce()
  })

  it('uses a valid offline envelope for a WebKit-shaped fetch rejection', async () => {
    installValidOfflineBootstrap()
    const fetchImpl = vi.fn().mockRejectedValue(new Error('Load failed'))
    const bootstrap = await loadBootstrap()

    await expect(bootstrap.initializeBootstrapContext({
      fetchImpl: fetchImpl as typeof fetch,
    })).resolves.toMatchObject({
      csrf_token: 'offline-csrf-token',
      user: { id: 7, home_url: '/student/' },
    })
    expect(fetchImpl).toHaveBeenCalledOnce()
  })

  it('keeps an HTTP bootstrap failure hard when an offline envelope exists', async () => {
    installValidOfflineBootstrap()
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({}, 503))
    const bootstrap = await loadBootstrap()

    await expect(bootstrap.initializeBootstrapContext({
      fetchImpl: fetchImpl as typeof fetch,
    })).rejects.toThrow('CSRF bootstrap failed with status 503')
  })

  it('keeps malformed live JSON hard when an offline envelope exists', async () => {
    installValidOfflineBootstrap()
    const fetchImpl = vi.fn().mockResolvedValue(new Response('not-json', { status: 200 }))
    const bootstrap = await loadBootstrap()

    await expect(bootstrap.initializeBootstrapContext({
      fetchImpl: fetchImpl as typeof fetch,
    })).rejects.toBeInstanceOf(SyntaxError)
  })

  it('keeps an invalid live identity hard when an offline envelope exists', async () => {
    installValidOfflineBootstrap()
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ csrf_token: 'live-csrf-token' }))
      .mockResolvedValueOnce(jsonResponse({ user: { id: 7 } }))
    const bootstrap = await loadBootstrap()

    await expect(bootstrap.initializeBootstrapContext({
      fetchImpl: fetchImpl as typeof fetch,
    })).rejects.toThrow('Session bootstrap response is invalid')
  })

  it('does not use an offline envelope whose page scope is stale', async () => {
    installValidOfflineBootstrap()
    window.history.replaceState({}, '', '/student/sessions/17/questions/24/')
    const fetchImpl = vi.fn().mockRejectedValue(new Error('Load failed'))
    const bootstrap = await loadBootstrap()

    await expect(bootstrap.initializeBootstrapContext({
      fetchImpl: fetchImpl as typeof fetch,
    })).rejects.toThrow('Bootstrap network request failed')
  })

  it('does not use an offline envelope after a live session endpoint returns 401', async () => {
    document.body.innerHTML = `<script id="kt-offline-bootstrap" type="application/json">${JSON.stringify({
      version: 1,
      page_path: '/student/sessions/17/questions/23/',
      assessment_session_id: 17,
      assignment_id: 23,
      csrf_token: 'offline-csrf-token',
      user: {
        id: 7,
        role: 'student',
        display_name: '离线学生',
        username: 'S20260001',
        student_number: 'S20260001',
        home_url: '/student/',
        uses_initial_password: false,
        initial_password_reminder_pending: false,
      },
    })}</script>`
    window.history.replaceState({}, '', '/student/sessions/17/questions/23/')
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ csrf_token: 'live-csrf-token' }))
      .mockResolvedValueOnce(jsonResponse({ error: { code: 'AUTHENTICATION_REQUIRED' } }, 401))
    const bootstrap = await loadBootstrap()

    await expect(bootstrap.initializeBootstrapContext({
      fetchImpl: fetchImpl as typeof fetch,
    })).resolves.toBeNull()

    expect(fetchImpl).toHaveBeenCalledTimes(2)
    expect(bootstrap.getCsrfToken()).toBe('live-csrf-token')
  })
})
