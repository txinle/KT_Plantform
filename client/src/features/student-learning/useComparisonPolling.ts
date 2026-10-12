import { onBeforeUnmount, onMounted, watch } from 'vue'
import type { StudentProjectionStatus } from '../student-courses/api'

export function useComparisonPolling(
  current: () => StudentProjectionStatus | undefined,
  hasComparison: () => boolean,
  check: () => Promise<StudentProjectionStatus>,
  load: () => Promise<void>,
) {
  let timer: ReturnType<typeof setTimeout> | undefined
  let stopped = false
  let disposed = false
  let initialized = false
  let flight: Promise<void> | undefined
  let manualRequested = false
  let checkpoint = 0
  let startedAt = 0
  const checkpoints = [5_000, 15_000, 30_000]

  function stop() {
    stopped = true
    if (timer !== undefined) clearTimeout(timer)
    timer = undefined
  }
  function refresh(automatic = false): Promise<void> {
    if (!automatic) manualRequested = true
    if (flight) return flight
    const request = (async () => {
      const metadata = await check()
      if (disposed || document.visibilityState !== 'visible' || (automatic && stopped && !manualRequested)) return
      if (metadata.comparison_status === 'failed') {
        stop()
        const data = current()
        if (data) data.comparison_status = 'failed'
      }
      if (!manualRequested) {
        if (metadata.comparison_status === 'ready') {
          stop()
          await load()
        }
      } else if (!metadata.version || metadata.version !== current()?.version) {
        await load()
      }
    })()
    flight = request
    const clear = () => { if (flight === request) { flight = undefined; manualRequested = false } }
    request.then(clear, clear)
    return request
  }
  function schedule() {
    if (['ready', 'failed'].includes(current()?.comparison_status || '') || hasComparison()) { stop(); return }
    if (stopped || checkpoint >= checkpoints.length) return
    timer = setTimeout(async () => {
      timer = undefined
      if (document.visibilityState !== 'visible') { stop(); return }
      checkpoint += 1
      try { await refresh(true) } catch { stop() }
      schedule()
    }, Math.max(0, startedAt + checkpoints[checkpoint]! - Date.now()))
  }
  function initialize() {
    if (initialized || !current()) return
    initialized = true
    if (document.visibilityState !== 'visible' || hasComparison() || current()?.comparison_status !== 'pending') {
      stop()
      return
    }
    startedAt = Date.now()
    schedule()
  }
  function visibilityChanged() { if (document.visibilityState !== 'visible') stop() }
  watch(current, () => {
    if (initialized && (['ready', 'failed'].includes(current()?.comparison_status || '') || hasComparison())) stop()
    else initialize()
  })
  onMounted(() => {
    document.addEventListener('visibilitychange', visibilityChanged)
    initialize()
  })
  onBeforeUnmount(() => {
    disposed = true
    stop()
    document.removeEventListener('visibilitychange', visibilityChanged)
  })
  return { refresh: () => refresh(false) }
}
