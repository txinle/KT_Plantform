/** Stop foreground waits without treating an unobserved commit as a rollback. */
export function createOperationBudget(onSlow?: () => void, timeoutMs = 60000) {
  const deadline = performance.now() + timeoutMs
  const expired = () => Object.assign(new Error('等待超时，作答材料仍保留，请核验原操作。'), { code: 'assessment_wait_expired' })
  return {
    async run<T>(action: () => T | Promise<T>): Promise<T> {
      const remaining = deadline - performance.now()
      if (remaining <= 0) throw expired()
      let timer: ReturnType<typeof setTimeout> | undefined
      const slow = onSlow ? setTimeout(onSlow, 3000) : undefined
      try {
        return await Promise.race([
          Promise.resolve().then(action),
          new Promise<never>((_resolve, reject) => { timer = setTimeout(() => reject(expired()), remaining) }),
        ])
      } finally { clearTimeout(timer); clearTimeout(slow) }
    },
  }
}
