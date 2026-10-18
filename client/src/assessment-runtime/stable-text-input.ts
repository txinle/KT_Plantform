export type TextFlushReason = 'idle' | 'blur' | 'navigation' | 'submit' | 'hidden' | 'deadline'
export interface TextCandidate { value: string; generation: number; baseHead: string | null; composing: boolean }
interface Options {
  initialValue: string
  idleMs: number
  getHead(): string | null
  persist(candidate: TextCandidate | null): Promise<void>
  record(value: string, inputType: string, operationId: string, cancelled: boolean): Promise<void>
  onStable(): Promise<unknown>
  onError(error: unknown): void
}

/** Mutable recovery candidate and append-only stable states have separate lifetimes. */
export function createStableTextInput(options: Options) {
  let value = options.initialValue
  let confirmedValue = value
  let generation = 0
  let composing = false
  let hadComposition = false
  let inputType = ''
  let disposed = false
  let idle: ReturnType<typeof setTimeout> | undefined
  let local: ReturnType<typeof setTimeout> | undefined
  let flight: Promise<void> | undefined
  let writes: Promise<void> = Promise.resolve()
  const clearIdle = () => { clearTimeout(idle); idle = undefined }
  const candidate = (): TextCandidate => ({ value, generation, baseHead: options.getHead(), composing })
  const persist = (row: TextCandidate | null) => {
    const pending = writes.then(() => options.persist(row))
    writes = pending.catch(() => undefined)
    return pending
  }
  const queueLocal = () => {
    if (local !== undefined) return
    local = setTimeout(() => {
      local = undefined
      void persist(candidate()).catch(options.onError)
    }, 250)
  }
  const scheduleIdle = () => {
    clearIdle()
    if (composing || disposed) return
    idle = setTimeout(() => {
      idle = undefined
      const settledGeneration = generation
      void flush('idle').then(() => {
        if (generation === settledGeneration) return options.onStable()
      }).catch(options.onError)
    }, options.idleMs)
  }
  const flush = (reason: TextFlushReason): Promise<void> => {
    clearIdle()
    clearTimeout(local)
    local = undefined
    if (disposed) return Promise.resolve()
    if (reason === 'hidden') return persist(candidate())
    if (composing) return persist(candidate()).then(() => {
      throw Object.assign(new Error('输入法尚未确认，请完成输入后重试。'), { code: 'input_composition_pending' })
    })
    if (flight) return flight.then(() => value !== confirmedValue || hadComposition ? flush(reason) : undefined)
    const snapshot = candidate()
    const operationType = inputType || 'insertText'
    const composition = hadComposition
    const pending = (async () => {
      await persist(snapshot)
      if (snapshot.value !== confirmedValue || composition) {
        await options.record(snapshot.value, operationType, crypto.randomUUID(), snapshot.value === confirmedValue)
        confirmedValue = snapshot.value
      }
      if (snapshot.generation === generation) {
        hadComposition = false
        inputType = ''
        await persist(null)
      } else await persist(candidate())
    })()
    flight = pending.finally(() => { flight = undefined })
    return flight
  }
  return {
    update(next: string, type: string, isComposing: boolean) {
      if (disposed) return
      value = next
      generation++
      composing ||= isComposing
      if (inputType && inputType !== type && value !== confirmedValue) inputType = hadComposition ? 'insertCompositionText' : 'insertReplacementText'
      else inputType = type || 'insertText'
      queueLocal()
      scheduleIdle()
    },
    compositionStart() {
      composing = true
      hadComposition = true
      clearIdle()
      queueLocal()
    },
    compositionEnd(next: string) {
      composing = false
      hadComposition = true
      value = next
      inputType = 'insertCompositionText'
      generation++
      queueLocal()
      scheduleIdle()
    },
    flush,
    resume() {
      if (!disposed && (value !== confirmedValue || hadComposition)) { queueLocal(); scheduleIdle() }
    },
    dispose() { disposed = true; clearIdle(); clearTimeout(local) },
  }
}
