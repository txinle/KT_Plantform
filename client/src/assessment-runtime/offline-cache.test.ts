import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const context = vi.hoisted(() => ({ userId: 3, buildVersion: 'a'.repeat(20), runtimeLoaded: false }))
vi.mock('./StudentAssessment.vue', () => { context.runtimeLoaded = true; return { default: {} } })
vi.mock('@/app/bootstrap', () => ({
  getBootstrapContext: () => ({ csrf_token: 'csrf-token', user: { id: context.userId } }),
}))
vi.mock('./build-version', () => ({
  get assessmentBuildVersion() { return context.buildVersion },
  get assessmentShellUrl() { return `/static/spa-assets/capture-shell-${context.buildVersion}.html` },
}))

import { clearOfflineAssessmentCache, prepareOfflineQuestion, prepareOfflineCommonResources } from './offline-cache'

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
  it('does not persist a live writer lease as reusable offline authority', async () => {
    workerResponse = { ok: true, commonResourcesPrepared: true }
    const input = { ...state as object, initial_writer_lease: { writer_lease_token: 'live-only',
      writer_client_instance_id: 'client', expires_at: '2099-01-01T00:00:00Z' } }
    await prepareOfflineQuestion(input as never)
    expect(postedMessage?.state).not.toHaveProperty('initial_writer_lease')
    expect(input).toHaveProperty('initial_writer_lease.writer_lease_token', 'live-only')
  })
  it('warms only public shell assets from the task list and deduplicates repeated warmups', async () => {
    workerResponse = { ok: true, commonResourcesPrepared: true }
    window.history.replaceState({}, '', '/student/courses/3/')
    await Promise.all([prepareOfflineCommonResources(), prepareOfflineCommonResources()])
    expect(context.runtimeLoaded).toBe(true)
    expect(worker.postMessage).toHaveBeenCalledOnce()
    expect(postedMessage).toMatchObject({ type: 'prepare-capture-v2-common', shell: expect.stringContaining('/static/'), buildVersion: context.buildVersion })
    expect(postedMessage).not.toHaveProperty('bootstrap')
    expect(postedMessage).not.toHaveProperty('state')
    expect(postedMessage).not.toHaveProperty('api')
  })
  beforeEach(() => {
    context.userId = 3
    context.buildVersion = 'a'.repeat(20)
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
    register = vi.fn().mockResolvedValue({ active: worker, update: vi.fn().mockResolvedValue(undefined) })
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

  it('prepares through the activated assessment registration when router entry leaves document ready outside its scope', async () => {
    workerResponse = { ok: true, commonResourcesPrepared: true }
    window.history.replaceState({}, '', '/student/courses/3/')
    window.history.pushState({}, '', '/student/sessions/7/questions/11/')
    Object.defineProperty(globalThis.navigator, 'serviceWorker', { configurable: true, value: {
      getRegistration, register, ready: new Promise(() => undefined),
    } })
    const result = await Promise.race([prepareOfflineQuestion(state).then(() => 'prepared'), new Promise(resolve => setTimeout(() => resolve('blocked'), 20))])
    expect(result).toBe('prepared')
    expect(worker.postMessage).toHaveBeenCalledWith(expect.objectContaining({ type: 'prepare-capture-v2' }), expect.any(Array))
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

  it('checks for an updated worker once before reusing it across question preparation', async () => {
    workerResponse = { ok: true }
    getRegistration.mockResolvedValue({ active: worker })

    await prepareOfflineQuestion(state)

    expect(register).toHaveBeenCalledTimes(1)
    await prepareOfflineQuestion(state)
    expect(register).toHaveBeenCalledTimes(1)
  })

  it('replaces an already-active worker that rejects the current router scope', async () => {
    workerResponse = { ok: true }
    const retired = { postMessage: vi.fn((_message: unknown, ports: Array<{ replyTo: TestMessageChannel['port1'] }>) => {
      queueMicrotask(() => ports[0]!.replyTo.onmessage?.({ data: { ok: false, error: 'Error: invalid_scope' } }))
    }) }
    getRegistration.mockResolvedValue({ active: retired })

    await expect(prepareOfflineQuestion(state)).resolves.toBeUndefined()

    expect(retired.postMessage).not.toHaveBeenCalled()
    expect(worker.postMessage).toHaveBeenCalled()
  })

  it('waits for the registered worker update before selecting its active instance', async () => {
    workerResponse = { ok: true }
    const retired = { postMessage: vi.fn((_message: unknown, ports: Array<{ replyTo: TestMessageChannel['port1'] }>) => {
      queueMicrotask(() => ports[0]!.replyTo.onmessage?.({ data: { ok: false, error: 'Error: invalid_scope' } }))
    }) }
    const registration = { active: retired as typeof worker, update: vi.fn(async () => { registration.active = worker }) }
    register.mockResolvedValue(registration)

    await expect(prepareOfflineQuestion(state)).resolves.toBeUndefined()

    expect(registration.update).toHaveBeenCalledTimes(1)
    expect(retired.postMessage).not.toHaveBeenCalled()
  })

  it('does not fall back to an unchecked active worker when both update checks fail', async () => {
    workerResponse = { ok: true }
    getRegistration.mockResolvedValue({ active: worker })
    register.mockRejectedValue(new TypeError('worker script fetch failed'))
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('')))
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    try {
      await expect(prepareOfflineQuestion(state)).rejects.toMatchObject({ code: 'offline_worker_initialization_failed' })
      expect(worker.postMessage).not.toHaveBeenCalled()
      expect(register).toHaveBeenCalledTimes(2)
    } finally { consoleError.mockRestore() }
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

  it('reuses acknowledged common resources while sending each entered question state and bootstrap', async () => {
    workerResponse = { ok: true, commonResourcesPrepared: true }
    const resource = 'http://localhost:3000/static/js/assessment.js'
    const entries = vi.spyOn(performance, 'getEntriesByType').mockReturnValue([{ name: resource }] as PerformanceEntry[])
    try {
      await prepareOfflineQuestion(state)
      await prepareOfflineQuestion({ session_id: 7, assignment: { assignment_id: 12 },
        navigation: [{ current: true, url: '/student/sessions/7/questions/12/' }],
      } as never)
      expect(worker.postMessage).toHaveBeenCalledTimes(2)
      expect(worker.postMessage.mock.calls[0]![0]).toMatchObject({ resources: [resource] })
      expect(postedMessage).toMatchObject({ resources: [], api: '/api/v1/student/sessions/7/questions/12/',
        state: { assignment: { assignment_id: 12 } }, bootstrap: { assignment_id: 12 } })
    } finally { entries.mockRestore() }
  })

  it('sends newly loaded static resources after common preparation', async () => {
    workerResponse = { ok: true, commonResourcesPrepared: true }
    const first = 'http://localhost:3000/static/js/assessment.js'
    const second = 'http://localhost:3000/static/js/question-media.js'
    const entries = vi.spyOn(performance, 'getEntriesByType').mockReturnValue([{ name: first }] as PerformanceEntry[])
    try {
      await prepareOfflineQuestion(state)
      entries.mockReturnValue([{ name: first }, { name: second }] as PerformanceEntry[])
      await prepareOfflineQuestion(state)
      expect(postedMessage).toMatchObject({ resources: [second] })
    } finally { entries.mockRestore() }
  })

  it('keeps sending all common resources to an active worker without manifest support', async () => {
    workerResponse = { ok: true }
    const resource = 'http://localhost:3000/static/js/assessment.js'
    const entries = vi.spyOn(performance, 'getEntriesByType').mockReturnValue([{ name: resource }] as PerformanceEntry[])
    try {
      await prepareOfflineQuestion(state)
      await prepareOfflineQuestion(state)
      expect(postedMessage).toMatchObject({ resources: [resource] })
    } finally { entries.mockRestore() }
  })

  it('retries common resources after failed question readiness', async () => {
    const resource = 'http://localhost:3000/static/js/assessment.js'
    const entries = vi.spyOn(performance, 'getEntriesByType').mockReturnValue([{ name: resource }] as PerformanceEntry[])
    try {
      workerResponse = { ok: false, error: 'QuotaExceededError' }
      await expect(prepareOfflineQuestion(state)).rejects.toThrow('QuotaExceededError')
      workerResponse = { ok: true, commonResourcesPrepared: true }
      await prepareOfflineQuestion(state)
      expect(postedMessage).toMatchObject({ resources: [resource] })
      await prepareOfflineQuestion(state)
      expect(postedMessage).toMatchObject({ resources: [] })
    } finally { entries.mockRestore() }
  })

  it('isolates common preparation by account, assessment session and build and resets it on clear', async () => {
    workerResponse = { ok: true, commonResourcesPrepared: true }
    const resource = 'http://localhost:3000/static/js/assessment.js'
    const entries = vi.spyOn(performance, 'getEntriesByType').mockReturnValue([{ name: resource }] as PerformanceEntry[])
    try {
      await prepareOfflineQuestion(state)
      context.userId = 4
      await prepareOfflineQuestion(state)
      expect(postedMessage).toMatchObject({ resources: [resource] })
      await prepareOfflineQuestion({ session_id: 8, assignment: { assignment_id: 11 },
        navigation: [{ current: true, url: '/student/sessions/8/questions/11/' }],
      } as never)
      expect(postedMessage).toMatchObject({ resources: [resource] })
      context.buildVersion = 'b'.repeat(20)
      await prepareOfflineQuestion(state)
      expect(postedMessage).toMatchObject({ resources: [resource], buildVersion: 'b'.repeat(20) })
      getRegistration.mockResolvedValue({ active: worker })
      await clearOfflineAssessmentCache()
      await prepareOfflineQuestion(state)
      expect(postedMessage).toMatchObject({ resources: [resource] })
      await prepareOfflineQuestion(state)
      expect(postedMessage).toMatchObject({ resources: [] })
    } finally { entries.mockRestore() }
  })

  it('does not restore common readiness when clear races with an earlier acknowledgement', async () => {
    workerResponse = { ok: true, commonResourcesPrepared: true }
    getRegistration.mockResolvedValue({ active: worker })
    const resource = 'http://localhost:3000/static/js/assessment.js'
    const entries = vi.spyOn(performance, 'getEntriesByType').mockReturnValue([{ name: resource }] as PerformanceEntry[])
    let acknowledge!: () => void
    worker.postMessage.mockImplementationOnce((_message, ports: Array<{ replyTo: TestMessageChannel['port1'] }>) => {
      acknowledge = () => ports[0]!.replyTo.onmessage?.({ data: { ok: true, commonResourcesPrepared: true } })
    })
    try {
      const preparing = prepareOfflineQuestion(state)
      await vi.waitFor(() => expect(worker.postMessage).toHaveBeenCalledTimes(1))
      await clearOfflineAssessmentCache()
      acknowledge()
      await preparing
      await prepareOfflineQuestion(state)
      expect(postedMessage).toMatchObject({ resources: [resource] })
    } finally { entries.mockRestore() }
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

  it('registers the assessment scope without using the HTTP cache for its script', async () => {
    workerResponse = { ok: true }

    await prepareOfflineQuestion(state)

    expect(register).toHaveBeenCalledTimes(1)
    expect(register).toHaveBeenCalledWith(
      '/assessment/process/capture-v2/offline-worker.js',
      { scope: '/student/sessions/', updateViaCache: 'none' },
    )
  })

  it('retries registration once after a transient failure', async () => {
    workerResponse = { ok: true }
    register
      .mockRejectedValueOnce(new TypeError('worker script fetch failed'))
      .mockResolvedValueOnce({ active: worker, update: vi.fn().mockResolvedValue(undefined) })

    await prepareOfflineQuestion(state)

    expect(getRegistration).not.toHaveBeenCalled()
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
