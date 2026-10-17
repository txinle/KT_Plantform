import type { BootstrapContext } from './bootstrap'
import type { DeepReadonly } from 'vue'

export function canNavigateStudentCourse(context: DeepReadonly<BootstrapContext> | null | undefined, path: string): boolean {
  const navigation = context?.client_navigation
  if (!navigation?.student_courses) return false
  if (!path.startsWith('/') || path.startsWith('//')) return false
  const target = new URL(path, window.location.origin)
  if (target.origin !== window.location.origin) return false
  const pathname = target.pathname
  if (pathname === '/student/') return true
  const match = /^\/student\/courses\/(\d+)\/$/.exec(pathname)
  return Boolean(match && (navigation.course_ids.includes('*') || navigation.course_ids.includes(match[1]!)))
}
