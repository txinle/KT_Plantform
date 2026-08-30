export interface BootstrapContext {
  module: string
  csrf_token: string
  user: {
    id: number
    role: string
    display_name: string
    username: string
    student_number: string | null
  }
  course_id: number | null
}

let cachedContext: BootstrapContext | undefined

export function getBootstrapContext(): BootstrapContext {
  if (cachedContext) return cachedContext
  const element = document.getElementById('kt-bootstrap')
  if (!element?.textContent) throw new Error('Vue bootstrap context is missing.')
  cachedContext = JSON.parse(element.textContent) as BootstrapContext
  return cachedContext
}
