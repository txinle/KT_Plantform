import { describe, it, expect, vi, afterEach } from 'vitest'
import { webcrypto } from 'node:crypto'
import { gzipSync } from 'node:zlib'
import { Blob } from 'node:buffer'
import { createWriterLease } from './writer-lease'
import { createPreparedPaperCache, submissionPollDelay, decryptPreparedContent, hydratePreparedQuestion } from './prepared-paper'
import { MemoryOutbox } from './fact-store'

afterEach(() => vi.unstubAllGlobals())

async function encrypted(value: unknown) {
  const key = await webcrypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, true, ['encrypt', 'decrypt'])
  const nonce = webcrypto.getRandomValues(new Uint8Array(12))
  const ciphertext = await webcrypto.subtle.encrypt({ name: 'AES-GCM', iv: nonce }, key, gzipSync(JSON.stringify(value)))
  const text = JSON.stringify({ version: 1, nonce: Buffer.from(nonce).toString('base64'), ciphertext: Buffer.from(ciphertext).toString('base64'), compression: 'gzip' })
  const bytes = new TextEncoder().encode(text)
  const sha256 = Buffer.from(await webcrypto.subtle.digest('SHA-256', bytes)).toString('hex')
  return { text, descriptor: { url: `/assessment-content/${sha256}.json`, sha256, byte_length: bytes.byteLength, key: Buffer.from(await webcrypto.subtle.exportKey('raw', key)).toString('base64') } }
}

it('reuses an authorized initial writer lease without a second acquisition', async () => {
  const post = vi.fn()
  const writer = createWriterLease({ client: { post }, clientInstanceId: 'client', acquireUrl: '/acquire', renewUrl: '/renew', releaseUrl: '/release',
    initialLease: { writer_lease_token: 'authorized', writer_client_instance_id: 'client', expires_at: new Date(Date.now() + 60000).toISOString() } })
  expect(await writer.beforePointerWrite()).toEqual({ writer_lease_token: 'authorized', writer_client_instance_id: 'client' })
  expect(post).not.toHaveBeenCalled()
})

describe('prepared paper cache', () => {
  it('decrypts authenticated compressed content and rejects modified envelopes or a foreign key', async () => {
    vi.stubGlobal('crypto', webcrypto)
    vi.stubGlobal('Blob', Blob)
    const sealed = await encrypted({ question: { text: '中文题干', options: ['A', 'B'] } })
    vi.stubGlobal('fetch', vi.fn(async () => new Response(sealed.text)))
    await expect(decryptPreparedContent(sealed.descriptor)).resolves.toEqual({ question: { text: '中文题干', options: ['A', 'B'] } })
    await expect(decryptPreparedContent({ ...sealed.descriptor, sha256: '0'.repeat(64) })).rejects.toThrow('校验失败')
    const foreign = await encrypted({ text: 'other student' })
    await expect(decryptPreparedContent({ ...sealed.descriptor, key: foreign.descriptor.key })).rejects.toThrow()
  })

  it('makes the first question available while the manifest download is still pending', async () => {
    vi.stubGlobal('crypto', webcrypto)
    vi.stubGlobal('Blob', Blob)
    const question = await encrypted({ question: { text: 'First question', options: [] } })
    const manifest = await encrypted({ version: 1, session_id: 7, content_version: 'v1', navigation: [], question_count: 180 })
    let release!: (value: Response) => void
    const fetch = vi.fn((url: string) => url === question.descriptor.url ? Promise.resolve(new Response(question.text)) : new Promise<Response>(resolve => { release = resolve }))
    vi.stubGlobal('fetch', fetch)
    const hydrated = await hydratePreparedQuestion({ assessment_flow_version: 1, session_id: 7, prepared_paper: { version: 1, content_version: 'v1', question: question.descriptor, manifest: manifest.descriptor } } as never, new MemoryOutbox(), 1)
    expect(hydrated.dto.question.text).toBe('First question')
    await Promise.resolve()
    expect(fetch).toHaveBeenCalledTimes(2)
    release(new Response(manifest.text))
    await expect(hydrated.manifest).resolves.toMatchObject({ question_count: 180 })
  })

  it('shares only same user/session/content requests and uses a byte budget', async () => {
    const values = new Map<string, unknown>()
    const store = { readMetadata: vi.fn(async (key: string) => values.get(key)), writeMetadata: vi.fn(async (key: string, value: unknown) => { values.set(key, value) }) }
    const load = vi.fn(async (descriptor: { url: string }) => ({ text: descriptor.url }))
    const first = createPreparedPaperCache(store as never, { userId: 1, sessionId: 7, contentVersion: 'v1' }, load)
    const descriptor = (url: string, byte_length = 50) => ({ url, byte_length, sha256: url, key: 'key' })
    await Promise.all([first.content(descriptor('a')), first.content(descriptor('a'))])
    expect(load).toHaveBeenCalledTimes(1)
    await first.prefetch([descriptor('b', 60), descriptor('c', 60), descriptor('d', 20)], 100)
    expect(load.mock.calls.map(([row]) => row.url)).toEqual(['a', 'b', 'd'])
    await createPreparedPaperCache(store as never, { userId: 2, sessionId: 7, contentVersion: 'v1' }, load).content(descriptor('a'))
    expect(load).toHaveBeenCalledTimes(4)
  })
  it('bounds submission polling at 2, 4, 8, then 10 seconds', () => {
    expect([0, 1, 2, 3, 10].map(submissionPollDelay)).toEqual([2000, 4000, 8000, 10000, 10000])
  })
})
