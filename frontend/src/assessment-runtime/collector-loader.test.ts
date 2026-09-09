import { afterEach, expect, it, vi } from 'vitest'
import collectorUrl from '../../../static/js/process-event-collector.js?url'

afterEach(() => { document.head.querySelectorAll('[data-kt-collector]').forEach(node => node.remove()); vi.unstubAllGlobals(); vi.resetModules() })

it('loads the unchanged collector once for concurrent consumers without a Django script tag', async () => {
  vi.stubGlobal('KTProcessData', undefined)
  const { ensureProcessCollector } = await import('./collector-loader')
  const first = ensureProcessCollector()
  const second = ensureProcessCollector()
  const scripts = document.head.querySelectorAll<HTMLScriptElement>('[data-kt-collector]')
  expect(scripts).toHaveLength(1)
  expect(scripts[0]!.src).toBe(new URL(collectorUrl, import.meta.url).href)
  vi.stubGlobal('KTProcessData', { ProcessEventCollector() {} })
  scripts[0]!.dispatchEvent(new Event('load'))
  await Promise.all([first, second])
})

it('reports load failure and allows an explicit retry', async () => {
  vi.stubGlobal('KTProcessData', undefined)
  const { ensureProcessCollector } = await import('./collector-loader')
  const pending = ensureProcessCollector()
  const failed = expect(pending).rejects.toThrow('采集器加载失败')
  document.head.querySelector('script[data-kt-collector]')!.dispatchEvent(new Event('error'))
  await failed
  const retry = ensureProcessCollector()
  vi.stubGlobal('KTProcessData', { ProcessEventCollector() {} })
  document.head.querySelector('script[data-kt-collector]')!.dispatchEvent(new Event('load'))
  await retry
})
