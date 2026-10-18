import { currentAssessmentOperationId } from './operation-timing'
import type { CaptureStore } from './capture-contract'
import type { StudentRuntimeQuestionEntry } from './api'

export interface StaticContentDescriptor { url: string; sha256: string; byte_length: number; key: string }
export interface PreparedPaper { version: 1; content_version: string; manifest: StaticContentDescriptor; question: StaticContentDescriptor }
export interface PreparedNavigationItem {
  assignment_id: number; attempt_id: number; sequence_index: number; status: string; url: string; current: boolean
  response_version: number; active_response_event_id: string | null; content: StaticContentDescriptor
}
export interface PreparedManifest { version: 1; session_id: number; content_version: string; navigation: PreparedNavigationItem[]; question_count: number }
export interface ExpectedAttemptVersion { attempt_id: number; response_version: number; target_event_id: string | null }
export interface PaperSubmissionIntent {
  operation_id?: string
  submission_token: string; client_clicked_at: string; expected_versions: ExpectedAttemptVersion[]
  finish_url?: string; content_version?: string
  submission_id?: string; status_url?: string; status?: 'queued' | 'running' | 'completed' | 'failed'
}
export interface PaperSubmissionResponse {
  processing_state?: 'queued' | 'running' | 'retrying' | 'recovering' | 'completed' | 'failed'
  worker?: { status: 'connected' | 'not_connected' | 'unknown'; observed_at: string }
  success: boolean; submission_id: string; status: 'queued' | 'running' | 'completed' | 'failed'
  accepted_at: string; completed_at?: string | null; status_url: string; next_url: string
  error?: { code: 'submission_needs_verification'; message: string } | null
}
export const submissionPollDelay = (index: number) => Math.min(10000, 2000 * 2 ** index)

function base64(value: string) { return Uint8Array.from(atob(value), letter => letter.charCodeAt(0)) }
export async function decryptPreparedContent<T>(descriptor: StaticContentDescriptor): Promise<T> {
  const response = await fetch(descriptor.url, { credentials: 'same-origin', cache: 'force-cache' })
  if (!response.ok) throw new Error('题目内容下载失败，请重试。')
  const bytes = await response.arrayBuffer()
  const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), byte => byte.toString(16).padStart(2, '0')).join('')
  if (bytes.byteLength !== descriptor.byte_length || hash !== descriptor.sha256) throw new Error('题目内容校验失败，请重新加载。')
  const envelope = JSON.parse(new TextDecoder().decode(bytes)) as { version: number; nonce: string; ciphertext: string; compression: string }
  if (envelope.version !== 1 || envelope.compression !== 'gzip') throw new Error('题目内容版本不受支持。')
  const key = await crypto.subtle.importKey('raw', base64(descriptor.key), 'AES-GCM', false, ['decrypt'])
  const clear = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: base64(envelope.nonce), tagLength: 128 }, key, base64(envelope.ciphertext))
  const stream = new Blob([clear]).stream().pipeThrough(new DecompressionStream('gzip'))
  return JSON.parse(await new Response(stream).text()) as T
}

interface PreparedContentCache { content<T>(descriptor: StaticContentDescriptor): Promise<T>; prefetch(descriptors: StaticContentDescriptor[], byteBudget?: number): Promise<void> }
const liveCaches = new WeakMap<object, Map<string, PreparedContentCache>>()

export function createPreparedPaperCache(store: Pick<CaptureStore, 'readMetadata' | 'writeMetadata'>,
  scope: { userId: number; sessionId: number; contentVersion: string }, load: (descriptor: StaticContentDescriptor) => Promise<unknown> = decryptPreparedContent): PreparedContentCache {
  const prefix = `prepared-paper:${scope.userId}:${scope.sessionId}:${scope.contentVersion}`
  const caches = liveCaches.get(store) || new Map<string, PreparedContentCache>()
  liveCaches.set(store, caches)
  const existing = caches.get(prefix)
  if (existing) return existing
  const pending = new Map<string, Promise<unknown>>()
  async function content<T>(descriptor: StaticContentDescriptor): Promise<T> {
    const key = `${prefix}:${descriptor.sha256}`
    let flight = pending.get(key)
    if (!flight) {
      flight = (async () => {
        const cached = await store.readMetadata<T>(key)
        if (cached !== null && cached !== undefined) return cached
        const decoded = await load(descriptor)
        await store.writeMetadata(key, decoded)
        return decoded
      })()
      pending.set(key, flight)
      const clear = () => { pending.delete(key) }
      void flight.then(clear, clear)
    }
    return flight as Promise<T>
  }
  async function prefetch(descriptors: StaticContentDescriptor[], byteBudget = 2 * 1024 * 1024) {
    let bytes = 0
    const queue = descriptors.filter(descriptor => {
      if (bytes + descriptor.byte_length > byteBudget) return false
      bytes += descriptor.byte_length
      return true
    })
    const worker = async () => { while (queue.length) await content(queue.shift()!) }
    await Promise.all([worker(), worker()])
  }
  const cache = { content, prefetch }
  caches.set(prefix, cache)
  return cache
}

export async function hydratePreparedQuestion(dto: StudentRuntimeQuestionEntry, store: CaptureStore, userId: number) {
  if (dto.assessment_flow_version !== 1 || !dto.prepared_paper) return { dto }
  const prepared = dto.prepared_paper
  const cache = createPreparedPaperCache(store, { userId, sessionId: dto.session_id, contentVersion: prepared.content_version })
  markPreparedStartupPhase('first_content_start')
  const decoded = await cache.content<{ question: StudentRuntimeQuestionEntry['question']; research_tools?: StudentRuntimeQuestionEntry['research_tools'] }>(prepared.question)
  markPreparedStartupPhase('first_content_end')
  dto.question = decoded.question
  if (decoded.research_tools) dto.research_tools = { ...decoded.research_tools, ...dto.research_tools }
  const manifest = cache.content<PreparedManifest>(prepared.manifest).then(value => {
    if (value.session_id !== dto.session_id || value.content_version !== prepared.content_version || value.version !== 1) throw new Error('试卷清单与当前会话不一致。')
    return value
  })
  return { dto, manifest, cache }
}

export type PreparedStartupPhase = 'click' | 'request' | 'grant' | 'activation_start' | 'activation_response' | 'first_content_start' | 'first_content_end' | 'runtime_init_start' | 'runtime_init_end' | 'operable'
declare global { interface Window { __ktPreparedStartupWitness?: ReadonlyArray<Readonly<{ operation_id: string | null; phase: PreparedStartupPhase; client_monotonic_ms: number; client_time_origin: number }>> } }
export function markPreparedStartupPhase(phase: PreparedStartupPhase) {
  const target = window as Window & { __ktPreparedStartupWitness?: ReadonlyArray<Readonly<{ operation_id: string | null; phase: PreparedStartupPhase; client_monotonic_ms: number; client_time_origin: number }>> }
  const sample = Object.freeze({ operation_id: currentAssessmentOperationId() || null, phase, client_monotonic_ms: performance.now(), client_time_origin: performance.timeOrigin })
  target.__ktPreparedStartupWitness = Object.freeze([...(target.__ktPreparedStartupWitness || []).slice(-99), sample])
}
