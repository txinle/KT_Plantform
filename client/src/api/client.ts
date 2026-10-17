export interface PagedCollection<T> {
  items: T[]
  page: {
    number: number
    size: 20
    total_items: number
    total_pages: number
    start: number
    end: number
  }
  summary?: Record<string, unknown>
}

export interface ApiErrorBody {
  error?: {
    code?: string
    message?: string
    fields?: Record<string, unknown>
    request_id?: string
  }
}

export class ApiError extends Error {
  status: number
  code: string
  fields: Record<string, unknown>
  requestId: string
  retryAfterSeconds?: number

  constructor(status: number, body: ApiErrorBody, retryAfterSeconds?: number) {
    const structured = body.error
    super(
      structured?.message
      || (status >= 500 ? '服务器暂时异常，请稍后核验作答状态。' : '请求未完成，请刷新页面后重试。'),
    )
    this.name = 'ApiError'
    this.status = status
    this.code = structured?.code || 'API_ERROR'
    this.fields = structured?.fields || {}
    this.requestId = structured?.request_id || ''
    this.retryAfterSeconds = retryAfterSeconds
  }
}

export class MutationOutcomeUnknownError<T = unknown> extends Error {
  authoritativeState: T | undefined
  cause: unknown

  constructor(cause: unknown, authoritativeState?: T) {
    super(authoritativeState === undefined
      ? '网络中断，尚未确认操作是否成功。请核验状态后重试。'
      : '请求响应中断，已取得服务器当前状态，请核对后继续。')
    this.name = 'MutationOutcomeUnknownError'
    this.cause = cause
    this.authoritativeState = authoritativeState
  }
}

interface ClientOptions {
  fetchImpl?: typeof fetch
  getCsrfToken?: () => string
  onAuthenticationRequired?: (loginUrl: string) => void
  getOperationId?: () => string | undefined
  onResponse?: (response: Response) => void
}

interface GetOptions {
  signal?: AbortSignal
  retry?: boolean
}

interface MutationOptions<TState> {
  method: 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  body?: unknown
  signal?: AbortSignal
  keepalive?: boolean
  refreshAuthoritativeState?: () => Promise<TState>
  idempotencyKey?: string
}

interface FormMutationOptions<TState> {
  method: 'POST' | 'PUT' | 'PATCH'
  body: FormData
  signal?: AbortSignal
  refreshAuthoritativeState?: () => Promise<TState>
}

async function responseBody(response: Response): Promise<unknown> {
  if (response.status === 204) return undefined
  try { return await response.json() }
  catch (error) {
    if (response.ok) throw error
    return { error: {
      code: response.status >= 500 ? 'SERVER_ERROR' : response.status === 401 ? 'AUTHENTICATION_REQUIRED' : 'REQUEST_REJECTED',
      message: response.status >= 500 ? '服务器暂时异常，请稍后核验作答状态。' : response.status === 401 ? '登录已失效，请重新登录后继续原任务。' : '请求未完成，请刷新页面后重试。',
    } }
  }
}

