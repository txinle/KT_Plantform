export interface AdmissionCredentials {
  admission_token: string
  admission_action: 'start' | 'resume'
  session_id?: number
}

export interface StartIntent {
  client_instance_id: string
  navigation_id: string
  client_occurred_at: string
  client_monotonic_ms: string
  client_time_origin: string
  reason: string
}

export function readStartIntent(taskId: number): StartIntent | undefined {
  try {
    const value = JSON.parse(sessionStorage.getItem(`kt-assessment-start:${taskId}`) || 'null') as StartIntent | null
    if (value && typeof value.client_instance_id === 'string' && typeof value.navigation_id === 'string') return value
  } catch { /* private browsing */ }
}

export function writeStartIntent(taskId: number, value: StartIntent) {
  try { sessionStorage.setItem(`kt-assessment-start:${taskId}`, JSON.stringify(value)) } catch { /* private browsing */ }
}

export function clearStartIntent(taskId: number) {
  try { sessionStorage.removeItem(`kt-assessment-start:${taskId}`) } catch { /* private browsing */ }
}

function key(taskId: number) {
  return `kt-assessment-admission:${taskId}`
}

export function readAdmissionCredentials(taskId: number): AdmissionCredentials | undefined {
  try {
    const raw = sessionStorage.getItem(key(taskId))
    if (!raw) return undefined
    const value = JSON.parse(raw) as Partial<AdmissionCredentials>
    if (typeof value.admission_token !== 'string' || !['start', 'resume'].includes(value.admission_action || '')) return undefined
    return value as AdmissionCredentials
  } catch { return undefined }
}

export function writeAdmissionCredentials(taskId: number, credentials: AdmissionCredentials) {
  try { sessionStorage.setItem(key(taskId), JSON.stringify(credentials)) } catch { /* private browsing */ }
}

export function clearAdmissionCredentials(taskId: number) {
  try { sessionStorage.removeItem(key(taskId)) } catch { /* private browsing */ }
}

export function currentAdmissionCredentials(sessionId?: number): { taskId: number; credentials: AdmissionCredentials } | undefined {
  try {
    for (let index = 0; index < sessionStorage.length; index += 1) {
      const storageKey = sessionStorage.key(index)
      if (!storageKey?.startsWith('kt-assessment-admission:')) continue
      const taskId = Number(storageKey.slice('kt-assessment-admission:'.length))
      const credentials = readAdmissionCredentials(taskId)
      if (Number.isInteger(taskId) && credentials && (sessionId === undefined || credentials.session_id === sessionId)) return { taskId, credentials }
    }
  } catch { /* private browsing */ }
  return undefined
}
