import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/app/bootstrap', () => ({
  getBootstrapContext: () => ({ csrf_token: 'csrf-token' }),
}))

import { clearOfflineAssessmentCache, prepareOfflineQuestion } from './offline-cache'

const state = {
  session_id: 7,
  assignment: { assignment_id: 11 },
  navigation: [
    { assignment_id: 11, current: true, url: '/student/sessions/7/questions/11/' },
  ],
} as never

let workerResponse: Record<string, unknown>
let postedMessage: Record<string, unknown> | undefined
let originalServiceWorker: PropertyDescriptor | undefined
let worker: { postMessage: ReturnType<typeof vi.fn> }
let getRegistration: ReturnType<typeof vi.fn>
let register: ReturnType<typeof vi.fn>

class TestMessageChannel {
  port1 = {
    close: vi.fn(),
    onmessage: undefined as undefined | ((event: { data: Record<string, unknown> }) => void),
  }

  port2 = { replyTo: this.port1 }
}

describe('prepareOfflineQuestion', () => {
  beforeEach(() => {
    window.history.replaceState({}, '', '/student/sessions/7/questions/11/')
    Object.defineProperty(globalThis.navigator, 'onLine', { configurable: true, value: true })
    originalServiceWorker = Object.getOwnPropertyDescriptor(globalThis.navigator, 'serviceWorker')
    worker = {
      postMessage: vi.fn((message: Record<string, unknown>, ports: Array<{ replyTo: TestMessageChannel['port1'] }>) => {
        postedMessage = message
        queueMicrotask(() => ports[0]!.replyTo.onmessage?.({ data: workerResponse }))
      }),
    }
    getRegistration = vi.fn().mockResolvedValue(undefined)
    register = vi.fn().mockResolvedValue({ active: worker })
    Object.defineProperty(globalThis.navigator, 'serviceWorker', {
      configurable: true,
      value: {
        getRegistration,
        register,
        ready: Promise.resolve({ active: worker }),
      },
    })
    vi.stubGlobal('MessageChannel', TestMessageChannel)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    if (originalServiceWorker) {
      Object.defineProperty(globalThis.navigator, 'serviceWorker', originalServiceWorker)
    } else {
      Reflect.deleteProperty(globalThis.navigator, 'serviceWorker')
    }
  })

  it('preserves an optional structured worker error code for technical diagnostics', async () => {
    workerResponse = {
      ok: false,
      code: 'capture_contract_unavailable',
      error: 'capture page response did not carry its contract marker',
    }

    await expect(prepareOfflineQuestion(state)).rejects.toMatchObject({
      code: 'capture_contract_unavailable',
      message: '离线恢复准备失败：capture page response did not carry its contract marker',
    })
  })

  it('reuses an active assessment worker without registering it again', async () => {
    workerResponse = { ok: true }
    getRegistration.mockResolvedValue({ active: worker })

    await prepareOfflineQuestion(state)

    expect(getRegistration).toHaveBeenCalledWith('/student/sessions/')
    expect(register).not.toHaveBeenCalled()
    await prepareOfflineQuestion(state)
    expect(register).not.toHaveBeenCalled()
  })

  it('sends unique static resources and the loaded Vite entry identity', async () => {
    workerResponse = { ok: true }
    const resource = 'http://localhost:3000/static/spa-assets/assets/shell-C0abc123.js'
    const script = document.createElement('script')
    script.type = 'module'
    script.src = resource
    document.head.append(script)
    const entries = vi.spyOn(performance, 'getEntriesByType').mockReturnValue([
      { name: resource }, { name: resource }, { name: 'http://localhost:3000/api/v1/auth/session/' },
    ] as PerformanceEntry[])
    try {
      await prepareOfflineQuestion(state)
      expect(postedMessage).toMatchObject({ resources: [resource], buildVersion: expect.stringMatching(/^[a-f0-9]{20}$/), shell: expect.stringMatching(/capture-shell-[a-f0-9]{20}\.html$/) })
    } finally {
      entries.mockRestore()
      script.remove()
    }
  })

  it('clears assessment recovery without registering a worker or including user data', async () => {
    workerResponse = { ok: true }
    getRegistration.mockResolvedValue({ active: worker })
    await clearOfflineAssessmentCache()
    expect(postedMessage).toEqual({ type: 'clear-capture-v2' })
    expect(register).not.toHaveBeenCalled()
  })

  it('clears legacy runtime caches when an old worker cannot acknowledge clear messages', async () => {
    vi.useFakeTimers()
    worker.postMessage.mockImplementation(() => undefined)
    getRegistration.mockResolvedValue({ active: worker })
    const deleteCache = vi.fn().mockResolvedValue(true)
    vi.stubGlobal('caches', { keys: async () => ['kt-capture-v2-runtime-1', 'kt-capture-v2-runtime-2-identity', 'unrelated'], delete: deleteCache })
    try {
      const clearing = clearOfflineAssessmentCache()
      let settled = false
      void clearing.then(() => { settled = true })
      await vi.advanceTimersByTimeAsync(300)
      expect(settled).toBe(true)
      await expect(clearing).resolves.toBeUndefined()
      expect(deleteCache.mock.calls.map(([name]) => name)).toEqual(['kt-capture-v2-runtime-1', 'kt-capture-v2-runtime-2-identity'])
      expect(register).not.toHaveBeenCalled()
    } finally { vi.useRealTimers() }
  })

  it('registers only when the assessment scope has no active worker', async () => {
    workerResponse = { ok: true }

    await prepareOfflineQuestion(state)

    expect(register).toHaveBeenCalledTimes(1)
    expect(register).toHaveBeenCalledWith(
      '/assessment/process/capture-v2/offline-worker.js',
      { scope: '/student/sessions/', updateViaCache: 'none' },
    )
  })

  it('rechecks the scope and retries registration once after a transient failure', async () => {
    workerResponse = { ok: true }
    register
      .mockRejectedValueOnce(new TypeError('worker script fetch failed'))
      .mockResolvedValueOnce({ active: worker })

    await prepareOfflineQuestion(state)

    expect(getRegistration).toHaveBeenCalledTimes(2)
    expect(register).toHaveBeenCalledTimes(2)
  })

  it('hard-fails with a concise message and logs worker response diagnostics', async () => {
    const registrationError = new TypeError('worker script fetch failed')
    register.mockRejectedValue(registrationError)
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response('', {
      status: 200,
      headers: {
        'Content-Type': 'application/javascript',
        'Service-Worker-Allowed': '/student/sessions/',
      },
    }))
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('isSecureContext', true)
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)

    await expect(prepareOfflineQuestion(state)).rejects.toMatchObject({
      code: 'offline_worker_initialization_failed',
      message: '安全作答环境初始化失败，请刷新页面后重试。',
    })
    expect(register).toHaveBeenCalledTimes(2)
    expect(fetchMock).toHaveBeenCalledWith(
      '/assessment/process/capture-v2/offline-worker.js',
      expect.objectContaining({ credentials: 'same-origin' }),
    )
    expect(consoleError).toHaveBeenCalledWith(
      'Capture V2 offline worker initialization failed.',
      expect.objectContaining({
        origin: 'http://localhost:3000',
        isSecureContext: true,
        scriptStatus: 200,
        scriptContentType: 'application/javascript',
        serviceWorkerAllowed: '/student/sessions/',
      }),
    )
    consoleError.mockRestore()
  })

  it('still accepts the existing service-worker error response shape', async () => {
    workerResponse = { ok: false, error: 'Error: capture_contract_unavailable' }

    await expect(prepareOfflineQuestion(state)).rejects.toThrow(
      '离线恢复准备失败：Error: capture_contract_unavailable',
    )
  })

  it('prepares the routed question URL while Vue Router is still committing navigation', async () => {
    workerResponse = { ok: true }
    const routedState = {
      session_id: 7,
      assignment: { assignment_id: 12 },
      navigation: [
        { assignment_id: 11, current: false, url: '/student/sessions/7/questions/11/' },
        { assignment_id: 12, current: true, url: '/student/sessions/7/questions/12/' },
      ],
    } as never

    await prepareOfflineQuestion(routedState)

    expect(postedMessage).toMatchObject({
      page: 'http://localhost:3000/student/sessions/7/questions/12/',
      api: '/api/v1/student/sessions/7/questions/12/',
      bootstrap: {
        page_path: '/student/sessions/7/questions/12/',
        assessment_session_id: 7,
        assignment_id: 12,
      },
    })
  })

  it('does not unlock an uncached question when connectivity drops before preparation', async () => {
    Object.defineProperty(globalThis.navigator, 'onLine', { configurable: true, value: false })

    await expect(prepareOfflineQuestion(state)).rejects.toMatchObject({
      code: 'offline_preparation_unavailable',
    })
  })

  it('keeps a server-identified offline recovery available while disconnected', async () => {
    Object.defineProperty(globalThis.navigator, 'onLine', { configurable: true, value: false })

    await expect(prepareOfflineQuestion({
      session_id: 7,
      assignment: { assignment_id: 11 },
      navigation: [
        { assignment_id: 11, current: true, url: '/student/sessions/7/questions/11/' },
      ],
      offline_recovery: true,
    } as never)).resolves.toBeUndefined()
  })
})
