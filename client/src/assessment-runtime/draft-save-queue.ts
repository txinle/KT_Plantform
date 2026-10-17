/** Keep the sent write intact and replace only the next, unsent pointer target. */
export function createDraftSaveQueue() {
  interface Pending {
    write: () => Promise<void>
    promise: Promise<void>
    resolve: () => void
    reject: (error: unknown) => void
  }
  let pending: Pending | undefined
  let sending = false
  function rejectPending(error: unknown) {
    const next = pending
    pending = undefined
    next?.reject(error)
  }

  async function drain() {
    sending = true
    while (pending) {
      const current = pending
      pending = undefined
      try {
        await current.write()
        current.resolve()
      } catch (error) {
        current.reject(error)
        // An unknown sent outcome must be recovered before any newer target.
        rejectPending(error)
        break
      }
    }
    sending = false
  }

  return {
    enqueue(write: () => Promise<void>): Promise<void> {
      if (pending) { pending.write = write; return pending.promise }
      let resolve!: () => void
      let reject!: (error: unknown) => void
      const promise = new Promise<void>((done, fail) => { resolve = done; reject = fail })
      pending = { write, promise, resolve, reject }
      if (!sending) void drain()
      return promise
    },
  }
}
