import { describe, expect, it } from 'vitest'

import { migratedRoutes } from './route-boundaries'

describe('Vue route boundaries', () => {
  it('registers only the migrated teacher question-bank, class, experiment, student, and assessment boundaries', () => {
    expect(migratedRoutes.map((route) => route.path)).toEqual([
      '/teacher/courses/:courseId/banks/',
      '/teacher/courses/:courseId/classes/',
      '/teacher/courses/:courseId/experiments/',
      '/teacher/courses/:courseId/data/',
      '/student/',
      '/student/courses/:courseId/',
      '/student/sessions/:sessionId/questions/:assignmentId/',
    ])
    expect(migratedRoutes.find((route) => route.path.endsWith('/banks/'))?.children?.map((route) => route.path)).toEqual([
      '',
      ':bankId/',
    ])
    expect(migratedRoutes.find((route) => route.path.endsWith('/classes/'))?.children?.map((route) => route.path)).toEqual([
      '',
      ':classId/',
    ])
    expect(migratedRoutes.find((route) => route.path.endsWith('/experiments/'))?.children?.map((route) => route.path)).toEqual([
      '',
      'create/',
      ':experimentId/',
      ':experimentId/edit/',
      ':experimentId/publish-plan/:planId/step/:step/',
    ])
    expect(migratedRoutes.find((route) => route.path.endsWith('/data/'))?.children?.map((route) => route.path)).toEqual([
      '',
      'export/',
      'exports/',
      'quality/',
      'exports/history/',
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
})
