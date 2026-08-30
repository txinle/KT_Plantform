import { describe, expect, it, vi } from 'vitest'

import {
  createTextObservation,
  materializeTextObservation,
  mergeTextObservation,
} from './text-observation'

describe('stable text observations', () => {
  it('classifies and merges an edit batch without retaining answer content', async () => {
    const first = createTextObservation('旧答', '知识', {
      inputType: 'insertText',
      isComposing: false,
      monotonicMs: 10,
    })
    const merged = mergeTextObservation(first, '知识追踪', {
      inputType: 'insertText',
      isComposing: false,
      monotonicMs: 18,
    })
    const hash = vi.fn(async (value: string) => `hash:${value.length}`)

    const payload = await materializeTextObservation('short_answer', merged, hash)

    expect(payload).toEqual({
      question_type: 'short_answer',
      edit_operation: 'insert',
      content_hash_before: 'hash:2',
      content_hash_after: 'hash:4',
      content_length_before: 2,
      content_length_after: 4,
      input_method: 'keyboard',
      batch_start_client_monotonic_ms: 10,
      batch_end_client_monotonic_ms: 18,
      is_ime: false,
    })
    expect(JSON.stringify(payload)).not.toContain('旧答')
    expect(JSON.stringify(payload)).not.toContain('知识追踪')
  })

  it('classifies paste, delete, replacement, and IME input', () => {
    expect(createTextObservation('', 'a', { inputType: 'insertFromPaste', isComposing: false, monotonicMs: 1 }).operation).toBe('paste')
    expect(createTextObservation('a', '', { inputType: 'deleteContentBackward', isComposing: false, monotonicMs: 1 }).operation).toBe('delete')
    expect(createTextObservation('a', 'b', { inputType: 'insertReplacementText', isComposing: false, monotonicMs: 1 }).operation).toBe('replace')
    expect(createTextObservation('', '中', { inputType: 'insertCompositionText', isComposing: true, monotonicMs: 1 }).method).toBe('ime')
  })
})
