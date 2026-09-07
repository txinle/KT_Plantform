import { describe, expect, it } from 'vitest'

import { teacherStandaloneQuestionBankRoutes } from './standalone-routes'

describe('standalone question-bank routes', () => {
  it('covers no-course bank and concept URLs without a course parameter', () => {
    expect(teacherStandaloneQuestionBankRoutes.map((route) => route.path)).toEqual([
      '/teacher/banks/',
      '/teacher/banks/:bankId/',
      '/teacher/banks/:bankId/import/',
      '/teacher/banks/:bankId/kc-import/',
      '/teacher/banks/:bankId/questions/:questionId/edit/',
      '/teacher/concepts/',
    ])
  })
})
