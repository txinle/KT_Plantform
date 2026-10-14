import { afterEach, describe, expect, it, vi } from 'vitest'

import { createStudentAssessmentApi } from './api'
import { createApiClient, MutationOutcomeUnknownError } from '@/api/client'
import { writeAdmissionCredentials } from './admission'

describe('student assessment API', () => {
  afterEach(() => sessionStorage.clear())

  it('aborts a close transport at 30 seconds and reports an unknown outcome', async () => {
    vi.useFakeTimers()
    const fetchImpl = vi.fn((_url, init: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init.signal?.addEventListener('abort', () => reject(init.signal!.reason))
    }))
    try {
      const api = createStudentAssessmentApi(createApiClient({ fetchImpl: fetchImpl as never }))
      const close = api.post('/release/', { capture_operation_id: 'original' }, { timeoutMs: 30000 })
      const outcome = expect(close).rejects.toBeInstanceOf(MutationOutcomeUnknownError)
      await vi.advanceTimersByTimeAsync(29999)
      expect(fetchImpl.mock.calls[0]![1].signal?.aborted).toBe(false)
      await vi.advanceTimersByTimeAsync(1)
      await outcome
      expect(fetchImpl.mock.calls[0]![1].signal?.aborted).toBe(true)
      expect(vi.getTimerCount()).toBe(0)
    } finally { vi.useRealTimers() }
  })

  it.each([undefined, 19])('sends only permit fields for the session, including task %s', async taskId => {
    writeAdmissionCredentials(19, { admission_token: 'permit', admission_action: 'start', session_id: 7 })
    const client = { get: vi.fn(), mutate: vi.fn().mockResolvedValue({}) }
    await createStudentAssessmentApi(client as never).enterQuestion(7, 11, 'resume', {
      navigationId: '00000000-0000-4000-8000-000000000010',
      clientOccurredAt: '2026-10-12T08:00:00.000Z', clientMonotonicMs: 100,
      clientTimeOrigin: '2026-10-12T07:59:58.765Z',
    }, taskId)
    const body = client.mutate.mock.calls[0][1].body
    expect(body).toMatchObject({ admission_token: 'permit', admission_action: 'start' })
    expect(body).not.toHaveProperty('session_id')
  })

  it('sends a frozen atomic envelope on the existing submission URL', async () => {
    const client = { get: vi.fn(), mutate: vi.fn().mockResolvedValue({ committed: true }) }
    const envelope = { version: 1 as const, client_instance_id: 'client', event_boundary: 8,
      events: [], checkpoints: [], expected_version: 2 }
    await createStudentAssessmentApi(client as never).submit('/student/attempts/13/submit/', 'token',
      '2026-10-11T09:00:00Z', 'head', undefined, undefined, envelope)
    expect(client.mutate).toHaveBeenCalledWith('/student/attempts/13/submit/', expect.objectContaining({
      body: expect.objectContaining({ submission_token: 'token', target_event_id: 'head', atomic_submission: envelope }),
    }))
  })

  it('loads the authoritative assignment and preserves legacy mutation URLs', async () => {
    const client = {
      get: vi.fn().mockResolvedValue({}),
      mutate: vi.fn().mockResolvedValue({}),
    }
    const api = createStudentAssessmentApi(client as never)

    await api.question(7, 10)
    await api.enterQuestion(7, 11, 'navigation_click', {
      navigationId: '00000000-0000-4000-8000-000000000010',
      clientOccurredAt: '2026-08-30T08:00:00.000Z',
      clientMonotonicMs: 62422.300000000745,
      clientTimeOrigin: '2026-08-30T07:59:58.765Z',
    })
    await api.saveDraft('/student/attempts/13/draft/', 'B', '2026-08-30T08:00:00Z')
    await api.submit('/student/attempts/13/submit/', '00000000-0000-4000-8000-000000000001', '2026-08-30T08:00:01Z')
    await api.skip('/student/attempts/13/skip/')
    await api.post('/assessment/process/confidence/13/commit/', { probability: 80 })
    await api.post(
      '/assessment/process/attempts/13/writer-lease/release/',
      { lease_token: 'lease-one' },
      { keepalive: true },
    )

    expect(client.get.mock.calls).toEqual([['/api/v1/student/sessions/7/questions/10/']])
    expect(client.mutate.mock.calls).toEqual([
      ['/api/v1/student/sessions/7/questions/11/', { method: 'POST', body: { reason: 'navigation_click', navigation_id: '00000000-0000-4000-8000-000000000010', client_occurred_at: '2026-08-30T08:00:00.000Z', client_monotonic_ms: '62422.300', client_time_origin: '2026-08-30T07:59:58.765Z' } }],
      [
        '/student/attempts/13/draft/',
        {
          method: 'POST',
          body: { answer: 'B', client_occurred_at: '2026-08-30T08:00:00Z' },
        },
      ],
      [
        '/student/attempts/13/submit/',
        {
          method: 'POST',
          body: {
            submission_token: '00000000-0000-4000-8000-000000000001',
            client_clicked_at: '2026-08-30T08:00:01Z',
          },
        },
      ],
      ['/student/attempts/13/skip/', { method: 'POST' }],
      [
        '/assessment/process/confidence/13/commit/',
        { method: 'POST', body: { probability: 80 } },
      ],
      [
        '/assessment/process/attempts/13/writer-lease/release/',
        { method: 'POST', body: { lease_token: 'lease-one' }, keepalive: true },
      ],
    ])
  })
})
