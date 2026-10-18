import { describe, expect, it } from 'vitest'
import { MemoryOutbox } from './fact-store'
import type { CaptureMutation } from './capture-contract'

describe('atomic rejected mutation release', () => {
  it('compares the complete pending owner before archiving and clearing it, preserving other metadata', async () => {
    const store = new MemoryOutbox()
    const intent: CaptureMutation = { url: '/draft/', body: { capture_operation_id: 'op' }, sessionId: 7, attemptId: 13,
      processSnapshot: { version: 1, client_instance_id: 'client', event_boundary: 0, events: [], checkpoints: [] }, phase: 'sent', materialFingerprint: 'sha' }
    const rejection = { code: 'rejected', status: 409, fields: {}, materialFingerprint: 'sha', captureOperationId: 'op', rejectedAt: 'time' }
    await store.writeMetadata('capture-mutation:7:13', intent)
    await store.writeMetadata('capture-flush:7', { immutable: 'flush' })
    expect(await store.rejectCaptureMutation({ ...intent, body: { capture_operation_id: 'other' } }, rejection)).toBe(false)
    expect(await store.readMetadata('capture-mutation:7:13')).toEqual(intent)
    expect(await store.readMetadata('capture-mutation-rejection:7:13')).toBeNull()
    expect(await store.rejectCaptureMutation(intent, rejection)).toBe(true)
    expect(await store.readMetadata('capture-mutation:7:13')).toBeNull()
    expect(await store.readMetadata('capture-mutation-rejection:7:13')).toEqual(rejection)
    expect(await store.readMetadata('capture-flush:7')).toEqual({ immutable: 'flush' })
    expect(await store.rejectCaptureMutation(intent, rejection)).toBe(false)
  })
})
