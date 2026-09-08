import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/app/bootstrap', () => ({
  getBootstrapContext: () => ({ csrf_token: 'csrf-token' }),
}))

import { prepareOfflineQuestion } from './offline-cache'

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
    const worker = {
      postMessage: vi.fn((message: Record<string, unknown>, ports: Array<{ replyTo: TestMessageChannel['port1'] }>) => {
        postedMessage = message
        queueMicrotask(() => ports[0]!.replyTo.onmessage?.({ data: workerResponse }))
      }),
    }
    Object.defineProperty(globalThis.navigator, 'serviceWorker', {
      configurable: true,
      value: {
        register: vi.fn().mockResolvedValue({ active: worker }),
        ready: Promise.resolve(),
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
