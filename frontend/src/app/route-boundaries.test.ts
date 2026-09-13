import { describe, expect, it } from 'vitest'

import { migratedRoutes } from './route-boundaries'
import { router } from './router'

describe('Vue route boundaries', () => {
  it('registers only the migrated course, question-bank, class, experiment, student, and assessment boundaries', () => {
    expect(migratedRoutes.map((route) => route.path)).toEqual(expect.arrayContaining([
      '/teacher/courses/',
      '/teacher/courses/create/',
      '/teacher/courses/:courseId/',
      '/teacher/courses/:courseId/settings/',
      '/teacher/courses/:courseId/banks/',
      '/teacher/courses/:courseId/classes/',
      '/teacher/courses/:courseId/experiments/',
      '/teacher/courses/:courseId/data/',
      '/student/',
      '/student/courses/:courseId/',
      '/student/sessions/:sessionId/questions/:assignmentId/',
      '/student/sessions/:sessionId/complete/',
      '/teacher/accounts/',
    ]))
    expect(migratedRoutes.find((route) => route.path === '/teacher/courses/:courseId/banks/')?.children?.map((route) => route.path)).toEqual([
      '',
      ':bankId/',
    ])
    expect(migratedRoutes.find((route) => route.path === '/teacher/courses/:courseId/classes/')?.children?.map((route) => route.path)).toEqual([
      '',
      ':classId/',
    ])
    expect(migratedRoutes.find((route) => route.path === '/teacher/courses/:courseId/experiments/')?.children?.map((route) => route.path)).toEqual([
      '',
      'create/',
      ':experimentId/',
      ':experimentId/edit/',
      ':experimentId/class-tasks/:taskId/grading/',
      ':experimentId/publish-plan/:planId/step/:step/',
    ])
    expect(migratedRoutes.find((route) => route.path.endsWith('/data/'))?.children?.map((route) => route.path)).toEqual([
      '',
      'export/',
      'exports/',
      'quality/',
      'exports/history/',
      'tables/',
      'advanced/',
    ])
    expect(migratedRoutes.find((route) => route.path === '/student/courses/:courseId/')?.children?.map((route) => route.path)).toEqual([
      '',
      'learning/',
      'learning/experiments/:experimentId/',
      'learning/experiments/:experimentId/questions/:questionId/',
    ])
  })

  it('does not register a catch-all route', () => {
    expect(migratedRoutes.some((route) => route.path.includes('pathMatch'))).toBe(false)
  })

  it('registers password change as an independent application route', () => {
    expect(router.resolve('/password/change/').name).toBe('password-change')
  })
})
