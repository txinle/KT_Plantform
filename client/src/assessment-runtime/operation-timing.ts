type Action = 'entry' | 'navigation' | 'submit' | 'finish'
type Stage = 'question_entry' | 'process_runtime' | 'offline_prepare' | 'local_intent'
  | 'draft_remote' | 'save_and_leave_remote' | 'checkpoint' | 'event_boundary' | 'submit_remote' | 'close' | 'lease' | 'ready'

let operation: { id: string; action: Action; started: number } | undefined

export function currentAssessmentOperationId() { return operation?.id }

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

export async function timedAssessmentStage<T>(stage: Stage, action: () => T | Promise<T>): Promise<T> {
  const id = operation?.id
  const started = performance.now()
  try {
    const result = await action()
    measure(stage, started, id, 'success')
    return result
  } catch (error) {
    measure(stage, started, id, 'failure')
    throw error
  }
}

export function finishAssessmentOperation(success = true) {
  if (!operation) return
  measure(operation.action, operation.started, operation.id, success ? 'success' : 'failure')
  operation = undefined
}
