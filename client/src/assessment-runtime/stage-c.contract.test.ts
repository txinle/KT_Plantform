import { describe, expect, it, vi } from 'vitest'
import { createStudentAssessmentApi, type NavigationClickContext } from './api'

// Stage C acceptance tests are intentionally red until the diagnostic gate releases implementation.
interface EntryApi {
  enterQuestion(sessionId: number, assignmentId: number, reason: string, click: NavigationClickContext): Promise<unknown>
}

const click: NavigationClickContext = Object.freeze({
  navigationId: '00000000-0000-4000-8000-000000000010',
  clientOccurredAt: '2026-10-12T08:00:00.000Z',
  clientMonotonicMs: 62422.300000000745,
  clientTimeOrigin: '2026-10-12T07:59:58.765Z',
})
const path = '/api/v1/student/sessions/7/questions/11/'
const material = {
  reason: 'navigation_click', navigation_id: click.navigationId,
  client_occurred_at: click.clientOccurredAt, client_monotonic_ms: '62422.300',
  client_time_origin: click.clientTimeOrigin,
}

function fixture() {
  const dto = { session_id: 7, assignment: { assignment_id: 11 }, process: {
    visit_id: null, visit_index: null, entry_event_id: null, navigation_id: null,
  } }
  const client = { get: vi.fn().mockResolvedValue(dto), mutate: vi.fn() }
  const api = createStudentAssessmentApi(client)
  return { client, api, entry: api as typeof api & EntryApi, dto }
}

describe('Stage C question entry contract', () => {
  it('loads an authorized read-only DTO without putting entry material into a GET query', async () => {
    const { client, api, dto } = fixture()
    expect(await api.question(7, 11)).toBe(dto)
    expect(client.get).toHaveBeenCalledExactlyOnceWith(path)
    expect(client.mutate).not.toHaveBeenCalled()
  })

  it('preserves null visit facts in a read-only DTO without manufacturing entry facts', async () => {
    const { client, api, dto } = fixture()
    const result = await api.question(7, 11)
    expect(result).toBe(dto)
    expect(result.process).toEqual({ visit_id: null, visit_index: null, entry_event_id: null, navigation_id: null })
    expect(client.mutate).not.toHaveBeenCalled()
  })

  it('posts the original frozen click material to the same business endpoint for formal entry', async () => {
    const { client, entry } = fixture()
    await entry.enterQuestion(7, 11, 'navigation_click', click)
    expect(client.mutate).toHaveBeenCalledExactlyOnceWith(path, { method: 'POST', body: material, signal: expect.any(AbortSignal) })
    expect(client.get).not.toHaveBeenCalled()
  })

  it('returns the complete entry DTO and immutable receipt without a second GET round trip', async () => {
    const { client, entry, dto } = fixture()
    const committed = { ...dto, entry_receipt: { navigation_id: click.navigationId } }
    client.mutate.mockResolvedValue(committed)
    expect(await entry.enterQuestion(7, 11, 'navigation_click', click)).toBe(committed)
    expect(client.get).not.toHaveBeenCalled()
  })

  it('replays an uncertain entry with exactly the original token and clock material', async () => {
    const { client, entry } = fixture()
    client.mutate.mockRejectedValueOnce(new TypeError('Network response unavailable')).mockResolvedValueOnce({})
    await expect(entry.enterQuestion(7, 11, 'navigation_click', click)).rejects.toThrow('Network response unavailable')
    await entry.enterQuestion(7, 11, 'navigation_click', click)
    expect(client.mutate.mock.calls).toEqual([
      [path, { method: 'POST', body: material, signal: expect.any(AbortSignal) }],
      [path, { method: 'POST', body: material, signal: expect.any(AbortSignal) }],
    ])
  })

  it('does not retry or consume a business conflict as successful entry', async () => {
    const { client, entry } = fixture()
    const conflict = Object.assign(new Error('Conflict'), { status: 409, code: 'writer_lease_conflict' })
    client.mutate.mockRejectedValue(conflict)
    await expect(entry.enterQuestion(7, 11, 'navigation_click', click)).rejects.toBe(conflict)
    expect(client.mutate).toHaveBeenCalledOnce()
    expect(client.get).not.toHaveBeenCalled()
  })
})
