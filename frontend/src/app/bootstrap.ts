export interface AuthenticatedUser {
  id: number
  role: string
  display_name: string
  username: string
  student_number: string | null
  home_url: string
  must_change_password: boolean
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
    && typeof user.must_change_password === 'boolean'
  )
}

function offlineBootstrapContext(): BootstrapContext | undefined {
  const element = document.getElementById('kt-offline-bootstrap')
  if (!element?.textContent) return undefined

  try {
    const envelope = JSON.parse(element.textContent) as Partial<OfflineBootstrapEnvelope>
    const rawOfflineUser = (envelope as { user?: unknown }).user
    const offlineUser = rawOfflineUser && typeof rawOfflineUser === 'object'
      && !('must_change_password' in rawOfflineUser)
      ? { ...rawOfflineUser, must_change_password: false }
      : rawOfflineUser
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
  const csrfResponse = await jsonResponse(fetchImpl, '/api/v1/auth/csrf/')
  if (!csrfResponse.ok) throw new Error(`CSRF bootstrap failed with status ${csrfResponse.status}.`)
  const csrf = await csrfResponse.json() as { csrf_token?: unknown }
  if (typeof csrf.csrf_token !== 'string' || !csrf.csrf_token) {
    throw new Error('CSRF bootstrap response is invalid.')
  }
  csrfToken = csrf.csrf_token

  const sessionResponse = await jsonResponse(fetchImpl, '/api/v1/auth/session/')
  if (sessionResponse.status === 401) return null
  if (!sessionResponse.ok) throw new Error(`Session bootstrap failed with status ${sessionResponse.status}.`)
  const session = await sessionResponse.json() as { user?: unknown }
  if (!isAuthenticatedUser(session.user)) throw new Error('Session bootstrap response is invalid.')
  return { csrf_token: csrfToken, user: session.user }
}

export function getBootstrapContext(): BootstrapContext {
  if (!cachedContext) throw new Error('Vue bootstrap context is not authenticated.')
  return cachedContext
}

export function getCsrfToken(): string {
  if (!csrfToken) throw new Error('CSRF bootstrap context is missing.')
  return csrfToken
}

export function clearBootstrapContext() {
  cachedContext = undefined
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
    csrfToken = offlineContext.csrf_token
    return offlineContext
  }

  const operation = (async () => {
    try {
      const context = await initializeLiveBootstrap(fetchImpl)
      cachedContext = context || undefined
      return context
    } catch (error) {
      if (error instanceof BootstrapNetworkError && offlineContext) {
        cachedContext = offlineContext
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
