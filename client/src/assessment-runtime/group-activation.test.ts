import { expect, it } from 'vitest'
import { createGroupActivationQueue } from './group-activation'

it('buffers the first input while entry is pending and applies it only to the entered item', async () => {
  let entered = 'a1'
  let release: (() => void) | undefined
  const applied: string[] = []
  const queue = createGroupActivationQueue({
    active: () => entered,
    activate: async key => { await new Promise<void>(resolve => { release = resolve }); entered = key },
    apply: change => { applied.push(`${entered}:${change.answer}`) },
  })
  const first = queue.enqueue({ itemKey: 'a2', answer: 'f', hasAnswer: true })
  await Promise.resolve()
  const second = queue.enqueue({ itemKey: 'a2', answer: 'first', hasAnswer: true })
  expect(applied).toEqual([])
  release?.()
  await Promise.all([first, second])
  expect(applied).toEqual(['a2:f', 'a2:first'])
})

it('keeps a failed activation from applying an answer to the outgoing attempt', async () => {
  const applied: unknown[] = []
  const queue = createGroupActivationQueue({ active: () => 'a1', activate: async () => { throw new Error('save failed') }, apply: change => { applied.push(change) } })
  await expect(queue.enqueue({ itemKey: 'a2', answer: '', hasAnswer: true })).rejects.toThrow('save failed')
  expect(applied).toEqual([])
})

it('serializes each member confidence after its actual entry and before another member answer', async () => {
  let entered = 'a1'
  const operations: string[] = []
  const queue = createGroupActivationQueue({
    active: () => entered,
    activate: async key => { operations.push(`enter:${key}`); entered = key },
    apply: change => { operations.push(`answer:${entered}:${change.answer}`) },
    applyConfidence: async (key, value) => { expect(entered).toBe(key); operations.push(`confidence:${key}:${value}`) },
  })
  await Promise.all([queue.enqueue({ itemKey: 'a2', confidence: '0' }), queue.enqueue({ itemKey: 'a1', answer: '', hasAnswer: true })])
  expect(operations).toEqual(['enter:a2', 'confidence:a2:0', 'enter:a1', 'answer:a1:'])
})
