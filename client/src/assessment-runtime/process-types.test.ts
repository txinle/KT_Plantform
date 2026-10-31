import { expect, it } from 'vitest'
import { sanitizePayload } from './process-types'

it.each(['group_presented', 'group_left'])('keeps %s outside the existing collection contract', eventType => {
  expect(() => sanitizePayload(eventType, { group_snapshot_id: 3 })).toThrow('Unsupported process event type')
})
