import { afterEach, describe, expect, it, vi } from 'vitest'
import * as timing from './operation-timing'

afterEach(() => { timing.finishAssessmentOperation(); vi.unstubAllGlobals() })

describe('confirmed material timing', () => {
  it('records each acknowledged event age and excludes foreign clock origins', () => {
    const measure = vi.fn()
    vi.stubGlobal('performance', { now: () => 1000, timeOrigin: Date.parse('2026-10-16T00:00:00Z'), measure })
    timing.measureConfirmedMaterial({ event_boundary: 4, events: [
      { event_id: 'A', client_sequence_no: 1, client_monotonic_ms: 100, client_time_origin: '2026-10-16T00:00:00Z' },
      { event_id: 'B', client_sequence_no: 2, client_monotonic_ms: 200, client_time_origin: '2026-10-16T00:00:00Z' },
      { event_id: 'old', client_sequence_no: 3, client_monotonic_ms: 50, client_time_origin: '2026-10-15T00:00:00Z' },
    ], checkpoints: [] }, 'save')
    const events = measure.mock.calls.filter(row => row[0] === 'kt-assessment:event_confirmation')
    expect(events.map(row => [row[1].detail.event_id, row[1].duration])).toEqual([['A', 900], ['B', 800]])
    expect(events[0]![1].detail).toMatchObject({ operation_id: 'save', client_sequence_no: 1, outcome: 'success' })
  })
  it('measures original emission to matching ACK independently of the current UI operation', () => {
    const measure = vi.fn()
    const origin = Date.parse('2026-10-13T00:00:00Z')
    vi.stubGlobal('performance', { now: () => 2000, timeOrigin: origin, measure })
    expect(typeof timing.measureConfirmedMaterial).toBe('function')
    expect(timing.measureConfirmedMaterial({ event_boundary: 4,
      events: [{ client_monotonic_ms: 100, client_time_origin: '2026-10-13T00:00:00Z' }],
      checkpoints: [{ client_monotonic_ms: 500, client_time_origin: '2026-10-13T00:00:00Z' }],
    }, 'original-operation')).toBe(true)
    expect(measure).toHaveBeenCalledExactlyOnceWith('kt-assessment:background_confirmation', {
      start: 100, duration: 1900,
      detail: { operation_id: 'original-operation', stage: 'background_confirmation', outcome: 'success',
        event_boundary: 4, event_count: 1, checkpoint_count: 1, timed_record_count: 2, untimed_record_count: 0 },
    })
  })

  it('does not reinterpret old-document or future clocks as zero-duration successful uploads', () => {
    const measure = vi.fn()
    vi.stubGlobal('performance', { now: () => 2000, timeOrigin: Date.parse('2026-10-13T00:00:00Z'), measure })
    expect(typeof timing.measureConfirmedMaterial).toBe('function')
    expect(timing.measureConfirmedMaterial({ event_boundary: 4,
      events: [{ client_monotonic_ms: 100, client_time_origin: '2026-10-12T00:00:00Z' },
        { client_monotonic_ms: 2500, client_time_origin: '2026-10-13T00:00:00Z' }], checkpoints: [],
    }, 'original-operation')).toBe(false)
    expect(measure).not.toHaveBeenCalled()
  })

  it('reports untimed records separately while measuring valid current-document material', () => {
    const measure = vi.fn()
    vi.stubGlobal('performance', { now: () => 2000, timeOrigin: Date.parse('2026-10-13T00:00:00Z'), measure })
    expect(typeof timing.measureConfirmedMaterial).toBe('function')
    expect(timing.measureConfirmedMaterial({ event_boundary: 4,
      events: [{ client_monotonic_ms: 100, client_time_origin: '2026-10-13T00:00:00Z' },
        { client_monotonic_ms: 500, client_time_origin: '2026-10-12T00:00:00Z' }], checkpoints: [],
    }, null)).toBe(true)
    expect(measure.mock.calls[0]![1].detail).toMatchObject({ operation_id: null, timed_record_count: 1, untimed_record_count: 1 })
  })

  it('keeps user interaction duration in the full operation while stages use their own boundaries', async () => {
    let now = 100
    const measure = vi.fn()
    vi.stubGlobal('performance', { now: () => now, measure })
    timing.beginAssessmentOperation('submit')
    now = 5000
    await timing.timedAssessmentStage('submit_remote', () => { now = 5200 })
    now = 6000
    timing.finishAssessmentOperation()
    expect(measure.mock.calls.find(row => row[0] === 'kt-assessment:submit_remote')![1].duration).toBe(200)
    expect(measure.mock.calls.find(row => row[0] === 'kt-assessment:submit')![1].duration).toBe(5900)
  })
})
