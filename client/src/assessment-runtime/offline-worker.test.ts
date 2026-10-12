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
  return { session_id: session, assignment: { assignment_id: assignment, attempt_id: 13 }, process: { visit_id: 41, entry_event_id: 'entry-event' },
    offline_authorization: { user_id: user, session_id: session, assignment_id: assignment },
    entry_receipt: { session_id: session, assignment_id: assignment, attempt_id: 13, visit_id: 41, entry_event_id: 'entry-event' } }
}

function harness() {
  const handlers: Record<string, (event: WorkerEvent) => void> = {}
  const stores = new Map<string, Map<string, Response>>()
  let beforeRead: ((url: string) => Promise<void>) | undefined
  const key = (request: string | Request) => typeof request === 'string' ? request : request.url
  const caches = {
    keys: async () => [...stores.keys()],
    delete: async (name: string) => stores.delete(name),
    open: async (name: string) => {
      if (!stores.has(name)) stores.set(name, new Map())
      const store = stores.get(name)!
      return {
        match: async (request: string | Request) => { const response = store.get(key(request))?.clone(); await beforeRead?.(key(request)); return response },
        put: async (request: string | Request, response: Response) => { store.set(key(request), response.clone()) },
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
  return { prepare, message, request, fetch, stores, caches, beforeCacheRead: (hook: typeof beforeRead) => { beforeRead = hook }, switchUser: (id: number) => { userId = id }, activate: async () => {
    let pending: Promise<unknown> | undefined
    handlers.activate!({ waitUntil: promise => { pending = promise } })
    await pending
  } }
}

describe('capture-v2 offline worker resource preparation', () => {
  it('deduplicates resources and reuses immutable hashes while revalidating unversioned URLs', async () => {
    const worker = harness()
    const mutable = `${origin}/static/js/assessment.js`
    expect(await worker.prepare([immutable, immutable, mutable])).toMatchObject({ ok: true })
    expect(worker.fetch.mock.calls.filter(([url]) => url === immutable)).toHaveLength(1)
    worker.fetch.mockClear()
    expect(await worker.prepare([immutable, mutable])).toMatchObject({ ok: true })
    expect(worker.fetch.mock.calls.map(([url]) => url)).not.toContain(page)
    expect(worker.fetch.mock.calls.filter(([url]) => url === immutable)).toHaveLength(0)
    expect(worker.fetch.mock.calls.filter(([url]) => url === mutable)).toHaveLength(1)
    expect(worker.fetch).toHaveBeenCalledWith(mutable, expect.objectContaining({ cache: 'no-cache' }))
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
