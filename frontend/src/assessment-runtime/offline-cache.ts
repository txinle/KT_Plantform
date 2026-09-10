import type { StudentRuntimeQuestionEntry } from './api'
import { getBootstrapContext, type OfflineBootstrapEnvelope } from '@/app/bootstrap'

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
    scope: OFFLINE_WORKER_SCOPE,
  })
  const readyRegistration = await navigator.serviceWorker.ready
  const worker = registration.active || readyRegistration.active
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
  const existing = await activeWorkerForScope().catch(() => undefined)
  if (existing) return existing

  let firstError: unknown
  try {
    return await registerOfflineWorker()
  } catch (error) {
    firstError = error
  }

  const racedRegistration = await activeWorkerForScope().catch(() => undefined)
  if (racedRegistration) return racedRegistration

  try {
    return await registerOfflineWorker()
  } catch (retryError) {
    const finalRegistration = await activeWorkerForScope().catch(() => undefined)
    if (finalRegistration) return finalRegistration
    await logWorkerInitializationFailure(firstError, retryError)
    throw new OfflinePreparationError(
      '安全作答环境初始化失败，请刷新页面后重试。',
      'offline_worker_initialization_failed',
    )
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
  const worker = await resolveOfflineWorker()
  await new Promise<void>((resolve, reject) => {
    const channel = new MessageChannel()
    const timeout = setTimeout(() => { channel.port1.close(); reject(new Error('离线恢复准备超时。')) }, 15000)
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
    worker.postMessage({
      type: 'prepare-capture-v2', page: pageUrl.href,
      bootstrap: {
        ...getBootstrapContext(), version: 1, page_path: pageUrl.pathname,
        assessment_session_id: state.session_id, assignment_id: state.assignment.assignment_id,
      } satisfies OfflineBootstrapEnvelope,
      api: `/api/v1/student/sessions/${state.session_id}/questions/${state.assignment.assignment_id}/`,
      state: JSON.parse(JSON.stringify(state)),
      resources: performance.getEntriesByType('resource').map((entry) => entry.name),
    }, [channel.port2])
  })
}
