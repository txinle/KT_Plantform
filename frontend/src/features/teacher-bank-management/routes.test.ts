import { describe, expect, it } from 'vitest'

import { teacherBankManagementRoutes } from './routes'

describe('teacher bank-management routes', () => {
  it('keeps the remaining legacy course bank URLs inside the Vue module', () => {
    expect(teacherBankManagementRoutes.map((route) => route.path)).toEqual([
      '/teacher/courses/:courseId/banks/archived/',
      '/teacher/courses/:courseId/banks/shared/',
      '/teacher/courses/:courseId/banks/shared/:sourceCourseId/',
      '/teacher/courses/:courseId/banks/shared/:sourceCourseId/creators/:creatorId/',
      '/teacher/courses/:courseId/banks/:bankId/edit/',
      '/teacher/courses/:courseId/banks/:bankId/import/',
      '/teacher/courses/:courseId/banks/:bankId/kc-import/',
      '/teacher/courses/:courseId/banks/:bankId/questions/:questionId/edit/',
    ])
  })
})
