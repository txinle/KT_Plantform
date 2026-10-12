type Recorder = (type: string, payload: Record<string, unknown>, options?: Record<string, unknown>) => Promise<unknown>

async function stateHash(state: { question_type: string; value: unknown }) {
  const bytes = new TextEncoder().encode(JSON.stringify(state))
  const digest = await crypto.subtle.digest('SHA-256', bytes)
  return [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, '0')).join('')
}

/** Fix input facts synchronously; serialize hashing and durable writes without sampling. */
export class ResponseTrajectory {
  readonly branchId = crypto.randomUUID()
  readonly questionType: string
  private value: unknown
  private record: Recorder
  private queue: Promise<unknown> = Promise.resolve()
  head: string | null

  constructor(questionType: string, value: unknown, head: string | null, record: Recorder) {
    this.questionType = questionType
    this.value = JSON.parse(JSON.stringify(value))
    this.head = head
    this.record = record
  }

  change(value: unknown, inputType: string, operationId: string = crypto.randomUUID(), extra = {}) {
    const before = { question_type: this.questionType, value: this.value }
    const after = { question_type: this.questionType, value: JSON.parse(JSON.stringify(value)) }
    if (JSON.stringify(before) === JSON.stringify(after)) return this.head
    const eventId = crypto.randomUUID()
    const parent = this.head
    const wallTime = new Date().toISOString()
    const monotonicMs = performance.now()
    this.value = after.value
    this.head = eventId
    const materialize = async () => {
      const [beforeHash, afterHash] = await Promise.all([stateHash(before), stateHash(after)])
      return {
        capture_version: 'capture-v2', operation_id: operationId, branch_id: this.branchId,
        parent_event_id: parent, before_state: before, after_state: after,
        before_state_hash: beforeHash, after_state_hash: afterHash, input_type: inputType,
        source: 'student', ...extra,
      }
    }
    const recording = this.record('response_transition', {}, { eventId, wallTime, monotonicMs, materialize })
    this.queue = Promise.all([this.queue, recording])
    // Preserve rejection for settled()/submission without an unhandled rejection
    // while a student is still typing. A failed link stops subsequent writes.
    void this.queue.catch(() => undefined)
    return eventId
  }

  operation(inputType: string, composition: string, selectionStart: number | null, selectionEnd: number | null, operationId: string) {
    const payload = {
      capture_version: 'capture-v2', operation_id: operationId, branch_id: this.branchId,
      parent_event_id: this.head, input_type: inputType, composition,
      selection_start: selectionStart, selection_end: selectionEnd,
    }
    const options = { eventId: crypto.randomUUID(), wallTime: new Date().toISOString(), monotonicMs: performance.now() }
    this.queue = Promise.all([this.queue, this.record('input_operation', payload, options)])
    void this.queue.catch(() => undefined)
  }

  settled() { return this.queue }
}
