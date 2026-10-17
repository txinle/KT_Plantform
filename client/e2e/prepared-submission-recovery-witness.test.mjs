import assert from 'node:assert/strict'
import test from 'node:test'
import { existsSync, mkdtempSync, readFileSync, rmdirSync, unlinkSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const accepted = { success: true, submission_id: 'actual-job', status: 'queued', accepted_at: '2026-10-15T00:00:00Z',
  submission_token: 'private-token', answer: 'private-answer' }

test('the coordination marker is derived only from an actual pending 202 and contains no payload', async () => {
  const { acceptedMarker } = await import('./prepared-submission-recovery-witness.mjs')
  const marker = acceptedMarker(202, accepted, '2026-10-15T00:00:01Z')
  assert.deepEqual(Object.keys(marker).sort(), ['accepted_at_sha256', 'observed_at_sha256', 'status', 'submission_id'])
  assert.equal(marker.submission_id, 'actual-job')
  assert.ok(!JSON.stringify(marker).includes('private-'))
  assert.throws(() => acceptedMarker(200, accepted))
  assert.throws(() => acceptedMarker(202, { ...accepted, status: 'completed' }))
  assert.throws(() => acceptedMarker(202, { ...accepted, success: false }))
})

test('the accepted marker cannot overwrite earlier worker-coordination evidence', async () => {
  const { writeAcceptedMarker } = await import('./prepared-submission-recovery-witness.mjs')
  const directory = mkdtempSync(join(tmpdir(), 'kt-submission-marker-'))
  const path = join(directory, 'accepted.json')
  try {
    writeAcceptedMarker(path, { submission_id: 'first' })
    assert.throws(() => writeAcceptedMarker(path, { submission_id: 'replacement' }))
    assert.equal(JSON.parse(readFileSync(path)).submission_id, 'first')
  } finally { if (existsSync(path)) unlinkSync(path); rmdirSync(directory) }
})

test('reload and completion must retain the real accepted receipt, not a new submission', async () => {
  const { sameSubmissionEvidence } = await import('./prepared-submission-recovery-witness.mjs')
  assert.equal(sameSubmissionEvidence('actual-job', 200, { ...accepted, status: 'completed' }).status, 'completed')
  assert.throws(() => sameSubmissionEvidence('actual-job', 200, { ...accepted, submission_id: 'different-job' }))
  assert.throws(() => sameSubmissionEvidence('actual-job', 500, accepted))
  assert.throws(() => sameSubmissionEvidence('actual-job', 200, { ...accepted, status: 'failed' }))
})

test('the worker restart mode and separate fresh output/marker destinations must be explicit', async () => {
  const { validateSubmissionMode } = await import('./prepared-submission-recovery-witness.mjs')
  assert.doesNotThrow(() => validateSubmissionMode('submission-worker-restart', '/private/new-report.json', '/private/new-marker.json', () => false))
  assert.throws(() => validateSubmissionMode(undefined, '/report', '/marker', () => false))
  assert.throws(() => validateSubmissionMode('submission-worker-restart', '/same', '/same', () => false))
  assert.throws(() => validateSubmissionMode('submission-worker-restart', '/report', '/marker', () => true))
})
