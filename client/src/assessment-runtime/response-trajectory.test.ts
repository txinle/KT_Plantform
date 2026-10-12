import { describe, expect, it, vi } from 'vitest'
import { webcrypto } from 'node:crypto'
import { ResponseTrajectory } from './response-trajectory'
import { reactive } from 'vue'
vi.stubGlobal('crypto', webcrypto)

describe('confirmed response trajectory', () => {
  it('copies reactive option collections before asynchronous capture', async () => {
    const rows: Record<string, unknown>[] = []
    const record = vi.fn(async (_type, _payload, options) => { rows.push(await options.materialize()); return {} })
    const trajectory = new ResponseTrajectory('multiple_choice', reactive<string[]>([]), null, record)
    const selected = reactive(['A'])
    trajectory.change(selected, 'select')
    selected.push('B')
    await trajectory.settled()
    expect((rows[0]!.after_state as { value: unknown }).value).toEqual(['A'])
  })
  it('fixes A → AB → A → empty before any asynchronous recording', async () => {
    const records = new Map<string, Record<string, unknown>>()
    const record = vi.fn(async (_type, _payload, options) => { records.set(options.eventId, await options.materialize()); return {} })
    const trajectory = new ResponseTrajectory('short_answer', '', null, record)
    const heads = ['A', 'AB', 'A', ''].map((value) => trajectory.change(value, 'insertText'))
    await trajectory.settled()
    const rows = heads.map((id) => records.get(id!)!)
    expect(rows.map((row) => (row.after_state as { value: string }).value)).toEqual(['A', 'AB', 'A', ''])
    expect(rows.map((row) => row.parent_event_id)).toEqual([null, ...heads.slice(0, 3)])
    expect(rows[0]!.after_state_hash).toBe(rows[2]!.after_state_hash)
    expect(String(rows[0]!.after_state_hash)).toHaveLength(64)
  })

  it('captures a short response larger than 64 KiB without truncation', async () => {
    const rows: Record<string, unknown>[] = []
    const record = vi.fn(async (_type, _payload, options) => { rows.push(await options.materialize()); return {} })
    const text = 'x'.repeat((64 * 1024) + 1)
    const trajectory = new ResponseTrajectory('short_answer', '', null, record)

    trajectory.change(text, 'insertText')
    await trajectory.settled()

    expect((rows[0]!.after_state as { value: string }).value).toBe(text)
    expect(String(rows[0]!.after_state_hash)).toHaveLength(64)
  })

  it('retains all one thousand rapid state changes as one causal chain', async () => {
    const rows = new Map<string, Record<string, unknown>>()
    const record = vi.fn(async (_type, _payload, options) => {
      rows.set(options.eventId, await options.materialize())
      return {}
    })
    const trajectory = new ResponseTrajectory('short_answer', '', null, record)
    const heads = Array.from(
      { length: 1000 },
      (_, index) => trajectory.change(String(index + 1), 'insertText', `rapid-edit-${index + 1}`),
    )

    await trajectory.settled()

    expect(record).toHaveBeenCalledTimes(1000)
    expect(rows.size).toBe(1000)
    expect(heads.map((head) => rows.get(head!)!.parent_event_id)).toEqual([null, ...heads.slice(0, -1)])
    expect(heads.map((head) => (rows.get(head!)!.after_state as { value: string }).value)).toEqual(
      Array.from({ length: 1000 }, (_, index) => String(index + 1)),
    )
  })

  it('does not manufacture a state for duplicate IME completion', async () => {
    const record = vi.fn(async () => ({}))
    const trajectory = new ResponseTrajectory('fill_blank', '', null, record)
    trajectory.change('电路', 'insertCompositionText')
    trajectory.change('电路', 'insertCompositionText')
    await trajectory.settled()
    expect(record).toHaveBeenCalledTimes(1)
  })

  it('reports durable capture failure instead of claiming the latest state is saved', async () => {
    const trajectory = new ResponseTrajectory('short_answer', '', null, async () => { throw new Error('quota') })
    trajectory.change('A', 'insertText')
    trajectory.change('B', 'insertText')
    await expect(trajectory.settled()).rejects.toThrow('quota')
  })
})
