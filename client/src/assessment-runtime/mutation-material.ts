import { ApiError } from '@/api/client'

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`
  if (value && typeof value === 'object') {
    const object = value as Record<string, unknown>
    const keys = Object.keys(object).sort((left, right) => {
      const a = Array.from(left, character => character.codePointAt(0)!)
      const b = Array.from(right, character => character.codePointAt(0)!)
      for (let index = 0; index < Math.min(a.length, b.length); index++) {
        if (a[index] !== b[index]) return a[index]! - b[index]!
      }
      return a.length - b.length
    })
    return `{${keys.map(key => `${JSON.stringify(key)}:${canonicalJson(object[key])}`).join(',')}}`
  }
  let encoded = JSON.stringify(value)
  if (typeof value === 'number' && (encoded.includes('.') || encoded.includes('e'))) {
    // The server hashes json.loads(request), whose float encoder uses two exponent digits.
    if (value !== 0 && Math.abs(value) < 0.0001) encoded = value.toExponential()
    encoded = encoded.replace(/e([+-])(\d)$/, 'e$10$2')
  }
  return encoded
}

export async function mutationMaterialFingerprint(document: Record<string, unknown>): Promise<string> {
  const material = { ...document }
  delete material.writer_lease_token
  delete material.writer_client_instance_id
  const encoded = new TextEncoder().encode(canonicalJson(JSON.parse(JSON.stringify(material))))
  const digest = await crypto.subtle.digest('SHA-256', encoded)
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')
}

export function sameDraftSemantics(left: Record<string, unknown>, right: Record<string, unknown>): boolean {
  const fields = ['answer', 'target_event_id', 'inline_confidence']
  return fields.every(key => Object.hasOwn(left, key) === Object.hasOwn(right, key)
    && (!Object.hasOwn(left, key) || canonicalJson(left[key]) === canonicalJson(right[key])))
}

export interface MutationReceiptIdentity {
  mutationKind: 'draft' | 'navigation'
  attemptId: number
  materialFingerprint: string
  captureOperationId?: string
  navigationToken?: string
}

export function matchesMutationRejection(error: unknown, identity: MutationReceiptIdentity): error is ApiError {
  if (!(error instanceof ApiError)) return false
  const fields = error.fields
  return fields[identity.mutationKind === 'draft' ? 'mutation_rejected' : 'navigation_rejected'] === true
    && fields.receipt_version === 2 && fields.mutation_kind === identity.mutationKind
    && fields.attempt_id === identity.attemptId && fields.material_fingerprint === identity.materialFingerprint
    && (identity.mutationKind === 'draft'
      ? typeof identity.captureOperationId === 'string' && fields.capture_operation_id === identity.captureOperationId
      : typeof identity.navigationToken === 'string' && fields.navigation_token === identity.navigationToken)
}
