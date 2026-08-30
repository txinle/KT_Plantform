import { describe, expect, it, vi } from 'vitest'

import { createTeacherQuestionBankApi } from './api'

describe('teacher question-bank API paths', () => {
  it('uses the scoped bank, question, and KC collection endpoints', async () => {
    const client = { get: vi.fn().mockResolvedValue({}) }
    const api = createTeacherQuestionBankApi(client as never)

    await api.list(7, { page: 2, query: '代数' })
    await api.courseConcepts(7, { page: 3, query: '函数' })
    await api.detail(7, 11)
    await api.questions(7, 11, {
      page: 2,
      query: 'x + 1',
      questionType: 'numeric',
      status: 'active',
    })
    await api.bankConcepts(7, 11, { page: 4, query: '概率' })

    expect(client.get).toHaveBeenNthCalledWith(
      1,
      '/api/v1/teacher/courses/7/banks/?page=2&q=%E4%BB%A3%E6%95%B0',
    )
    expect(client.get).toHaveBeenNthCalledWith(
      2,
      '/api/v1/teacher/courses/7/concepts/?page=3&q=%E5%87%BD%E6%95%B0',
    )
    expect(client.get).toHaveBeenNthCalledWith(
      3,
      '/api/v1/teacher/courses/7/banks/11/',
    )
    expect(client.get).toHaveBeenNthCalledWith(
      4,
      '/api/v1/teacher/courses/7/banks/11/questions/?page=2&q=x+%2B+1&question_type=numeric&status=active',
    )
    expect(client.get).toHaveBeenNthCalledWith(
      5,
      '/api/v1/teacher/courses/7/banks/11/concepts/?page=4&q=%E6%A6%82%E7%8E%87',
    )
  })
})
