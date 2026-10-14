import { unwrapApiData } from '@/api/client'
export interface AuthenticatedUser {
  id: number
  role: string
  display_name: string
  username: string
  student_number: string | null
  home_url: string
  uses_initial_password: boolean
  initial_password_reminder_pending: boolean
  research_consent_required: boolean
  research_consent_version: string
}

export interface BootstrapContext {
  csrf_token: string
  user: AuthenticatedUser
}

export interface OfflineBootstrapEnvelope extends BootstrapContext {
  version: 1
  page_path: string
  assessment_session_id: number
  assignment_id: number
}

interface BootstrapInitializationOptions {
  fetchImpl?: typeof fetch
  force?: boolean
}

class BootstrapNetworkError extends Error {
  constructor(path: string, readonly cause: unknown) {
    const detail = cause instanceof Error ? cause.message : String(cause)
    super(`Bootstrap network request failed for ${path}: ${detail}`)
    this.name = 'BootstrapNetworkError'
  }
}

let cachedContext: BootstrapContext | undefined
let offlineRecoveryPagePath: string | undefined
let csrfToken: string | undefined
let pendingInitialization: Promise<BootstrapContext | null> | undefined

function isAuthenticatedUser(value: unknown): value is AuthenticatedUser {
  if (!value || typeof value !== 'object') return false
  const user = value as Record<string, unknown>
  return (
    typeof user.id === 'number'
    && typeof user.role === 'string'
    && typeof user.display_name === 'string'
    && typeof user.username === 'string'
    && (typeof user.student_number === 'string' || user.student_number === null)
    && typeof user.home_url === 'string'
    && user.home_url.startsWith('/')
    && typeof user.uses_initial_password === 'boolean'
    && typeof user.initial_password_reminder_pending === 'boolean'
    && typeof user.research_consent_required === 'boolean'
    && typeof user.research_consent_version === 'string'
  )
}

function offlineBootstrapContext(): BootstrapContext | undefined {
  const element = document.getElementById('kt-offline-bootstrap')
  if (!element?.textContent) return undefined

  try {
    const envelope = JSON.parse(element.textContent) as Partial<OfflineBootstrapEnvelope>
    const offlineUser = (envelope as { user?: unknown }).user
    const match = /^\/student\/sessions\/(\d+)\/questions\/(\d+)\/$/.exec(window.location.pathname)
    if (
      !match
      || envelope.version !== 1
      || envelope.page_path !== window.location.pathname
      || envelope.assessment_session_id !== Number(match[1])
      || envelope.assignment_id !== Number(match[2])
      || typeof envelope.csrf_token !== 'string'
      || !envelope.csrf_token
      || !isAuthenticatedUser(offlineUser)
    ) return undefined
    return { csrf_token: envelope.csrf_token, user: offlineUser }
  } catch {
    return undefined
  }
}

async function jsonResponse(fetchImpl: typeof fetch, path: string): Promise<Response> {
  try {
    return await fetchImpl(path, {
      method: 'GET',
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    })
  } catch (error) {
    throw new BootstrapNetworkError(path, error)
  }
}

async function initializeLiveBootstrap(fetchImpl: typeof fetch): Promise<BootstrapContext | null> {
  const response = await jsonResponse(fetchImpl, '/api/v1/auth/bootstrap/')
  if (!response.ok) throw new Error(`Authentication bootstrap failed with status ${response.status}.`)
  const context = unwrapApiData<{ csrf_token?: unknown, user?: unknown }>(await response.json())
  if (typeof context.csrf_token !== 'string' || !context.csrf_token
    || (context.user !== null && !isAuthenticatedUser(context.user))) {
    throw new Error('Authentication bootstrap response is invalid.')
  }
  csrfToken = context.csrf_token
  return context.user === null ? null : { csrf_token: csrfToken, user: context.user }
}

export function acceptBootstrapContext(context: BootstrapContext): BootstrapContext {
  if (typeof context.csrf_token !== 'string' || !context.csrf_token || !isAuthenticatedUser(context.user)) {
    throw new Error('Authentication bootstrap response is invalid.')
  }
  cachedContext = context
  csrfToken = context.csrf_token
  offlineRecoveryPagePath = undefined
  return context
}

export function getBootstrapContext(): BootstrapContext {
  if (!cachedContext) throw new Error('Vue bootstrap context is not authenticated.')
  return cachedContext
}

export function isOfflineBootstrapRecovery(pagePath = window.location.pathname): boolean {
  return offlineRecoveryPagePath === pagePath
}

export function getCsrfToken(): string {
  if (!csrfToken) throw new Error('CSRF bootstrap context is missing.')
  return csrfToken
}

export function clearBootstrapContext() {
  cachedContext = undefined
  offlineRecoveryPagePath = undefined
  csrfToken = undefined
  pendingInitialization = undefined
}

export async function initializeBootstrapContext(
  options: BootstrapInitializationOptions = {},
): Promise<BootstrapContext | null> {
  if (!options.force && cachedContext) return cachedContext
  if (!options.force && pendingInitialization) return pendingInitialization

  const fetchImpl = options.fetchImpl || globalThis.fetch.bind(globalThis)
  const offlineContext = offlineBootstrapContext()
  if (offlineContext && !navigator.onLine) {
    cachedContext = offlineContext
    offlineRecoveryPagePath = window.location.pathname
    csrfToken = offlineContext.csrf_token
    return offlineContext
  }

  const operation = (async () => {
    try {
      const context = await initializeLiveBootstrap(fetchImpl)
      const previousUserId = cachedContext?.user.id ?? offlineContext?.user.id
      if (!context || (previousUserId !== undefined && previousUserId !== context.user.id)) {
        const { clearOfflineAssessmentCache } = await import('@/assessment-runtime/offline-cache')
        await clearOfflineAssessmentCache()
      }
      cachedContext = context || undefined
      offlineRecoveryPagePath = undefined
      return context
    } catch (error) {
      if (error instanceof BootstrapNetworkError && offlineContext) {
        cachedContext = offlineContext
        offlineRecoveryPagePath = window.location.pathname
        csrfToken = offlineContext.csrf_token
        return offlineContext
      }
      throw error
    }
  })()
  pendingInitialization = operation
  try {
    return await operation
  } finally {
    if (pendingInitialization === operation) pendingInitialization = undefined
  }
}
