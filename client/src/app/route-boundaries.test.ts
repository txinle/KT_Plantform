import { describe, expect, it } from 'vitest'
import routeCatalog from '../../../contracts/frontend-routes.json'

import { migratedRoutes } from './route-boundaries'
import { router } from './router'

describe('Vue route boundaries', () => {
  it('registers independent administrator bank and question editing routes', () => {
    for (const path of ['/manage/resources/courses/1/banks/2/', '/manage/resources/courses/1/banks/2/import/', '/manage/resources/courses/1/banks/2/questions/3/edit/', '/manage/resources/banks/2/', '/manage/resources/banks/2/questions/3/edit/']) {
      expect(router.resolve(path).name).not.toBe('not-found')
      expect(router.resolve(path).matched[0]?.props.default).toEqual({ active: 'bank' })
    }
  })
  it('registers every business route in the independent gateway page catalog', () => {
    const pages = new Set(routeCatalog.routes.map(route => `/${route.path}`.replace(/<int:\w+>/g, ':id')))
    const missing = router.getRoutes().map(route => route.path)
      .filter(path => !path.includes('pathMatch'))
      .filter(path => !pages.has(path.replace(/:\w+/g, ':id')))
    expect(missing).toEqual([])
  })

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
    expect(migratedRoutes.find((route) => route.path === '/teacher/courses/:courseId/data/')?.children?.map((route) => route.path)).toEqual([
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
