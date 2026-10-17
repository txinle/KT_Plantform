import { describe, expect, it } from 'vitest'
import { canNavigateStudentCourse } from './student-navigation'
import type { BootstrapContext } from './bootstrap'

describe('server-projected student navigation', () => {
  it('keeps document navigation unless the server explicitly allows the migrated target', () => {
    const context = { client_navigation: { student_courses: true, course_ids: ['7'] } } as BootstrapContext
    expect(canNavigateStudentCourse(undefined, '/student/')).toBe(false)
    expect(canNavigateStudentCourse(context, '/student/')).toBe(true)
    expect(canNavigateStudentCourse(context, '/student/courses/7/?tab=tasks')).toBe(true)
    expect(canNavigateStudentCourse(context, '/student/courses/8/')).toBe(false)
    for (const path of ['/teacher/', '/admin/', '/', '//evil.test/student/', 'https://evil.test/student/', '/student/sessions/7/questions/11/', '/student/courses/7/unmigrated/']) {
      expect(canNavigateStudentCourse(context, path), path).toBe(false)
    }
    expect(canNavigateStudentCourse({ ...context, client_navigation: { student_courses: false, course_ids: ['*'] } }, '/student/')).toBe(false)
  })
})