export function unwrapApiData<T>(body: unknown): T {
  if (!body || typeof body !== 'object' || !Object.hasOwn(body, 'data')) throw new Error('服务器响应契约不完整，请核验状态后重试。')
  const envelope = body as { data: T; meta?: { request_id?: unknown } }
  if (typeof envelope.meta?.request_id !== 'string') throw new Error('服务器响应缺少请求标识，请核验状态后重试。')
  return envelope.data
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

export function createApiClient(options: ClientOptions = {}) {
  const fetchImpl = options.fetchImpl || globalThis.fetch.bind(globalThis)
  const getCsrfToken = options.getCsrfToken || (() => '')
  const correlationHeaders = (): Record<string, string> => {
    const operationId = options.getOperationId?.()
    return operationId && /^(?:[a-f0-9]{32}|[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})$/i.test(operationId) ? {
      'X-KT-Operation-ID': operationId,
      'X-Request-ID': crypto.randomUUID().replaceAll('-', ''),
    } : {}
  }
  const onAuthenticationRequired = options.onAuthenticationRequired || ((loginUrl: string) => {
    window.location.assign(loginUrl)
  })


  async function parse<T>(response: Response): Promise<T> {
    options.onResponse?.(response)
    const body = await responseBody(response)
    if (!response.ok) {
      const retryAfter = response.headers.get('Retry-After')
      const seconds = retryAfter === null ? NaN : /^\d+$/.test(retryAfter) ? Number(retryAfter)
        : /^[A-Za-z]{3}, /.test(retryAfter) ? Math.max(0, (Date.parse(retryAfter) - Date.now()) / 1000) : NaN
      const apiError = new ApiError(response.status, (body || {}) as ApiErrorBody, Number.isFinite(seconds) ? seconds : undefined)
      if (response.status === 401) {
        const currentPage = `${window.location.pathname}${window.location.search}${window.location.hash}`
        onAuthenticationRequired(`/login/?next=${encodeURIComponent(currentPage)}`)
      }
      throw apiError
    }
    return response.status === 204 ? undefined as T : unwrapApiData<T>(body)
  }

  return {
    async download(path: string, requestOptions: { method?: 'GET' | 'POST'; body?: FormData } = {}): Promise<{ blob: Blob; filename: string }> {
      const method = requestOptions.method || 'GET'
      const headers: Record<string, string> = { Accept: '*/*', ...correlationHeaders() }
      if (method === 'POST' && getCsrfToken()) headers['X-CSRFToken'] = getCsrfToken()
      const response = await fetchImpl(path, { method, credentials: 'same-origin', headers, body: requestOptions.body })
      if (!response.ok) await parse(response)
      const disposition = response.headers.get('Content-Disposition') || ''
      const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition)?.[1]
      const plain = /filename="([^"]+)"|filename=([^;]+)/i.exec(disposition)
      const filename = (encoded ? decodeURIComponent(encoded) : plain?.[1] || plain?.[2] || 'download').split(/[\\/]/).at(-1) || 'download'
      return { blob: await response.blob(), filename }
    },
    async get<T>(path: string, requestOptions: GetOptions = {}): Promise<T> {
      let lastError: unknown
      const attempts = requestOptions.retry === false ? 1 : 2
      for (let attempt = 0; attempt < attempts; attempt += 1) {
        try {
          const response = await fetchImpl(path, {
            method: 'GET',
            credentials: 'same-origin',
            headers: { Accept: 'application/json', ...correlationHeaders() },
            signal: requestOptions.signal,
          })
          if ([502, 503, 504].includes(response.status) && attempt + 1 < attempts) continue
          return await parse<T>(response)
        } catch (error) {
          if (isAbortError(error) || error instanceof ApiError) throw error
          if (attempt + 1 === attempts) throw new Error('无法连接服务器，请检查网络后重试。', { cause: error })
          lastError = error
        }
      }
      throw lastError
    },

    async mutate<T, TState = unknown>(
      path: string,
      mutation: MutationOptions<TState>,
    ): Promise<T> {
      try {
        const headers: Record<string, string> = {
          Accept: 'application/json',
          'Content-Type': 'application/json',
          ...correlationHeaders(),
        }
        const csrfToken = getCsrfToken()
        if (csrfToken) headers['X-CSRFToken'] = csrfToken
        if (mutation.idempotencyKey) headers['Idempotency-Key'] = mutation.idempotencyKey
        const response = await fetchImpl(path, {
          method: mutation.method,
          credentials: 'same-origin',
          headers,
          body: mutation.body === undefined ? undefined : JSON.stringify(mutation.body),
          signal: mutation.signal,
          keepalive: mutation.keepalive,
        })
        return await parse<T>(response)
      } catch (error) {
        if (error instanceof ApiError) throw error
        const authoritativeState = mutation.refreshAuthoritativeState
          ? await mutation.refreshAuthoritativeState().catch(() => undefined)
          : undefined
        throw new MutationOutcomeUnknownError(error, authoritativeState)
      }
    },

    async mutateForm<T, TState = unknown>(
      path: string,
      mutation: FormMutationOptions<TState>,
    ): Promise<T> {
      try {
        const headers: Record<string, string> = { Accept: 'application/json', ...correlationHeaders() }
        const csrfToken = getCsrfToken()
        if (csrfToken) headers['X-CSRFToken'] = csrfToken
        const response = await fetchImpl(path, {
          method: mutation.method,
          credentials: 'same-origin',
          headers,
          body: mutation.body,
          signal: mutation.signal,
        })
        return await parse<T>(response)
      } catch (error) {
        if (error instanceof ApiError) throw error
        const authoritativeState = mutation.refreshAuthoritativeState
          ? await mutation.refreshAuthoritativeState().catch(() => undefined)
          : undefined
        throw new MutationOutcomeUnknownError(error, authoritativeState)
      }
    },
  }
}
