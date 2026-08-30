export interface TextInputDetails {
  inputType?: string
  isComposing?: boolean
  monotonicMs: number
}

export interface TextObservation {
  before: string
  after: string
  operation: 'paste' | 'delete' | 'replace' | 'insert' | 'unknown'
  method: 'ime' | 'paste' | 'keyboard' | 'unknown'
  isIme: boolean
  startedAt: number
  endedAt: number
}

function editOperation(inputType = ''): TextObservation['operation'] {
  if (inputType === 'insertFromPaste') return 'paste'
  if (inputType.startsWith('delete')) return 'delete'
  if (inputType === 'insertReplacementText' || inputType === 'insertFromDrop') return 'replace'
  if (inputType.startsWith('insert')) return 'insert'
  return 'unknown'
}

function inputMethod(inputType = '', composing = false): TextObservation['method'] {
  if (composing || inputType.includes('Composition')) return 'ime'
  if (inputType === 'insertFromPaste') return 'paste'
  return inputType ? 'keyboard' : 'unknown'
}

export function createTextObservation(
  before: string,
  after: string,
  details: TextInputDetails,
): TextObservation {
  const method = inputMethod(details.inputType, details.isComposing)
  return {
    before,
    after,
    operation: editOperation(details.inputType),
    method,
    isIme: method === 'ime',
    startedAt: details.monotonicMs,
    endedAt: details.monotonicMs,
  }
}

export function mergeTextObservation(
  observation: TextObservation,
  after: string,
  details: TextInputDetails,
): TextObservation {
  const method = inputMethod(details.inputType, details.isComposing)
  return {
    ...observation,
    after,
    endedAt: details.monotonicMs,
    isIme: observation.isIme || method === 'ime',
  }
}

export async function sha256Text(value: string): Promise<string | null> {
  if (!globalThis.crypto?.subtle || !globalThis.TextEncoder) return null
  const bytes = new globalThis.TextEncoder().encode(value)
  const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes)
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('')
}

export async function materializeTextObservation(
  questionType: string,
  observation: TextObservation,
  hashText: (value: string) => Promise<string | null> = sha256Text,
) {
  const [beforeHash, afterHash] = await Promise.all([
    hashText(observation.before),
    hashText(observation.after),
  ])
  if (!beforeHash || !afterHash) return null
  return {
    question_type: questionType,
    edit_operation: observation.operation,
    content_hash_before: beforeHash,
    content_hash_after: afterHash,
    content_length_before: observation.before.length,
    content_length_after: observation.after.length,
    input_method: observation.method,
    batch_start_client_monotonic_ms: observation.startedAt,
    batch_end_client_monotonic_ms: observation.endedAt,
    is_ime: observation.isIme,
  }
}
