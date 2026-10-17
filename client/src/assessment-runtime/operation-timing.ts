type Action = 'login' | 'entry' | 'navigation' | 'submit' | 'finish'
type Stage = 'question_entry' | 'process_runtime' | 'offline_prepare' | 'local_intent'
  | 'draft_remote' | 'save_and_leave_remote' | 'checkpoint' | 'event_boundary' | 'submit_remote' | 'close' | 'lease' | 'ready'
  | 'local_store' | 'pointer_queue' | 'record_queue' | 'ack' | 'tools'
  | 'login_remote' | 'course_list_read' | 'task_list_read'

interface MaterialClock { client_monotonic_ms: number; client_time_origin: string; event_id?: string; client_sequence_no?: number }

export function measureConfirmedMaterial(snapshot: { event_boundary: number; events: MaterialClock[]; checkpoints: MaterialClock[] }, id: string | null) {
  if (typeof performance.measure !== 'function') return false
  const now = performance.now()
  const records = [...snapshot.events, ...snapshot.checkpoints]
  const starts = records.filter(row => Number.isFinite(row.client_monotonic_ms)
    && row.client_monotonic_ms >= 0 && row.client_monotonic_ms <= now
    && Math.abs(Date.parse(row.client_time_origin) - performance.timeOrigin) < 1)
    .map(row => row.client_monotonic_ms)
  if (!starts.length) return false
  for (const event of snapshot.events) {
    if (!event.event_id || !Number.isFinite(event.client_monotonic_ms) || event.client_monotonic_ms < 0
      || event.client_monotonic_ms > now || Math.abs(Date.parse(event.client_time_origin) - performance.timeOrigin) >= 1
      || !Number.isFinite(Date.parse(event.client_time_origin))) continue
    performance.measure('kt-assessment:event_confirmation', {
      start: event.client_monotonic_ms, duration: now - event.client_monotonic_ms,
      detail: { operation_id: id, stage: 'event_confirmation', outcome: 'success',
        event_id: event.event_id, client_sequence_no: event.client_sequence_no, client_time_origin: event.client_time_origin },
    })
  }
  const started = Math.min(...starts)
  performance.measure('kt-assessment:background_confirmation', {
    start: started, duration: now - started,
    detail: { operation_id: id, stage: 'background_confirmation', outcome: 'success',
      event_boundary: snapshot.event_boundary, event_count: snapshot.events.length,
      checkpoint_count: snapshot.checkpoints.length, timed_record_count: starts.length,
      untimed_record_count: records.length - starts.length },
  })
  return true
}

let operation: { id: string; action: Action; started: number } | undefined

export function currentAssessmentOperationId() { return operation?.id }
export function currentAssessmentAction() { return operation?.action }

export function beginAssessmentOperation(action: Action) {
  operation = { id: crypto.randomUUID().replaceAll('-', ''), action, started: performance.now() }
  return operation.id
}

function measure(stage: string, started: number, id: string | undefined, outcome: 'success' | 'failure') {
  if (!id || typeof performance.measure !== 'function') return
  performance.measure(`kt-assessment:${stage}`, {
    start: started, duration: Math.max(0, performance.now() - started),
    detail: { operation_id: id, stage, outcome },
  })
}

export async function timedAssessmentStage<T>(stage: Stage, action: () => T | Promise<T>, id: string | null = operation?.id || null): Promise<T> {
  const started = performance.now()
  try {
    const result = await action()
    measure(stage, started, id || undefined, 'success')
    return result
  } catch (error) {
    measure(stage, started, id || undefined, 'failure')
    throw error
  }
}

export function finishAssessmentOperation(success = true) {
  if (!operation) return
  measure(operation.action, operation.started, operation.id, success ? 'success' : 'failure')
  operation = undefined
}
