import { afterEach, expect, it, vi } from 'vitest'
import { createStableTextInput, type TextCandidate } from './stable-text-input'

afterEach(() => vi.useRealTimers())
function fixture(initialValue = '') {
  const persisted: Array<TextCandidate | null> = []
  const record = vi.fn<(value: string, inputType: string, operationId: string, cancelled: boolean) => Promise<void>>(async () => undefined)
  const save = vi.fn(async () => undefined)
  const onError = vi.fn()
  const input = createStableTextInput({ initialValue, idleMs: 1000, getHead: () => 'head',
    persist: async candidate => { persisted.push(candidate) }, record, onStable: save, onError })
  return { input, persisted, record, save, onError }
}

it('records one full answer after 1000ms idle without periodic research snapshots', async () => {
  vi.useFakeTimers()
  const f = fixture()
  for (let n = 1; n <= 30; n++) {
    f.input.update('x'.repeat(n), 'insertText', false)
    await vi.advanceTimersByTimeAsync(100)
  }
  expect(f.record).not.toHaveBeenCalled()
  expect(f.save).not.toHaveBeenCalled()
  expect(f.persisted.length).toBeGreaterThan(0)
  await vi.advanceTimersByTimeAsync(899)
  expect(f.record).not.toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(1)
  expect(f.record).toHaveBeenCalledTimes(1)
  expect(f.record.mock.calls[0]![0]).toBe('x'.repeat(30))
  expect(f.save).toHaveBeenCalledTimes(1)
  f.input.dispose()
})

it('keeps stable A to B to empty to A changes and skips an unstable return', async () => {
  vi.useFakeTimers()
  const f = fixture('A')
  f.input.update('B', 'insertReplacementText', false)
  await f.input.flush('blur')
  f.input.update('', 'deleteContentBackward', false)
  await f.input.flush('navigation')
  f.input.update('A', 'insertText', false)
  await f.input.flush('submit')
  f.input.update('temporary', 'insertText', false)
  f.input.update('A', 'deleteContentBackward', false)
  await f.input.flush('blur')
  expect(f.record.mock.calls.map(row => row[0])).toEqual(['B', '', 'A'])
  f.input.dispose()
})

it('never confirms composing text and coalesces duplicate confirmation and boundary flush', async () => {
  vi.useFakeTimers()
  const f = fixture()
  f.input.compositionStart()
  f.input.update('pin', 'insertCompositionText', true)
  await vi.advanceTimersByTimeAsync(1500)
  expect(f.record).not.toHaveBeenCalled()
  await expect(f.input.flush('navigation')).rejects.toMatchObject({ code: 'input_composition_pending' })
  f.input.compositionEnd('拼')
  f.input.update('拼', 'insertText', false)
  await Promise.all([f.input.flush('blur'), f.input.flush('navigation'), f.input.flush('submit')])
  expect(f.record).toHaveBeenCalledTimes(1)
  expect(f.record.mock.calls[0]![0]).toBe('拼')
  f.input.dispose()
})

it('retains a newer candidate while an older stable state is being persisted', async () => {
  vi.useFakeTimers()
  const f = fixture()
  let done!: () => void
  f.record.mockImplementationOnce(() => new Promise<void>(resolve => { done = resolve }))
  f.input.update('A', 'insertText', false)
  const flush = f.input.flush('blur')
  await vi.waitFor(() => expect(f.record).toHaveBeenCalledTimes(1))
  f.input.update('AB', 'insertText', false)
  done()
  await flush
  await vi.advanceTimersByTimeAsync(250)
  expect(f.persisted.at(-1)).toMatchObject({ value: 'AB' })
  await f.input.flush('submit')
  expect(f.record.mock.calls.map(row => row[0])).toEqual(['A', 'AB'])
  f.input.dispose()
})

it('does not mark a failed local write as a confirmed stable answer', async () => {
  vi.useFakeTimers()
  const f = fixture()
  f.record.mockRejectedValueOnce(new Error('quota exceeded'))
  f.input.update('answer', 'insertText', false)
  await expect(f.input.flush('submit')).rejects.toThrow('quota exceeded')
  expect(f.save).not.toHaveBeenCalled()
  expect(f.persisted.at(-1)).toMatchObject({ value: 'answer' })
  f.input.dispose()
})

it('preserves paste provenance for a single stable paste operation', async () => {
  vi.useFakeTimers()
  const f = fixture()
  f.input.update('pasted answer', 'insertFromPaste', false)
  await f.input.flush('blur')
  expect(f.record.mock.calls[0]![1]).toBe('insertFromPaste')
  f.input.dispose()
})

it('a navigation flush waits for the newest candidate after an older local write', async () => {
  vi.useFakeTimers()
  const f = fixture()
  let done!: () => void
  f.record.mockImplementationOnce(() => new Promise<void>(resolve => { done = resolve }))
  f.input.update('A', 'insertText', false)
  const first = f.input.flush('blur')
  await vi.waitFor(() => expect(f.record).toHaveBeenCalledTimes(1))
  f.input.update('AB', 'insertText', false)
  const navigation = f.input.flush('navigation')
  done()
  await Promise.all([first, navigation])
  expect(f.record.mock.calls.map(row => row[0])).toEqual(['A', 'AB'])
  f.input.dispose()
})

it('does not autosave a newer unstable candidate when an older idle write completes', async () => {
  vi.useFakeTimers()
  const f = fixture()
  let done!: () => void
  f.record.mockImplementationOnce(() => new Promise<void>(resolve => { done = resolve }))
  f.input.update('A', 'insertText', false)
  await vi.advanceTimersByTimeAsync(1000)
  f.input.update('AB', 'insertText', false)
  done()
  await vi.advanceTimersByTimeAsync(0)
  expect(f.save).not.toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(1000)
  expect(f.save).toHaveBeenCalledOnce()
  f.input.dispose()
})

it('resumes stability timing for a retained candidate after the page becomes visible', async () => {
  vi.useFakeTimers()
  const f = fixture()
  f.input.update('before background', 'insertText', false)
  await f.input.flush('hidden')
  await vi.advanceTimersByTimeAsync(5000)
  expect(f.record).not.toHaveBeenCalled()
  f.input.resume()
  await vi.advanceTimersByTimeAsync(1000)
  expect(f.record).toHaveBeenCalledTimes(1)
  expect(f.save).toHaveBeenCalledOnce()
  f.input.dispose()
})
