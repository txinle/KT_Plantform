import { ApiError } from '@/api/client'

const DEFAULT_RENEW_BEFORE_MS = 5_000

export interface WriterLeaseClient {
  post<T>(url: string, body: Record<string, unknown>): Promise<T>
}

interface WriterLeaseResponse {
  success: boolean
  lease_token: string
  client_instance_id: string
  expires_at: string
}

interface CachedWriterLease {
  token: string
  expiresAt: number
}

export interface WriterLeaseCredentials {
  writer_lease_token: string
  writer_client_instance_id: string
}

export interface WriterLease {
  beforePointerWrite(): Promise<WriterLeaseCredentials>
  invalidate(): void
}

export interface CreateWriterLeaseOptions {
  client: WriterLeaseClient
  clientInstanceId: string
  acquireUrl: string
  renewUrl: string
  now?: () => number
  renewBeforeMs?: number
}

export class WriterLeaseConflictError extends Error {
  readonly code = 'writer_lease_conflict'
  readonly cause: unknown

  constructor(cause: unknown) {
    super('该题正在其他设备上作答，当前页面不能保存或提交。')
    this.name = 'WriterLeaseConflictError'
    this.cause = cause
  }
}

export function isWriterLeaseConflict(error: unknown): boolean {
  return error instanceof WriterLeaseConflictError
    || (error instanceof ApiError
      && error.status === 409
      && error.code === 'writer_lease_conflict')
}

function asWriterLeaseConflict(error: unknown): unknown {
  return isWriterLeaseConflict(error) && !(error instanceof WriterLeaseConflictError)
    ? new WriterLeaseConflictError(error)
    : error
}

function cacheLease(response: WriterLeaseResponse, clientInstanceId: string): CachedWriterLease {
  const expiresAt = Date.parse(response.expires_at)
  if (
    !response.success
    || !response.lease_token
    || response.client_instance_id !== clientInstanceId
    || !Number.isFinite(expiresAt)
  ) {
    throw new Error('Writer lease response is invalid.')
  }
  return { token: response.lease_token, expiresAt }
}

export function createWriterLease(options: CreateWriterLeaseOptions): WriterLease {
  const now = options.now || (() => Date.now())
  const renewBeforeMs = options.renewBeforeMs ?? DEFAULT_RENEW_BEFORE_MS
  let cached: CachedWriterLease | undefined
  let pending: Promise<CachedWriterLease> | undefined

  async function request(url: string, body: Record<string, unknown>) {
    try {
      const response = await options.client.post<WriterLeaseResponse>(url, body)
      cached = cacheLease(response, options.clientInstanceId)
      return cached
    } catch (error) {
      throw asWriterLeaseConflict(error)
    }
  }

  function acquire() {
    return request(options.acquireUrl, {
      client_instance_id: options.clientInstanceId,
    })
  }

  function renew(active: CachedWriterLease) {
    return request(options.renewUrl, {
      lease_token: active.token,
      client_instance_id: options.clientInstanceId,
    })
  }

  async function ensureLease() {
    if (!cached || cached.expiresAt <= now()) return acquire()
    if (cached.expiresAt - now() > renewBeforeMs) return cached
    try {
      return await renew(cached)
    } catch (error) {
      if (!isWriterLeaseConflict(error)) throw error
      cached = undefined
      return acquire()
    }
  }

  return {
    async beforePointerWrite() {
      if (!pending) {
        pending = ensureLease().finally(() => {
          pending = undefined
        })
      }
      const active = await pending
      return {
        writer_lease_token: active.token,
        writer_client_instance_id: options.clientInstanceId,
      }
    },
    invalidate() {
      cached = undefined
    },
  }
}
