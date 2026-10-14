import { ApiError, createApiClient } from '@/api/client'
import { disposeAllSessionControllers } from '@/assessment-runtime/session-controller'
import { clearOfflineAssessmentCache } from '@/assessment-runtime/offline-cache'

import {
  acceptBootstrapContext,
  clearBootstrapContext,
  getCsrfToken,
  getBootstrapContext,
  initializeBootstrapContext,
  type AuthenticatedUser,
  type BootstrapContext,
} from './bootstrap'

export const PASSWORD_CHANGE_PATH = '/password/change/'

export interface ResearchConsentDocument {
  version: string
  title: string
  paragraphs: string[]
  researcher_name: string
  researcher_email: string
  checkbox_label: string
  exit_label: string
  accept_label: string
}

interface AuthenticationApiOptions {
  fetchImpl?: typeof fetch
  getCsrfToken: () => string
  refreshBootstrapContext: (options: { force: true }) => Promise<BootstrapContext | null>
  clearBootstrapContext: () => void
}

export function createAuthenticationApi(options: AuthenticationApiOptions) {
  const client = createApiClient({ fetchImpl: options.fetchImpl, getCsrfToken: options.getCsrfToken,
    onAuthenticationRequired: () => undefined })
  const refresh = async () => {
    const context = await options.refreshBootstrapContext({ force: true })
    if (!context) throw new Error('登录状态未建立。')
    return context
  }
  return {
    getResearchConsent: () => client.get<ResearchConsentDocument>('/api/v1/auth/research-consent/'),
    async acceptResearchConsent(version: string): Promise<BootstrapContext> {
      await client.mutate('/api/v1/auth/research-consent/', { method: 'POST', body: { accepted: true, version } })
      return refresh()
    },
    async login(username: string, password: string): Promise<BootstrapContext> {
      const context = await client.mutate<BootstrapContext>('/api/v1/auth/login/', { method: 'POST', body: { username, password } })
      disposeAllSessionControllers()
      await clearOfflineAssessmentCache()
      return acceptBootstrapContext(context)
    },
    async logout(): Promise<void> {
      await client.mutate('/api/v1/auth/logout/', { method: 'POST' })
      disposeAllSessionControllers()
      await clearOfflineAssessmentCache()
      options.clearBootstrapContext()
    },
    async changePassword(currentPassword: string, newPassword1: string, newPassword2: string): Promise<BootstrapContext> {
      await client.mutate('/api/v1/auth/password/change/', { method: 'POST', body: {
        current_password: currentPassword, new_password1: newPassword1, new_password2: newPassword2,
      } })
      return refresh()
    },
    async markInitialPasswordReminderShown(): Promise<void> {
      await client.mutate('/api/v1/auth/password/reminder-shown/', { method: 'POST' })
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

export function getResearchConsent() {
  return defaultAuthenticationApi().getResearchConsent()
}

export function acceptResearchConsent(version: string) {
  return defaultAuthenticationApi().acceptResearchConsent(version)
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
  if (['/login/', '/', '/teacher/'].includes(pathname) && pathname !== user.home_url) return user.home_url
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
