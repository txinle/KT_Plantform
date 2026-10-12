import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useComparisonPolling } from './useComparisonPolling'
import type { StudentProjectionStatus } from '../student-courses/api'

describe('bounded student comparison checks', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
  })
  afterEach(() => vi.useRealTimers())
  function harness(initial = 'pending', hasComparison = false) {
    const status = ref(initial as StudentProjectionStatus['comparison_status'])
    const version = ref('v1')
    const check = vi.fn().mockResolvedValue({ version: 'v1', comparison_status: 'pending' })
    const load = vi.fn().mockImplementation(async () => { status.value = 'ready'; version.value = 'v2' })
    let refresh!: () => Promise<void>
    const wrapper = mount(defineComponent({ setup() {
      refresh = useComparisonPolling(() => ({ version: version.value, comparison_status: status.value }), () => hasComparison, check, load).refresh
      return () => null
    } }))
    return { wrapper, check, load, refresh }
  }
  it('checks only at 5/15/30 seconds and never loads a still-pending full result', async () => {
    const h = harness()
    await vi.advanceTimersByTimeAsync(4_999)
    expect(h.check).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1)
    expect(h.check).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(10_000)
    expect(h.check).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(15_000)
    expect(h.check).toHaveBeenCalledTimes(3)
    await vi.advanceTimersByTimeAsync(120_000)
    expect(h.check).toHaveBeenCalledTimes(3)
    expect(h.load).not.toHaveBeenCalled()
    h.wrapper.unmount()
  })
  it('loads ready once, merges manual checks, and reuses an unchanged version', async () => {
    const h = harness()
    h.check.mockResolvedValue({ version: 'v2', comparison_status: 'ready' })
    await vi.advanceTimersByTimeAsync(5_000)
    await flushPromises()
    expect(h.load).toHaveBeenCalledTimes(1)
    await Promise.all([h.refresh(), h.refresh()])
    expect(h.check).toHaveBeenCalledTimes(2)
    expect(h.load).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(120_000)
    expect(h.check).toHaveBeenCalledTimes(2)
    h.wrapper.unmount()
  })
  it('merges manual refresh into a pending check and loads changed personal facts once', async () => {
    const h = harness()
    let resolve!: (value: unknown) => void
    h.check.mockImplementation(() => new Promise((done) => { resolve = done }))
    await vi.advanceTimersByTimeAsync(5_000)
    const refresh = h.refresh()
    expect(h.refresh()).toBe(refresh)
    resolve({ version: 'v2', comparison_status: 'pending' })
    await refresh
    expect(h.check).toHaveBeenCalledTimes(1)
    expect(h.load).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(120_000)
    expect(h.check).toHaveBeenCalledTimes(1)
    h.wrapper.unmount()
  })
  it.each(['ready', 'failed', 'updating'])('does not check an initially %s page', async (status) => {
    const h = harness(status)
    await vi.advanceTimersByTimeAsync(120_000)
    expect(h.check).not.toHaveBeenCalled()
    h.wrapper.unmount()
  })
  it('keeps existing comparisons quiet and never restarts after hiding', async () => {
    const existing = harness('updating', true)
    await vi.advanceTimersByTimeAsync(30_000)
    expect(existing.check).not.toHaveBeenCalled()
    existing.wrapper.unmount()
    const h = harness()
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' })
    document.dispatchEvent(new Event('visibilitychange'))
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.advanceTimersByTimeAsync(120_000)
    expect(h.check).not.toHaveBeenCalled()
    h.wrapper.unmount()
  })
  it('stops after failure or unmount, including an in-flight ready response', async () => {
    const h = harness()
    let resolve!: (value: unknown) => void
    h.check.mockImplementation(() => new Promise((done) => { resolve = done }))
    await vi.advanceTimersByTimeAsync(5_000)
    h.wrapper.unmount()
    resolve({ version: 'v2', comparison_status: 'ready' })
    await flushPromises()
    expect(h.load).not.toHaveBeenCalled()
    const failed = harness()
    failed.check.mockResolvedValue({ version: 'v1', comparison_status: 'failed' })
    await vi.advanceTimersByTimeAsync(120_000)
    expect(failed.check).toHaveBeenCalledTimes(1)
    failed.wrapper.unmount()
  })
})
