import { ApiError, type ApiErrorBody } from '@/api/client'

import {
  clearBootstrapContext,
  getCsrfToken,
  initializeBootstrapContext,
  type BootstrapContext,
} from './bootstrap'

interface AuthenticationApiOptions {
  fetchImpl?: typeof fetch
  getCsrfToken: () => string
  refreshBootstrapContext: (options: { force: true }) => Promise<BootstrapContext | null>
  clearBootstrapContext: () => void
}

async function responseError(response: Response): Promise<ApiError> {
  let body: ApiErrorBody = {}
  try {
    body = await response.json() as ApiErrorBody
  } catch {
    // The status still identifies an authentication transport failure.
  }
  return new ApiError(response.status, body)
}

export function createAuthenticationApi(options: AuthenticationApiOptions) {
  const fetchImpl = options.fetchImpl || globalThis.fetch.bind(globalThis)

  return {
    async login(username: string, password: string): Promise<BootstrapContext> {
      const response = await fetchImpl('/api/v1/auth/login/', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
          'X-CSRFToken': options.getCsrfToken(),
        },
        body: JSON.stringify({ username, password }),
      })
      if (!response.ok) throw await responseError(response)
      const context = await options.refreshBootstrapContext({ force: true })
      if (!context) throw new Error('登录状态未建立。')
      return context
    },

    async logout(): Promise<void> {
      const response = await fetchImpl('/api/v1/auth/logout/', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          Accept: 'application/json',
          'X-CSRFToken': options.getCsrfToken(),
        },
      })
      if (!response.ok) throw await responseError(response)
      options.clearBootstrapContext()
    },
  }
}

function defaultAuthenticationApi() {
  return createAuthenticationApi({
    getCsrfToken,
    refreshBootstrapContext: initializeBootstrapContext,
    clearBootstrapContext,
  })
}

export function login(username: string, password: string) {
  return defaultAuthenticationApi().login(username, password)
}

export function logout() {
  return defaultAuthenticationApi().logout()
}

export function safeNextPath(next: unknown, fallback: string, origin = window.location.origin): string {
  if (typeof next !== 'string' || !next.startsWith('/') || next.startsWith('//')) return fallback
  try {
    const target = new URL(next, origin)
    if (target.origin !== origin) return fallback
    return `${target.pathname}${target.search}${target.hash}`
  } catch {
    return fallback
  }
}
