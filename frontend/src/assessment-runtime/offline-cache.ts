import type { StudentRuntimeQuestionEntry } from './api'
import { getBootstrapContext, type OfflineBootstrapEnvelope } from '@/app/bootstrap'

export async function prepareOfflineQuestion(state: StudentRuntimeQuestionEntry) {
  if (state.offline_recovery || !navigator.onLine) return
  if (!navigator.serviceWorker) throw new Error('浏览器不支持离线恢复，请使用支持 Service Worker 的浏览器。')
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
      if (event.data.ok) resolve()
      else reject(new Error(`离线恢复准备失败：${event.data.error}`))
    }
    worker.postMessage({
      type: 'prepare-capture-v2', page: location.href,
      bootstrap: {
        ...getBootstrapContext(), version: 1, page_path: location.pathname,
        assessment_session_id: state.session_id, assignment_id: state.assignment.assignment_id,
      } satisfies OfflineBootstrapEnvelope,
      api: `/api/v1/student/sessions/${state.session_id}/questions/${state.assignment.assignment_id}/`,
      state: JSON.parse(JSON.stringify(state)),
      resources: performance.getEntriesByType('resource').map((entry) => entry.name),
    }, [channel.port2])
  })
}
