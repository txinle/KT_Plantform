import { afterEach, describe, expect, it, vi } from 'vitest'
import { MemoryOutbox } from './fact-store'

const account = vi.hoisted(() => ({ id: 7 }))
const capture = vi.hoisted(() => ({ store: null as unknown }))
vi.mock('@/app/bootstrap', () => ({ getBootstrapContext: () => ({ user: { id: account.id }, csrf_token: 'csrf' }) }))
vi.mock('./fact-store', async original => ({ ...await original<object>(), defaultOutbox: async () => capture.store }))

function sharedLocks() {
  const held = new Set<string>()
  return { request: vi.fn(async (name: string, _options: unknown, callback: (lock: unknown) => Promise<void>) => {
    if (held.has(name)) return callback(null)
    held.add(name)
    try { return await callback({ name }) } finally { held.delete(name) }
  }) }
}

async function documentIdentity() {
  vi.resetModules()
  const hide: EventListener[] = []
  const original = window.addEventListener.bind(window)
  const spy = vi.spyOn(window, 'addEventListener').mockImplementation((type, listener, options) => {
    if (type === 'pagehide') hide.push(listener as EventListener)
    else original(type, listener, options)
  })
  const module = await import('./session-controller')
  return { get: module.assessmentClientInstanceId, controller: module.getSessionController, hide: async () => {
    for (const listener of hide) listener(new Event('pagehide'))
    await Promise.resolve(); await Promise.resolve()
  }, stop: () => spy.mockRestore() }
}

afterEach(() => { vi.restoreAllMocks(); sessionStorage.clear(); account.id = 7 })

describe('document ownership of a durable assessment client', () => {
  it('claims a copied question document before choosing its entry-intent namespace', async () => {
    Object.defineProperty(navigator, 'locks', { configurable: true, value: sharedLocks() })
    const store = new MemoryOutbox()
    capture.store = store
    sessionStorage.setItem('kt-assessment-tab-id', 'question-tab')
    const first = await documentIdentity()
    const controller = await first.controller(7, 'csrf')
    const original = await controller.freezeEntry(7, 14, 'resume')
    first.stop()
    sessionStorage.setItem('kt-assessment-tab-id', 'question-tab')
    const copied = await documentIdentity()
    const other = await copied.controller(7, 'csrf')
    const forked = await other.freezeEntry(7, 14, 'resume')
    expect(forked.navigationId).not.toBe(original.navigationId)
    expect(sessionStorage.getItem('kt-assessment-tab-id')).not.toBe('question-tab')
    expect([...store.metadata.keys()].filter(key => key.startsWith('entry-intent:7:'))).toHaveLength(2)
    controller.dispose(); other.dispose(); await first.hide(); await copied.hide(); copied.stop()
  })

  it('forks copied tab storage in shared IDB and restores the original client after its pagehide', async () => {
    Object.defineProperty(navigator, 'locks', { configurable: true, value: sharedLocks() })
    const store = new MemoryOutbox()
    sessionStorage.setItem('kt-assessment-tab-id', 'original-tab')
    const first = await documentIdentity()
    const original = await first.get(store)
    first.stop()
    sessionStorage.setItem('kt-assessment-tab-id', 'original-tab')
    const copied = await documentIdentity()
    const other = await copied.get(store)
    expect(other).not.toBe(original)
    expect(sessionStorage.getItem('kt-assessment-tab-id')).not.toBe('original-tab')
    copied.stop()
    await first.hide()
    sessionStorage.setItem('kt-assessment-tab-id', 'original-tab')
    const reloaded = await documentIdentity()
    expect(await reloaded.get(store)).toBe(original)
    await reloaded.hide(); reloaded.stop(); await copied.hide()
  })

  it('changes durable identity when the authenticated account changes in one document', async () => {
    Object.defineProperty(navigator, 'locks', { configurable: true, value: sharedLocks() })
    const store = new MemoryOutbox()
    sessionStorage.setItem('kt-assessment-tab-id', 'account-tab')
    const document = await documentIdentity()
    const first = await document.get(store)
    account.id = 8
    const second = await document.get(store)
    expect(second).not.toBe(first)
    expect(await store.readMetadata('assessment-client:7:account-tab')).toBe(first)
    expect(await store.readMetadata('assessment-client:8:account-tab')).toBe(second)
    account.id = 7
    expect(await document.get(store)).toBe(first)
    await document.hide(); document.stop()
  })

  it('uses fresh document identities rather than sharing a durable lease when Web Locks are unavailable', async () => {
    Object.defineProperty(navigator, 'locks', { configurable: true, value: undefined })
    const store = new MemoryOutbox()
    sessionStorage.setItem('kt-assessment-tab-id', 'copied-tab')
    const first = await documentIdentity()
    const one = await first.get(store)
    expect(await first.get(store)).toBe(one)
    first.stop()
    const second = await documentIdentity()
    expect(await second.get(store)).not.toBe(one)
    expect([...store.metadata.keys()]).toHaveLength(0)
    second.stop()
  })
})
