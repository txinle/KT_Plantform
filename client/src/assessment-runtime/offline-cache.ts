import type { StudentRuntimeQuestionEntry } from './api'
import { getBootstrapContext, type OfflineBootstrapEnvelope } from '@/app/bootstrap'
import { assessmentBuildVersion, assessmentShellUrl } from './build-version'

const OFFLINE_WORKER_URL = '/assessment/process/capture-v2/offline-worker.js'
const OFFLINE_WORKER_SCOPE = '/student/sessions/'

class OfflinePreparationError extends Error {
  constructor(message: string, readonly code?: string) {
    super(message)
    this.name = 'OfflinePreparationError'
  }
}

async function activeWorkerForScope() {
  const registration = await navigator.serviceWorker.getRegistration(OFFLINE_WORKER_SCOPE)
  return registration?.active
}

async function registerOfflineWorker() {
  const registration = await navigator.serviceWorker.register(OFFLINE_WORKER_URL, {
    scope: OFFLINE_WORKER_SCOPE, updateViaCache: 'none',
  })
  const candidate = registration.installing || registration.waiting
  if (candidate && candidate.state !== 'activated') await new Promise<void>((resolve, reject) => {
    const timeout = setTimeout(() => { candidate.removeEventListener('statechange', check); reject(new Error('offline worker activation timed out')) }, 15000)
    const check = () => {
      if (!['activated', 'redundant'].includes(candidate.state)) return
      clearTimeout(timeout)
      candidate.removeEventListener('statechange', check)
      if (candidate.state === 'activated') resolve()
      else reject(new Error('offline worker activation failed'))
    }
    candidate.addEventListener('statechange', check)
    check()
  })
  const readyRegistration = await navigator.serviceWorker.ready
  const worker = candidate?.state === 'activated' ? candidate : registration.active || readyRegistration.active
  if (!worker) throw new Error('offline worker did not become active')
  return worker
}

async function logWorkerInitializationFailure(firstError: unknown, retryError: unknown) {
  let scriptStatus: number | null = null
  let scriptContentType: string | null = null
  let serviceWorkerAllowed: string | null = null
  let diagnosticError: string | null = null
  try {
    const response = await fetch(OFFLINE_WORKER_URL, {
      cache: 'no-store',
      credentials: 'same-origin',
    })
    scriptStatus = response.status
    scriptContentType = response.headers.get('Content-Type')
    serviceWorkerAllowed = response.headers.get('Service-Worker-Allowed')
  } catch (error) {
    diagnosticError = String(error)
  }
  console.error('Capture V2 offline worker initialization failed.', {
    origin: location.origin,
    isSecureContext: globalThis.isSecureContext === true,
    scriptStatus,
    scriptContentType,
    serviceWorkerAllowed,
    diagnosticError,
    firstError: String(firstError),
    retryError: String(retryError),
  })
}

async function resolveOfflineWorker() {
  await activeWorkerForScope().catch(() => undefined)

  let firstError: unknown
  try {
    return await registerOfflineWorker()
  } catch (error) {
    firstError = error
  }

  await activeWorkerForScope().catch(() => undefined)

  try {
    return await registerOfflineWorker()
  } catch (retryError) {
    await logWorkerInitializationFailure(firstError, retryError)
    throw new OfflinePreparationError(
      '安全作答环境初始化失败，请刷新页面后重试。',
      'offline_worker_initialization_failed',
    )
  }
}

const workerResolutions = new WeakMap<ServiceWorkerContainer, Promise<ServiceWorker>>()
function currentOfflineWorker() {
  const container = navigator.serviceWorker
  let pending = workerResolutions.get(container)
  if (!pending) {
    pending = resolveOfflineWorker().catch(error => { workerResolutions.delete(container); throw error })
    workerResolutions.set(container, pending)
  }
  return pending
}

async function sendWorkerMessage(worker: ServiceWorker, message: Record<string, unknown>, timeoutMs = 15000) {
  await new Promise<void>((resolve, reject) => {
    const channel = new MessageChannel()
    const timeout = setTimeout(() => { channel.port1.close(); reject(new Error('离线恢复准备超时。')) }, timeoutMs)
    channel.port1.onmessage = (event) => {
      clearTimeout(timeout)
      channel.port1.close()
      const result = event.data as { ok?: boolean; error?: unknown; code?: unknown }
      if (result?.ok) resolve()
      else reject(new OfflinePreparationError(
        `离线恢复准备失败：${String(result?.error ?? '未知错误')}`,
        typeof result?.code === 'string' ? result.code : undefined,
      ))
    }
    worker.postMessage(message, [channel.port2])
  })
}

export async function clearOfflineAssessmentCache() {
  const worker = navigator.serviceWorker ? await activeWorkerForScope() : undefined
  // Runtime-1 workers ignore this message; their durable caches still need clearing.
  if (worker) await sendWorkerMessage(worker, { type: 'clear-capture-v2' }, 250).catch(() => undefined)
  if (globalThis.caches) {
    const names = await caches.keys()
    await Promise.all(names.filter(name => name.startsWith('kt-capture-v2-runtime-')).map(name => caches.delete(name)))
  }
}

export async function prepareOfflineQuestion(state: StudentRuntimeQuestionEntry) {
  if (state.offline_recovery) return
  if (!navigator.onLine) throw new OfflinePreparationError(
    '当前网络已断开，尚未完成本题的安全作答准备。',
    'offline_preparation_unavailable',
  )
  if (!navigator.serviceWorker) throw new Error('浏览器不支持离线恢复，请使用支持 Service Worker 的浏览器。')
  const currentQuestionUrl = state.navigation.find(item => item.current)?.url
  const pageUrl = new URL(currentQuestionUrl || location.href, location.origin)
  const worker = await currentOfflineWorker()
  const resources = [...new Set(performance.getEntriesByType('resource').map((entry) => entry.name))]
    .filter(resource => {
      const url = new URL(resource, location.origin)
      return url.origin === location.origin && url.pathname.startsWith('/static/')
    })
  await sendWorkerMessage(worker, {
    type: 'prepare-capture-v2', page: pageUrl.href,
    bootstrap: {
      ...getBootstrapContext(), version: 1, page_path: pageUrl.pathname,
      assessment_session_id: state.session_id, assignment_id: state.assignment.assignment_id,
    } satisfies OfflineBootstrapEnvelope,
    api: `/api/v1/student/sessions/${state.session_id}/questions/${state.assignment.assignment_id}/`,
    state: JSON.parse(JSON.stringify(state)),
    buildVersion: assessmentBuildVersion,
    shell: assessmentShellUrl,
    resources,
  })
}
