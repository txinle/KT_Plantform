import { describe, expect, it, vi } from 'vitest'

import { createTeacherBankManagementApi } from './api'

describe('teacher bank-management API paths', () => {
  it('uses JSON management endpoints for archive, sharing, editors, and imports', async () => {
    const client = {
      get: vi.fn().mockResolvedValue({}),
      mutate: vi.fn().mockResolvedValue({}),
      mutateForm: vi.fn().mockResolvedValue({}),
    }
    const api = createTeacherBankManagementApi(client as never)
    const questionFile = new File(['序号,题目\n1,题目'], 'questions.csv')
    const kcFile = new File(['题目序号,一级知识点,二级知识点,三级知识点\n1,KC1,,'], 'kcs.csv')

    await api.archivedBanks(7, 2)
    await api.restoreBank(7, 11)
    await api.deleteBank(7, 11)
    await api.sharedSources(7)
    await api.sharedCreators(7, 8)
    await api.sharedBanks(7, 8, 9)
    await api.copySharedBank(7, 8, 9, 11)
    await api.bankEditor(7, 11)
    await api.updateBank(7, 11, { name: '题库', description: '', visibility: 'private' })
    await api.questionEditor(7, 11, 31)
    await api.updateQuestion(7, 11, 31, { text: '题目' })
    await api.recheckQuestions(7, 11)
    await api.previewQuestionImport(7, 11, questionFile)
    await api.confirmQuestionImport(7, 11, 'preview-1')
    await api.previewKCImport(7, 11, kcFile)
    await api.confirmKCImport(7, 11, 'batch-1')
    await api.updateCourseConcept(7, 41, { name: 'KC', description: '' })
    await api.deleteCourseConcept(7, 41)
    await api.setCourseConceptStatus(7, 41, 'archived')
    await api.setCourseConceptStatus(7, 41, 'active')

    expect(client.get).toHaveBeenNthCalledWith(1, '/api/v1/teacher/courses/7/banks/archived/?page=2')
    expect(client.get).toHaveBeenNthCalledWith(2, '/api/v1/teacher/courses/7/banks/shared/')
    expect(client.get).toHaveBeenNthCalledWith(3, '/api/v1/teacher/courses/7/banks/shared/8/')
    expect(client.get).toHaveBeenNthCalledWith(4, '/api/v1/teacher/courses/7/banks/shared/8/creators/9/')
    expect(client.get).toHaveBeenNthCalledWith(5, '/api/v1/teacher/courses/7/banks/11/editor/')
    expect(client.get).toHaveBeenNthCalledWith(6, '/api/v1/teacher/courses/7/banks/11/questions/31/editor/')
    expect(client.mutate).toHaveBeenCalledWith('/api/v1/teacher/courses/7/banks/11/restore/', { method: 'POST' })
    expect(client.mutate).toHaveBeenCalledWith('/api/v1/teacher/courses/7/banks/11/delete/', { method: 'DELETE', body: { confirmation: 'delete' } })
    expect(client.mutate).toHaveBeenCalledWith('/api/v1/teacher/courses/7/banks/shared/8/creators/9/banks/11/copy/', { method: 'POST' })
    expect(client.mutate).toHaveBeenCalledWith('/api/v1/teacher/courses/7/banks/11/editor/', { method: 'PUT', body: { name: '题库', description: '', visibility: 'private' } })
    expect(client.mutate).toHaveBeenCalledWith('/api/v1/teacher/courses/7/banks/11/questions/31/editor/', { method: 'PUT', body: { text: '题目' } })
    expect(client.mutate).toHaveBeenCalledWith('/api/v1/teacher/courses/7/banks/11/questions/recheck/', { method: 'POST' })
    expect(client.mutate).toHaveBeenCalledWith('/api/v1/teacher/courses/7/banks/11/imports/questions/confirm/', { method: 'POST', body: { preview_id: 'preview-1' } })
    expect(client.mutate).toHaveBeenCalledWith('/api/v1/teacher/courses/7/banks/11/imports/kc/confirm/', { method: 'POST', body: { batch_id: 'batch-1' } })
    expect(client.mutate).toHaveBeenCalledWith('/api/v1/teacher/courses/7/concepts/41/', { method: 'PUT', body: { name: 'KC', description: '' } })
    expect(client.mutate).toHaveBeenCalledWith('/api/v1/teacher/courses/7/concepts/41/', { method: 'DELETE' })
    expect(client.mutate).toHaveBeenCalledWith('/api/v1/teacher/courses/7/concepts/41/', { method: 'PATCH', body: { status: 'archived' } })
    expect(client.mutate).toHaveBeenCalledWith('/api/v1/teacher/courses/7/concepts/41/', { method: 'PATCH', body: { status: 'active' } })
    expect(client.mutateForm).toHaveBeenCalledWith('/api/v1/teacher/courses/7/banks/11/imports/questions/preview/', { method: 'POST', body: expect.any(FormData) })
    expect(client.mutateForm).toHaveBeenCalledWith('/api/v1/teacher/courses/7/banks/11/imports/kc/preview/', { method: 'POST', body: expect.any(FormData) })
  })
})
