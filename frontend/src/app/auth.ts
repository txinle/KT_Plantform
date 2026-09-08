import { ApiError, type ApiErrorBody } from '@/api/client'

import {
  clearBootstrapContext,
  getCsrfToken,
  getBootstrapContext,
  initializeBootstrapContext,
  type AuthenticatedUser,
  type BootstrapContext,
} from './bootstrap'

export const PASSWORD_CHANGE_PATH = '/password/change/'

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

    async changePassword(
      currentPassword: string,
      newPassword1: string,
      newPassword2: string,
    ): Promise<BootstrapContext> {
      const response = await fetchImpl('/api/v1/auth/password/change/', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
          'X-CSRFToken': options.getCsrfToken(),
        },
        body: JSON.stringify({
          current_password: currentPassword,
          new_password1: newPassword1,
          new_password2: newPassword2,
        }),
      })
      if (!response.ok) throw await responseError(response)
      const context = await options.refreshBootstrapContext({ force: true })
      if (!context) throw new Error('登录状态未建立。')
      return context
    },

    async markInitialPasswordReminderShown(): Promise<void> {
      const response = await fetchImpl('/api/v1/auth/password/reminder-shown/', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          Accept: 'application/json',
          'X-CSRFToken': options.getCsrfToken(),
        },
      })
      if (!response.ok) throw await responseError(response)
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

export function changePassword(
  currentPassword: string,
  newPassword1: string,
  newPassword2: string,
) {
  return defaultAuthenticationApi().changePassword(
    currentPassword, newPassword1, newPassword2,
  )
}

export async function markInitialPasswordReminderShown() {
  await defaultAuthenticationApi().markInitialPasswordReminderShown()
  getBootstrapContext().user.initial_password_reminder_pending = false
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

export function loginDestination(
  user: AuthenticatedUser,
  next: unknown,
  origin = window.location.origin,
): string {
  return safeNextPath(next, user.home_url, origin)
}

export function authenticatedEntryRedirect(
  user: AuthenticatedUser,
  pathname: string,
): string | undefined {
  if (pathname === PASSWORD_CHANGE_PATH && user.role !== 'student') return user.home_url
  if (pathname === '/login/') return user.home_url
  return undefined
}

export function redirectPasswordChangeAuthenticationRequired(
  caught: unknown,
  navigate: (path: string) => void = (path) => window.location.assign(path),
): boolean {
  if (!(caught instanceof ApiError) || caught.code !== 'AUTHENTICATION_REQUIRED') return false
  navigate('/login/')
  return true
}
