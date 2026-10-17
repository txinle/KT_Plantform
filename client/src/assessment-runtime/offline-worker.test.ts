// @vitest-environment node
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { describe, expect, it, vi } from 'vitest'

const origin = 'http://localhost:3000'
const page = `${origin}/student/sessions/7/questions/11/`
const api = `${origin}/api/v1/student/sessions/7/questions/11/`
const shell = `${origin}/static/spa-assets/capture-shell-shell-C0abc123.js.html`
const immutable = `${origin}/static/spa-assets/assets/shell-C0abc123.js`
type Data = Record<string, unknown>
type WorkerEvent = { data?: Data; source?: { url: string; id: string }; ports?: { postMessage: (value: Data) => void }[];
  request?: Request; clientId?: string; waitUntil: (promise: Promise<unknown>) => void; respondWith?: (promise: Promise<Response>) => void }


function enteredState(session: number, assignment: number, user: number) {
  return { assessment_flow_version: 1, session_id: session, assignment: { assignment_id: assignment, attempt_id: 13 }, process: { paper_flow_version: 1, visit_id: 41, entry_event_id: 'entry-event' },
    offline_authorization: { user_id: user, session_id: session, assignment_id: assignment },
    entry_receipt: { session_id: session, assignment_id: assignment, attempt_id: 13, visit_id: 41, entry_event_id: 'entry-event' } }
}

function preparedPaperState() {
  const state = enteredState(7, 11, 3)
  return { ...state, assessment_flow_version: 1,
    process: { ...state.process, session_id: 7, attempt_id: 13, paper_flow_version: 1, navigation_id: 'navigation-one', visit_index: 1 },
    entry_receipt: { ...state.entry_receipt, navigation_id: 'navigation-one', visit_index: 1, entered_at: '2026-10-15T00:00:00Z' } }
}

function harness(existingStores?: Map<string, Map<string, Response>>) {
  const handlers: Record<string, (event: WorkerEvent) => void> = {}
  const stores = existingStores ?? new Map<string, Map<string, Response>>()
  let beforeRead: ((url: string) => Promise<void>) | undefined
  let beforePut: ((url: string) => Promise<void>) | undefined
  const key = (request: string | Request) => typeof request === 'string' ? request : request.url
  const caches = {
    keys: async () => [...stores.keys()],
    delete: async (name: string) => stores.delete(name),
    open: async (name: string) => {
      if (!stores.has(name)) stores.set(name, new Map())
      const store = stores.get(name)!
      return {
        match: async (request: string | Request) => { const response = store.get(key(request))?.clone(); await beforeRead?.(key(request)); return response },
        put: async (request: string | Request, response: Response) => { await beforePut?.(key(request)); store.set(key(request), response.clone()) },
        delete: async (request: string | Request) => store.delete(key(request)),
      }
    },
  }
  let userId = 3
  const fetch = vi.fn(async (request: string | Request) => {
    const url = key(request)
    if (url.endsWith('/api/v1/auth/session/')) return Response.json({ data: { user: { id: userId } }, meta: { request_id: 'auth-test' } })
    if (url.includes('/capture-shell-')) return new Response(`<html><head><meta name="kt-shell-build" content="${url.includes('D0def456') ? 'shell-D0def456.js' : 'shell-C0abc123.js'}"></head><body><div id="kt-app"></div></body></html>`)
    if (new URL(url).pathname.startsWith('/student/sessions/')) return new Response('<html><head></head><body></body></html>', { headers: { 'X-Capture-Contract': 'capture-v2' } })
    return new Response('asset', { headers: { 'Content-Type': 'application/javascript' } })
  })
  runInNewContext(readFileSync(new URL('../../legacy/js/capture-v2-offline-worker.js', import.meta.url), 'utf8'), {
    self: { location: { origin }, addEventListener: (type: string, handler: (event: WorkerEvent) => void) => { handlers[type] = handler },
      skipWaiting: vi.fn(), clients: { claim: vi.fn() } },
    caches, fetch, URL, Headers, Request, Response,
  })
  async function message(data: Data, sourceUrl = page) {
    let pending: Promise<unknown> | undefined
    let result: Data | undefined
    handlers.message!({ data, source: { url: sourceUrl, id: 'client-1' },
      ports: [{ postMessage: value => { result = value } }], waitUntil: promise => { pending = promise } })
    await pending
    return result
  }
  const prepare = (resources: string[], extra: Data = {}, sourceUrl = page) => message({ type: 'prepare-capture-v2', page, api,
    state: enteredState(7, 11, userId),
    bootstrap: { version: 1, page_path: new URL(page).pathname, assessment_session_id: 7, assignment_id: 11,
      csrf_token: 'csrf', user: { id: userId } }, buildVersion: 'shell-C0abc123.js', shell, resources, ...extra }, sourceUrl)
  async function request(url: string) {
    let response: Promise<Response> | undefined
    let pending: Promise<unknown> | undefined
    handlers.fetch!({ request: new Request(url), clientId: 'client-1',
      waitUntil: promise => { pending = promise }, respondWith: promise => { response = promise } })
    await pending
    return response ? await response : undefined
  }
  return { prepare, message, request, fetch, stores, caches, beforeCacheRead: (hook: typeof beforeRead) => { beforeRead = hook },
    beforeCachePut: (hook: typeof beforePut) => { beforePut = hook }, switchUser: (id: number) => { userId = id }, activate: async () => {
    let pending: Promise<unknown> | undefined
    handlers.activate!({ waitUntil: promise => { pending = promise } })
    await pending
  } }
}

