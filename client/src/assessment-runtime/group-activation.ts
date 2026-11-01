import type { GroupActivationIntent, GroupAnswerChange } from '@/question-content/question-group-content'

export function createGroupActivationQueue(options: {
  active: () => string | undefined
  activate: (itemKey: string) => Promise<void>
  apply: (change: GroupAnswerChange) => void | Promise<void>
  applyConfidence?: (itemKey: string, value: string) => Promise<void>
}) {
  let queue: Promise<unknown> = Promise.resolve()
  return {
    enqueue(intent: GroupActivationIntent): Promise<void> {
      const operation = queue.then(async () => {
        if (options.active() !== intent.itemKey) await options.activate(intent.itemKey)
        if (options.active() !== intent.itemKey) throw new Error('目标小题尚未成功进入，输入已保留。')
        if (intent.hasAnswer) await options.apply({ itemKey: intent.itemKey, answer: intent.answer })
        if (intent.confidence !== undefined) await options.applyConfidence?.(intent.itemKey, intent.confidence)
      })
      queue = operation.catch(() => undefined)
      return operation
    },
    settled: () => queue,
  }
}
