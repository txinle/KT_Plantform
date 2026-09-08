import type { StudentRuntimeQuestionEntry } from './api'
import { getBootstrapContext, type OfflineBootstrapEnvelope } from '@/app/bootstrap'

class OfflinePreparationError extends Error {
  constructor(message: string, readonly code?: string) {
    super(message)
    this.name = 'OfflinePreparationError'
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
  const registration = await navigator.serviceWorker.register('/assessment/process/capture-v2/offline-worker.js', {
    scope: '/student/sessions/',
  })
  await navigator.serviceWorker.ready
  const worker = registration.active
  if (!worker) throw new Error('离线恢复组件尚未就绪。')
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
