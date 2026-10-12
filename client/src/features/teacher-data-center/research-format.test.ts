import { describe, expect, it } from 'vitest'
import { formatResearchTime } from './research-format'

describe('research time display', () => {
  it('displays compact Beijing time without treating it as a browser date', () => {
    expect(formatResearchTime('20260909T190925.339')).toBe('2026-09-09 19:09:25.339')
    expect(formatResearchTime('20260909T190925.339', true)).toBe('19:09:25.339')
  })
  it('keeps a missing boundary explicit and converts ISO time to Beijing', () => {
    expect(formatResearchTime('')).toBe('未记录')
    expect(formatResearchTime('2026-09-09T11:09:25.339Z', true)).toBe('19:09:25.339')
  })
})
