import { createHash } from 'node:crypto'
import { describe, expect, it } from 'vitest'
import { ApiError } from '@/api/client'
import { matchesMutationRejection, mutationMaterialFingerprint, sameDraftSemantics } from './mutation-material'

describe('mutation material identity', () => {
  it('hashes sorted UTF8 JSON with only the two root writer transport fields excluded', async () => {
    const document = { writer_lease_token: 'lease', writer_client_instance_id: 'client', capture_operation_id: 'operation',
      answer: { '2': '二', '10': '十', z: ['答复', null] }, capture_bundle: { writer_lease_token: 'nested', events: [] } }
    const canonical = '{"answer":{"10":"十","2":"二","z":["答复",null]},"capture_bundle":{"events":[],"writer_lease_token":"nested"},"capture_operation_id":"operation"}'
    expect(await mutationMaterialFingerprint(document)).toBe(createHash('sha256').update(canonical, 'utf8').digest('hex'))
    expect(await mutationMaterialFingerprint({ ...document, writer_lease_token: 'new' })).toBe(await mutationMaterialFingerprint(document))
    expect(await mutationMaterialFingerprint({ ...document, inline_confidence: null })).not.toBe(await mutationMaterialFingerprint(document))
  })

  it('compares complete draft semantics including confidence presence while ignoring transport and CAS', () => {
    const original = { answer: { text: 'A' }, target_event_id: 'head', expected_version: 1, client_occurred_at: 'old' }
    expect(sameDraftSemantics(original, { ...original, expected_version: 4, writer_lease_token: 'new', client_occurred_at: 'new' })).toBe(true)
    expect(sameDraftSemantics(original, { ...original, inline_confidence: null })).toBe(false)
    expect(sameDraftSemantics({ ...original, inline_confidence: { probability: null, answer_event_id: 'head' } },
      { ...original, inline_confidence: { probability: 50, answer_event_id: 'head' } })).toBe(false)
    expect(sameDraftSemantics(original, { ...original, answer: { text: 'B' } })).toBe(false)
  })

  it('matches Python canonical exponent spelling after the request JSON is parsed', async () => {
    const document = { small: 0.00001, smaller: 1.25e-7, normal: 0.0001, large: 1e21, integer: 1e20 }
    const canonical = '{"integer":100000000000000000000,"large":1e+21,"normal":0.0001,"small":1e-05,"smaller":1.25e-07}'
    expect(await mutationMaterialFingerprint(document)).toBe(createHash('sha256').update(canonical).digest('hex'))
  })

  it('binds rejection proof to kind, attempt, material and original operation', () => {
    const identity = { mutationKind: 'draft' as const, attemptId: 13, materialFingerprint: 'sha', captureOperationId: 'op' }
    const fields = { mutation_rejected: true, receipt_version: 2, mutation_kind: 'draft', attempt_id: 13, material_fingerprint: 'sha', capture_operation_id: 'op' }
    const error = (patch = {}) => new ApiError(409, { error: { fields: { ...fields, ...patch } } })
    expect(matchesMutationRejection(error(), identity)).toBe(true)
    for (const patch of [{ receipt_version: 1 }, { attempt_id: 14 }, { material_fingerprint: 'other' }, { capture_operation_id: 'other' }, { mutation_kind: 'navigation' }]) {
      expect(matchesMutationRejection(error(patch), identity)).toBe(false)
    }
    expect(matchesMutationRejection({ fields }, identity)).toBe(false)
  })
})