describe('capture-v2 offline worker resource preparation', () => {
  it.each(['/student/courses/1/', '/login/', '/student/sessions/7/questions/11/'])('rejects retired per-item recovery from %s', async source => {
    const worker = harness()
    const paper = preparedPaperState()
    const state = { ...paper, assessment_flow_version: 0, entry_allowed: true,
      process: { ...paper.process, paper_flow_version: 0, capture_version: 'capture-v2' } }
    expect(await worker.prepare([immutable], { state }, origin + source)).toMatchObject({ ok: false })
    for (const invalid of [
      { ...state, entry_allowed: false },
      { ...state, process: { ...state.process, capture_version: 'v1' } },
      { ...state, entry_receipt: { ...state.entry_receipt, navigation_id: 'foreign' } },
      { ...state, process: { ...state.process, session_id: 99 } },
    ]) expect(await worker.prepare([immutable], { state: invalid }, origin + source)).toMatchObject({ ok: false })
    expect(await worker.prepare([immutable], { state }, `${origin}/teacher/`)).toMatchObject({ ok: false })
  })
  it('warms public resources from the task list without auth or question content and reuses them after activation', async () => {
    const worker = harness()
    expect(await worker.message({ type: 'prepare-capture-v2-common', buildVersion: 'shell-C0abc123.js', shell, resources: [immutable] }, `${origin}/student/courses/1/`)).toMatchObject({ ok: true })
    expect(worker.fetch.mock.calls.map(([url]) => url)).toEqual([shell, immutable])
    expect([...worker.stores.values()].flatMap(store => [...store.keys()]).some(url => url === api || url.includes('offline-identity'))).toBe(false)
    await worker.prepare([immutable])
    expect(worker.fetch.mock.calls.filter(([url]) => url === shell || url === immutable)).toHaveLength(2)
    expect(worker.fetch.mock.calls.filter(([url]) => url === `${origin}/api/v1/auth/session/`)).toHaveLength(1)
  })
  it.each(['/student/courses/1/', '/login/'])('prepares a fully authorized flow-one receipt from a same-origin router document %s', async source => {
    const worker = harness()
    expect(await worker.prepare([immutable], { state: preparedPaperState() }, origin + source)).toMatchObject({ ok: true, commonResourcesPrepared: true })
    expect(worker.fetch.mock.calls.filter(([url]) => url === `${origin}/api/v1/auth/session/`)).toHaveLength(1)
    expect(worker.fetch.mock.calls.filter(([url]) => url === page || url === api)).toHaveLength(0)
    worker.fetch.mockRejectedValue(new TypeError('offline'))
    expect(await (await worker.request(api))?.json()).toMatchObject({ data: { assessment_flow_version: 1, offline_recovery: true } })
  })

  it('rejects foreign origins, foreign question sessions, missing receipts and mismatched flow-one receipts without replacing prior recovery', async () => {
    const worker = harness()
    await worker.prepare([immutable])
    const keys = [...worker.stores.keys()]
    const valid = preparedPaperState()
    for (const [state, source] of [
      [valid, 'https://other.test/login/'], [valid, `${origin}/student/sessions/99/questions/11/`],
      [{ ...valid, entry_receipt: undefined }, `${origin}/login/`],
      [{ ...valid, entry_receipt: { ...valid.entry_receipt, entry_event_id: 'forged' } }, `${origin}/login/`],
      [{ ...valid, process: { ...valid.process, paper_flow_version: 0 } }, `${origin}/login/`],
    ] as Array<[Data, string]>) {
      expect(await worker.prepare([immutable], { state }, source)).toMatchObject({ ok: false })
      expect([...worker.stores.keys()]).toEqual(keys)
    }
    worker.fetch.mockRejectedValue(new TypeError('offline'))
    expect(await (await worker.request(api))?.json()).toMatchObject({ data: { session_id: 7, offline_recovery: true } })
  })

  it('continues to reject an authenticated account mismatch for a flow-one router document', async () => {
    const worker = harness()
    worker.switchUser(4)
    expect(await worker.prepare([immutable], { state: preparedPaperState(), bootstrap: { version: 1, page_path: new URL(page).pathname, assessment_session_id: 7, assignment_id: 11, user: { id: 3 } } }, `${origin}/login/`)).toMatchObject({ ok: false })
    expect([...worker.stores.values()].flatMap(store => [...store.keys()]).filter(url => url.includes('offline-envelope'))).toHaveLength(0)
  })

  it('does not let a router preparation restore cache after logout during its authentication check', async () => {
    const worker = harness()
    let authenticate!: (value: Response) => void
    const pending = new Promise<Response>(resolve => { authenticate = resolve })
    const original = worker.fetch.getMockImplementation()!
    worker.fetch.mockImplementation(async url => String(url).endsWith('/api/v1/auth/session/') ? pending : original(url))
    const preparing = worker.prepare([immutable], { state: preparedPaperState() }, `${origin}/login/`)
    await Promise.resolve()
    await worker.message({ type: 'clear-capture-v2' })
    authenticate(Response.json({ data: { user: { id: 3 } } }))
    expect(await preparing).toMatchObject({ ok: false })
    worker.fetch.mockRejectedValue(new TypeError('offline'))
    await expect(worker.request(api)).rejects.toThrow('offline')
  })

  it('deduplicates resources and reuses completed common preparation in the same account session and build', async () => {
    const worker = harness()
    const mutable = `${origin}/static/js/assessment.js`
    expect(await worker.prepare([immutable, immutable, mutable])).toMatchObject({ ok: true, commonResourcesPrepared: true })
    expect(worker.fetch.mock.calls.filter(([url]) => url === immutable)).toHaveLength(1)
    worker.fetch.mockClear()
    const cacheReads: string[] = []
    worker.beforeCacheRead(async url => { cacheReads.push(url) })
    expect(await worker.prepare([immutable, mutable])).toMatchObject({ ok: true })
    expect(worker.fetch.mock.calls.map(([url]) => url)).not.toContain(page)
    expect(worker.fetch.mock.calls.filter(([url]) => url === immutable)).toHaveLength(0)
    expect(worker.fetch.mock.calls.filter(([url]) => url === mutable)).toHaveLength(0)
    expect(cacheReads).not.toContain(shell)
    expect(cacheReads).not.toContain(immutable)
    expect(worker.fetch.mock.calls.filter(([url]) => String(url).endsWith('/api/v1/auth/session/'))).toHaveLength(1)
  })

  it('still durably writes each question bootstrap and API state when common resources are reused', async () => {
    const worker = harness()
    expect(await worker.prepare([immutable])).toMatchObject({ ok: true })
    const otherPage = `${origin}/student/sessions/7/questions/12/`
    const otherApi = `${origin}/api/v1/student/sessions/7/questions/12/`
    const updated = { ...enteredState(7, 12, 3), saved_answer: 'updated' }
    const bootstrap = { version: 1, page_path: new URL(otherPage).pathname, assessment_session_id: 7,
      assignment_id: 12, csrf_token: 'new-csrf', user: { id: 3 } }
    expect(await worker.prepare([], { page: otherPage, api: otherApi, state: updated, bootstrap })).toMatchObject({ ok: true })
    expect(await worker.prepare([], { page: otherPage, api: otherApi,
      state: { ...updated, saved_answer: 'latest' }, bootstrap })).toMatchObject({ ok: true })
    worker.fetch.mockRejectedValue(new TypeError('offline'))
    expect(await (await worker.request(otherApi))?.json()).toMatchObject({ data: { saved_answer: 'latest' } })
    expect(await (await worker.request(otherPage))?.text()).toContain('new-csrf')
    expect(await (await worker.request(api))?.json()).toMatchObject({ data: { assignment: { assignment_id: 11 } } })
  })

  it('retries unsuccessful common preparation and includes newly loaded static resources', async () => {
    const worker = harness()
    const mutable = `${origin}/static/js/assessment.js`
    const later = `${origin}/static/js/question-media.js`
    const original = worker.fetch.getMockImplementation()!
    worker.fetch.mockImplementationOnce(original)
    worker.fetch.mockImplementation(async url => url === mutable ? new Response('', { status: 503 }) : original(url))
    expect(await worker.prepare([mutable])).toMatchObject({ ok: false })
    worker.fetch.mockImplementation(original)
    expect(await worker.prepare([mutable])).toMatchObject({ ok: true })
    expect(await worker.prepare([mutable, later])).toMatchObject({ ok: true })
    expect(worker.fetch.mock.calls.filter(([url]) => url === mutable)).toHaveLength(2)
    expect(worker.fetch.mock.calls.filter(([url]) => url === later)).toHaveLength(1)
  })

  it('rechecks prior mandatory resources after the worker restarts when the client sends only new URLs', async () => {
    const firstWorker = harness()
    expect(await firstWorker.prepare([immutable])).toMatchObject({ ok: true })
    for (const store of firstWorker.stores.values()) store.delete(immutable)
    const restarted = harness(firstWorker.stores)
    const original = restarted.fetch.getMockImplementation()!
    restarted.fetch.mockImplementation(async url => url === immutable ? new Response('', { status: 503 }) : original(url))
    expect(await restarted.prepare([])).toMatchObject({ ok: false })
    restarted.fetch.mockImplementation(original)
    expect(await restarted.prepare([])).toMatchObject({ ok: true })
    expect(restarted.fetch.mock.calls.filter(([url]) => url === immutable)).toHaveLength(2)
  })

  it('does not mark question ready when its API state cannot be durably written and retries the write', async () => {
    const worker = harness()
    expect(await worker.prepare([immutable])).toMatchObject({ ok: true })
    const writes: string[] = []
    worker.beforeCachePut(async url => { writes.push(url); if (url === api) throw new Error('QuotaExceededError') })
    expect(await worker.prepare([])).toMatchObject({ ok: false, error: expect.stringContaining('QuotaExceededError') })
    worker.beforeCachePut(async url => { writes.push(url) })
    expect(await worker.prepare([])).toMatchObject({ ok: true })
    expect(writes.filter(url => url === api)).toHaveLength(2)
  })

  it('revalidates unversioned common resources for different sessions, accounts and after explicit clear', async () => {
    const worker = harness()
    const mutable = `${origin}/static/js/assessment.js`
    expect(await worker.prepare([mutable])).toMatchObject({ ok: true })
    const otherPage = `${origin}/student/sessions/8/questions/12/`
    expect(await worker.prepare([mutable], { page: otherPage, api: `${origin}/api/v1/student/sessions/8/questions/12/`,
      state: enteredState(8, 12, 3), bootstrap: { version: 1, page_path: new URL(otherPage).pathname,
        assessment_session_id: 8, assignment_id: 12, user: { id: 3 } },
    }, otherPage)).toMatchObject({ ok: true })
    worker.switchUser(4)
    expect(await worker.prepare([mutable])).toMatchObject({ ok: true })
    await worker.message({ type: 'clear-capture-v2' })
    expect(await worker.prepare([mutable])).toMatchObject({ ok: true })
    expect(worker.fetch.mock.calls.filter(([url]) => url === mutable)).toHaveLength(4)
  })

  it('fetches missing resources concurrently with at most four requests in flight', async () => {
    const worker = harness()
    let inFlight = 0
    let maximum = 0
    const original = worker.fetch.getMockImplementation()!
    worker.fetch.mockImplementation(async (url) => {
      if (!String(url).includes('/static/') || String(url).includes('/capture-shell-')) return original(url)
      maximum = Math.max(maximum, ++inFlight)
      await new Promise(resolve => setTimeout(resolve, 2))
      inFlight--
      return new Response('asset')
    })
    const resources = Array.from({ length: 10 }, (_, i) => `${origin}/static/spa-assets/assets/chunk-${i}abcdef.js`)
    expect(await worker.prepare(resources)).toMatchObject({ ok: true })
    expect(maximum).toBe(4)
  })

  it('fails readiness on a mandatory resource HTTP error and does not cache a partial question', async () => {
    const worker = harness()
    const original = worker.fetch.getMockImplementation()!
    worker.fetch.mockImplementation(async url => url === immutable ? new Response('', { status: 404 }) : original(url))
    expect(await worker.prepare([immutable])).toMatchObject({ ok: false })
    worker.fetch.mockRejectedValue(new TypeError('offline'))
    await expect(worker.request(page)).rejects.toThrow('offline')
  })

  it('prepares authorized DTOs without fetching per-question HTML', async () => {
    const worker = harness()
    await worker.prepare([immutable])
    const original = worker.fetch.getMockImplementation()!
    worker.fetch.mockImplementation(async url => url === page ? new Response('', { status: 403 }) : original(url))
    expect(await worker.prepare([immutable])).toMatchObject({ ok: true })
    expect(worker.fetch.mock.calls.filter(([url]) => url === page)).toHaveLength(0)
  })

  it('keeps current question recovery and its bootstrap and DTO available offline', async () => {
    const worker = harness()
    await worker.prepare([immutable])
    worker.fetch.mockRejectedValue(new TypeError('offline'))
    expect(await (await worker.request(page))?.text()).toContain('kt-offline-bootstrap')
    expect(await (await worker.request(api))?.json()).toMatchObject({ data: { session_id: 7, offline_recovery: true } })
  })

  it('invalidates question recovery after logout', async () => {
    const worker = harness()
    await worker.prepare([immutable])
    await worker.message({ type: 'clear-capture-v2' })
    worker.fetch.mockRejectedValue(new TypeError('offline'))
    await expect(worker.request(page)).rejects.toThrow('offline')
    await expect(worker.request(api)).rejects.toThrow('offline')
  })

  it('does not restore recovery when logout happens while authentication verification is pending', async () => {
    const worker = harness()
    let authenticate!: (value: Response) => void
    const authPending = new Promise<Response>(resolve => { authenticate = resolve })
    const original = worker.fetch.getMockImplementation()!
    worker.fetch.mockImplementation(async url => String(url).endsWith('/api/v1/auth/session/') ? authPending : original(url))
    const preparing = worker.prepare([immutable])
    await Promise.resolve()
    await worker.message({ type: 'clear-capture-v2' })
    authenticate(Response.json({ data: { user: { id: 3 } }, meta: { request_id: 'auth-test' } }))
    expect(await preparing).toMatchObject({ ok: false })
    worker.fetch.mockRejectedValue(new TypeError('offline'))
    await expect(worker.request(page)).rejects.toThrow('offline')
  })

  it('rejects a different authenticated account and removes earlier account recovery', async () => {
    const worker = harness()
    await worker.prepare([immutable])
    worker.switchUser(4)
    expect(await worker.prepare([immutable], { bootstrap: { version: 1, page_path: new URL(page).pathname,
      assessment_session_id: 7, assignment_id: 11, user: { id: 3 } } })).toMatchObject({ ok: false })
    worker.fetch.mockRejectedValue(new TypeError('offline'))
    await expect(worker.request(page)).rejects.toThrow('offline')
  })

  it('does not reuse another task cache after preparing a different assessment session', async () => {
    const worker = harness()
    await worker.prepare([immutable])
    const otherPage = `${origin}/student/sessions/8/questions/12/`
    expect(await worker.prepare([immutable], { page: otherPage, api: `${origin}/api/v1/student/sessions/8/questions/12/`,
      state: enteredState(8, 12, 3), bootstrap: { version: 1,
        page_path: new URL(otherPage).pathname, assessment_session_id: 8, assignment_id: 12, user: { id: 3 } },
    }, otherPage)).toMatchObject({ ok: true })
    worker.fetch.mockRejectedValue(new TypeError('offline'))
    await expect(worker.request(page)).rejects.toThrow('offline')
    expect(await (await worker.request(otherPage))?.text()).toContain('kt-offline-bootstrap')
  })

  it('cleans obsolete runtime namespaces on worker activation', async () => {
    const worker = harness()
    await worker.caches.open('kt-capture-v2-runtime-1')
    await worker.caches.open('unrelated-app-cache')
    await worker.activate()
    expect(await worker.caches.keys()).not.toContain('kt-capture-v2-runtime-1')
    expect(await worker.caches.keys()).toContain('unrelated-app-cache')
  })

  it('does not reuse resource entries from another build namespace', async () => {
    const worker = harness()
    await worker.prepare([immutable])
    worker.fetch.mockClear()
    expect(await worker.prepare([immutable], { buildVersion: 'shell-D0def456.js', shell: `${origin}/static/spa-assets/capture-shell-shell-D0def456.js.html` })).toMatchObject({ ok: true })
    expect(worker.fetch.mock.calls.filter(([url]) => url === immutable)).toHaveLength(1)
  })
})


it('does not serve cached question material if logout invalidates identity during cache reads', async () => {
  const worker = harness()
  expect(await worker.prepare([immutable])).toMatchObject({ ok: true })
  worker.fetch.mockRejectedValue(new TypeError('offline'))
  let release!: () => void
  let started!: () => void
  const reading = new Promise<void>(resolve => { started = resolve })
  const paused = new Promise<void>(resolve => { release = resolve })
  worker.beforeCacheRead(async url => {
    if (url.includes('capture-shell-')) { started(); await paused }
  })
  const request = worker.request(page)
  await reading
  await worker.message({ type: 'clear-capture-v2' })
  release()
  await expect(request).rejects.toThrow('offline')
})
