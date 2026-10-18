import { afterEach, expect, it, vi } from 'vitest'
import { createOperationBudget } from './operation-budget'
afterEach(() => vi.useRealTimers())
it('shares a 60 second budget across phases and never starts the next phase after expiry', async () => {
  vi.useFakeTimers()
  const budget = createOperationBudget()
  await vi.advanceTimersByTimeAsync(40000)
  const blocked = budget.run(() => new Promise(() => undefined))
  const outcome = expect(blocked).rejects.toMatchObject({ code: 'assessment_wait_expired' })
  await vi.advanceTimersByTimeAsync(20000)
  await outcome
  const next = vi.fn()
  await expect(budget.run(next)).rejects.toMatchObject({ code: 'assessment_wait_expired' })
  expect(next).not.toHaveBeenCalled()
})
it('announces a slow phase once after 3 seconds and clears its timers', async () => {
  vi.useFakeTimers()
  const slow = vi.fn()
  const budget = createOperationBudget(slow)
  let done!: (value: string) => void
  const result = budget.run(() => new Promise<string>(resolve => { done = resolve }))
  await vi.advanceTimersByTimeAsync(3000)
  expect(slow).toHaveBeenCalledOnce()
  done('confirmed')
  await expect(result).resolves.toBe('confirmed')
  expect(vi.getTimerCount()).toBe(0)
})
