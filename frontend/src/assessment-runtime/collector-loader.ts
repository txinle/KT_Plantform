import collectorUrl from '../../../static/js/process-event-collector.js?url'

let pending: Promise<void> | undefined
const available = () => Boolean((globalThis as { KTProcessData?: unknown }).KTProcessData)

export function ensureProcessCollector(): Promise<void> {
  if (available()) return Promise.resolve()
  if (pending) return pending
  pending = new Promise<void>((resolve, reject) => {
    const script = document.createElement('script')
    script.src = new URL(collectorUrl, import.meta.url).href
    script.async = true
    script.dataset.ktCollector = 'true'
    const fail = () => { script.remove(); reject(new Error('采集器加载失败，请检查网络后重试。')) }
    script.onload = () => available() ? resolve() : fail()
    script.onerror = fail
    document.head.append(script)
  }).catch(error => { pending = undefined; throw error })
  return pending
}
