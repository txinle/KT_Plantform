export interface AdmissionCredentials {
  admission_token: string
  admission_action: 'start' | 'resume'
  session_id?: number
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
