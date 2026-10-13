import { ApiError } from '@/api/client'
import { describe, expect, it, vi } from 'vitest'

import {
  createWriterLease,
  type WriterLeaseClient,
} from './writer-lease'

const clientInstanceId = 'd2c09559-1e11-4a66-a10a-3e0f7dbd85e2'

function lease(token: string, expiresAt: number) {
  return {
    success: true,
    lease_token: token,
    client_instance_id: clientInstanceId,
    expires_at: new Date(expiresAt).toISOString(),
  }
}

function clientFor(post: ReturnType<typeof vi.fn>): WriterLeaseClient {
  return { post: post as WriterLeaseClient['post'] }
}

describe('capture-v2 writer lease', () => {
  it('acquires once and reuses credentials before later pointer writes', async () => {
    let now = Date.parse('2026-09-06T00:00:00.000Z')
    const post = vi.fn().mockResolvedValue(
      lease('lease-one', now + 30_000),
    )
    const writerLease = createWriterLease({
      client: clientFor(post),
      clientInstanceId,
      acquireUrl: '/process/attempts/13/writer-lease/acquire/',
      renewUrl: '/process/attempts/13/writer-lease/renew/',
      releaseUrl: '/process/attempts/13/writer-lease/release/',
      now: () => now,
    })

    const first = await writerLease.beforePointerWrite()
    now += 1_000
    const second = await writerLease.beforePointerWrite()

    expect(first).toEqual({
      writer_lease_token: 'lease-one',
      writer_client_instance_id: clientInstanceId,
    })
    expect(second).toEqual(first)
    expect(post).toHaveBeenCalledOnce()
    expect(post).toHaveBeenCalledWith(
      '/process/attempts/13/writer-lease/acquire/',
      { client_instance_id: clientInstanceId },
    )
  })

  it('renews nearing expiry and reacquires when that lease has expired', async () => {
    let now = Date.parse('2026-09-06T00:00:00.000Z')
    const post = vi.fn()
      .mockResolvedValueOnce(lease('lease-one', now + 10_000))
      .mockRejectedValueOnce(new ApiError(409, {
        error: { code: 'writer_lease_conflict', message: 'writer lease is not valid' },
      }))
      .mockResolvedValueOnce(lease('lease-two', now + 40_000))
    const writerLease = createWriterLease({
      client: clientFor(post),
      clientInstanceId,
      acquireUrl: '/process/attempts/13/writer-lease/acquire/',
      renewUrl: '/process/attempts/13/writer-lease/renew/',
      releaseUrl: '/process/attempts/13/writer-lease/release/',
      now: () => now,
      renewBeforeMs: 5_000,
    })

    await writerLease.beforePointerWrite()
    now += 6_000
    const replacement = await writerLease.beforePointerWrite()

    expect(replacement.writer_lease_token).toBe('lease-two')
    expect(post).toHaveBeenNthCalledWith(
      2,
      '/process/attempts/13/writer-lease/renew/',
      {
        lease_token: 'lease-one',
        client_instance_id: clientInstanceId,
      },
    )
    expect(post).toHaveBeenNthCalledWith(
      3,
      '/process/attempts/13/writer-lease/acquire/',
      { client_instance_id: clientInstanceId },
    )
  })

  it('reacquires after the cached expiry has elapsed', async () => {
    let now = Date.parse('2026-09-06T00:00:00.000Z')
    const post = vi.fn()
      .mockResolvedValueOnce(lease('lease-one', now + 1_000))
      .mockResolvedValueOnce(lease('lease-two', now + 31_000))
    const writerLease = createWriterLease({
      client: clientFor(post),
      clientInstanceId,
      acquireUrl: '/process/attempts/13/writer-lease/acquire/',
      renewUrl: '/process/attempts/13/writer-lease/renew/',
      releaseUrl: '/process/attempts/13/writer-lease/release/',
      now: () => now,
      renewBeforeMs: 0,
    })

    await writerLease.beforePointerWrite()
    now += 1_001
    const replacement = await writerLease.beforePointerWrite()

    expect(replacement.writer_lease_token).toBe('lease-two')
    expect(post).toHaveBeenCalledTimes(2)
    expect(post).toHaveBeenLastCalledWith(
      '/process/attempts/13/writer-lease/acquire/',
      { client_instance_id: clientInstanceId },
    )
  })

  it('surfaces an independent-device conflict with a clear error', async () => {
    const post = vi.fn().mockRejectedValue(new ApiError(409, {
      error: { code: 'writer_lease_conflict', message: 'attempt writer lease is held by another client' },
    }))
    const writerLease = createWriterLease({
      client: clientFor(post),
      clientInstanceId,
      acquireUrl: '/process/attempts/13/writer-lease/acquire/',
      renewUrl: '/process/attempts/13/writer-lease/renew/',
      releaseUrl: '/process/attempts/13/writer-lease/release/',
    })

    await expect(writerLease.beforePointerWrite()).rejects.toMatchObject({
      name: 'WriterLeaseConflictError',
      code: 'writer_lease_conflict',
      message: expect.stringContaining('另一页面或设备'),
    })
    expect(post).toHaveBeenCalledOnce()
  })

  it('releases cached credentials with keepalive and does not reacquire', async () => {
    const now = Date.parse('2026-09-06T00:00:00.000Z')
    const post = vi.fn()
      .mockResolvedValueOnce(lease('lease-one', now + 30_000))
      .mockResolvedValueOnce({ success: true })
    const writerLease = createWriterLease({
      client: clientFor(post),
      clientInstanceId,
      acquireUrl: '/process/attempts/13/writer-lease/acquire/',
      renewUrl: '/process/attempts/13/writer-lease/renew/',
      releaseUrl: '/process/attempts/13/writer-lease/release/',
      now: () => now,
    })
    await writerLease.beforePointerWrite()

    await writerLease.release({ keepalive: true })
    await writerLease.release({ keepalive: true })

    expect(post).toHaveBeenCalledTimes(2)
    expect(post).toHaveBeenLastCalledWith(
      '/process/attempts/13/writer-lease/release/',
      {
        lease_token: 'lease-one',
        client_instance_id: clientInstanceId,
      },
      { keepalive: true },
    )
  })

  it('replays the same release credentials after its acknowledgement is lost', async () => {
    const now = Date.parse('2026-09-06T00:00:00.000Z')
    const post = vi.fn().mockResolvedValueOnce(lease('lease-one', now + 30_000))
      .mockRejectedValueOnce(new Error('response lost')).mockResolvedValueOnce({ success: true })
    const writerLease = createWriterLease({ client: clientFor(post), clientInstanceId,
      acquireUrl: '/acquire/', renewUrl: '/renew/', releaseUrl: '/release/', now: () => now })
    await writerLease.beforePointerWrite()
    await expect(writerLease.release()).rejects.toThrow('response lost')
    await writerLease.release()
    expect(post).toHaveBeenCalledTimes(3)
    expect(post.mock.calls[2]).toEqual(post.mock.calls[1])
    expect(writerLease.cachedCredentials()).toBeUndefined()
  })
})
