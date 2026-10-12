import { describe, expect, it, vi } from 'vitest'

import {
  createTeacherStandaloneQuestionBankApi,
  isStandaloneCanonicalRedirect,
} from './standalone-api'

describe('standalone question-bank API', () => {
  it('uses same-origin JSON endpoints for the no-course legacy URLs', async () => {
    const client = {
      get: vi.fn().mockResolvedValue({}),
      mutate: vi.fn().mockResolvedValue({}),
      mutateForm: vi.fn().mockResolvedValue({}),
    }
    const api = createTeacherStandaloneQuestionBankApi(client as never)
    const file = new File(['rows'], 'questions.csv', { type: 'text/csv' })

    await api.banks()
    await api.createBank({ name: '独立题库', description: '', visibility: 'private' })
    await api.detail(11)
    await api.updateVisibility(11, 'shared')
    await api.questionEditor(11, 31)
    await api.recheckQuestions(11)
    await api.previewQuestionImport(11, file)
    await api.confirmQuestionImport(11, 'preview-id')
    await api.previewKCImport(11, file)
    await api.confirmKCImport(11, 'kc-preview-id')
    await api.copySharedBank(17)
    await api.concepts()
    await api.detail(12, { destination: 'question_editor', questionId: 31 })

    expect(client.get).toHaveBeenNthCalledWith(1, '/api/v1/teacher/banks/')
    expect(client.mutate).toHaveBeenNthCalledWith(1, '/api/v1/teacher/banks/', {
      method: 'POST', body: { name: '独立题库', description: '', visibility: 'private' },
    })
    expect(client.get).toHaveBeenNthCalledWith(2, '/api/v1/teacher/banks/11/')
    expect(client.mutate).toHaveBeenNthCalledWith(2, '/api/v1/teacher/banks/11/visibility/', {
      method: 'PUT', body: { visibility: 'shared' },
    })
    expect(client.get).toHaveBeenNthCalledWith(3, '/api/v1/teacher/banks/11/questions/31/editor/')
    expect(client.mutate).toHaveBeenNthCalledWith(3, '/api/v1/teacher/banks/11/questions/recheck/', { method: 'POST' })
    expect(client.mutateForm).toHaveBeenNthCalledWith(1, '/api/v1/teacher/banks/11/imports/questions/preview/', expect.objectContaining({ method: 'POST' }))
    expect(client.mutate).toHaveBeenNthCalledWith(4, '/api/v1/teacher/banks/11/imports/questions/confirm/', {
      method: 'POST', body: { preview_id: 'preview-id' },
    })
    expect(client.mutateForm).toHaveBeenNthCalledWith(2, '/api/v1/teacher/banks/11/imports/kc/preview/', expect.objectContaining({ method: 'POST' }))
    expect(client.mutate).toHaveBeenNthCalledWith(5, '/api/v1/teacher/banks/11/imports/kc/confirm/', {
      method: 'POST', body: { preview_id: 'kc-preview-id' },
    })
    expect(client.mutate).toHaveBeenNthCalledWith(6, '/api/v1/teacher/banks/shared/17/copy/', { method: 'POST' })
    expect(client.get).toHaveBeenNthCalledWith(4, '/api/v1/teacher/concepts/')
    expect(client.get).toHaveBeenNthCalledWith(
      5,
      '/api/v1/teacher/banks/12/?destination=question_editor&question_id=31',
    )
  })

  it('recognizes a canonical redirect response from standalone endpoints', () => {
    expect(isStandaloneCanonicalRedirect({ redirect_url: '/teacher/courses/' })).toBe(true)
    expect(isStandaloneCanonicalRedirect({ banks: [] })).toBe(false)
  })
})
